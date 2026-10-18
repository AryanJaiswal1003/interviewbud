"""Runtime behaviour against a scripted LLM stream (no network): listen mode, tool calls, speculative cancel."""

import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import delete, select

from app import llm, session
from app.agent.memory import new_memory
from app.db import Candidate, Interview, Problem, ProblemTest, SessionLocal, Turn, init_db


def _content(text):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=None))])


def _tool(index, cid, name, args):
    fn = NS(name=name, arguments=args)
    return NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=index, id=cid, function=fn)]))])


class FakeStream:
    def __init__(self, chunks, delay=0.0):
        self.chunks, self.delay = chunks, delay

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield c


class FakeLLM:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        script = self.scripts.pop(0) if self.scripts else [_content("Okay.")]
        delay = 0.0
        if isinstance(script, tuple):
            script, delay = script
        return FakeStream(script, delay)


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, t):
        self.sent.append(json.loads(t))

    async def send_bytes(self, b):
        pass

    def of(self, kind):
        return [m for m in self.sent if m["type"] == kind]


@pytest.fixture
async def rt(monkeypatch):
    await init_db()
    async with SessionLocal() as db:
        problem = await db.get(Problem, 1)  # two-sum, ingested from the real dataset
        assert problem, "run the dataset ingest first"
        cand = Candidate(name="Test Candidate", profile={"name": "Test", "headline": "Engineer"})
        db.add(cand)
        await db.flush()
        iv = Interview(candidate_id=cand.id, role="Backend Engineer", status="ready", problem_id=1,
                       plan={"opening_line": "Hi!", "phase_budgets": [{"phase": "coding", "minutes": 20, "goals": []}],
                             "coding_focus": ["hash map"]},
                       memory=new_memory())
        db.add(iv)
        await db.commit()
        iid, cid = iv.id, cand.id
    monkeypatch.setattr(session.get_settings(), "fish_api_key", "")  # text-only: no TTS
    monkeypatch.setattr(session.wm, "extract", _no_extract)  # memory extraction calls the LLM; covered elsewhere
    r = await session.get_runtime(iid)
    r.ws = FakeWS()
    r.started_at = 0.0
    yield r
    await asyncio.gather(*list(r.bg), return_exceptions=True)
    session.RUNTIMES.pop(iid, None)
    async with SessionLocal() as db:
        await db.execute(delete(ProblemTest).where(ProblemTest.interview_id == iid))
        await db.execute(delete(Candidate).where(Candidate.id == cid))
        await db.commit()


async def _no_extract(*a, **k):
    raise RuntimeError("extraction disabled in tests")


async def wait_idle(rt):
    for _ in range(200):
        if rt.reply is None or (rt.reply.task and rt.reply.task.done()):
            await asyncio.sleep(0.01)
            if rt.reply is None:
                return
        await asyncio.sleep(0.02)


async def test_text_turn_records_both_sides(rt, monkeypatch):
    fake = FakeLLM([[_content("Nice, "), _content("tell me more about that queue.")]])
    monkeypatch.setattr(llm, "client", lambda: fake)
    await rt.handle_text("We moved fan-out into SQS.")
    await wait_idle(rt)
    await asyncio.gather(*list(rt.bg), return_exceptions=True)
    end = rt.ws.of("agent_turn_end")[-1]
    assert end["text"] == "Nice, tell me more about that queue."
    assert rt.history[-2]["content"] == "We moved fan-out into SQS."
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Turn).where(Turn.interview_id == rt.id).order_by(Turn.seq))).all()
    assert [t.speaker for t in rows] == ["candidate", "interviewer"]
    # live state is the trailing system message
    assert fake.calls[0]["messages"][-1]["content"].startswith("LIVE STATE")


async def test_listen_mode_stays_silent(rt, monkeypatch):
    monkeypatch.setattr(llm, "client", lambda: FakeLLM([[_content("<lis"), _content("ten/>")]]))
    await rt.handle_text("so I'm thinking we could maybe")
    await wait_idle(rt)
    end = rt.ws.of("agent_turn_end")[-1]
    assert end["listen"] is True and end["text"] == ""
    assert not rt.ws.of("agent_text_delta")
    assert rt.history[-1]["content"] == "<listen/>"


async def test_present_problem_and_injected_test(rt, monkeypatch):
    fake = FakeLLM([
        [_tool(0, "c1", "present_problem", "{}")],
        [_content("Here's the problem. "), _tool(0, "c2", "add_test_case",
                                                 json.dumps({"input": "nums = [5,5], target = 10", "why": "dupes"}))],
        [_content("I added a test with duplicates.")],
    ])
    monkeypatch.setattr(llm, "client", lambda: fake)
    await rt.handle_text("Ready for coding.")
    await wait_idle(rt)
    assert rt.memory["phase"] == "coding" and rt.memory["problem_presented"]
    problems = rt.ws.of("problem")
    assert problems and any(t["kind"] == "injected" and t["expected"] == "[0, 1]"
                            for t in problems[-1]["problem"]["visible_tests"])
    # tool results were fed back so the model could continue speaking
    assert any(m["role"] == "tool" for m in fake.calls[-1]["messages"])
    assert rt.ws.of("agent_turn_end")[-1]["text"].endswith("I added a test with duplicates.")


async def test_speculative_reply_is_cancelled_when_candidate_resumes(rt, monkeypatch):
    slow = ([_content("Got it. "), _content("So why did you pick that?")], 0.3)
    fake = FakeLLM([slow, [_content("Ah, the whole picture now.")]])
    monkeypatch.setattr(llm, "client", lambda: fake)
    rt.pending = ["We picked Postgres"]
    await rt._start_reply("voice")
    await asyncio.sleep(0.05)
    await rt.on_speech_start()  # candidate keeps talking before any audio played
    await asyncio.sleep(0.05)
    assert rt.pending == ["We picked Postgres"]  # their words are kept, not lost
    rt.pending.append("because of the JSONB support.")
    await rt._start_reply("voice")
    await wait_idle(rt)
    assert rt.history[-2]["content"] == "We picked Postgres because of the JSONB support."
    assert rt.ws.of("agent_turn_end")[-1]["text"] == "Ah, the whole picture now."


async def test_candidate_code_run_reaches_agent(rt, monkeypatch):
    monkeypatch.setattr(llm, "client", lambda: FakeLLM([[_content("<listen/>")]]))
    rt.memory["problem_presented"] = True
    code = ("class Solution:\n    def twoSum(self, nums, target):\n        d = {}\n"
            "        for i, x in enumerate(nums):\n            if target - x in d:\n"
            "                return [d[target - x], i]\n            d[x] = i\n")
    view = await rt.run_code(code, "submit")
    assert view["passed"] == view["total"] > 10
    assert rt.ws.of("code_result")[-1]["run"]["summary"]["passed"] == view["passed"]
    assert "hidden" in view["by_kind"] and "hard" in view["by_kind"]


async def test_goodbye_ends_the_interview_right_away(rt, monkeypatch):
    """Regression: _end used to wait for every background task (slow memory extraction) before telling the
    browser, so the room stayed open for minutes after the goodbye."""
    async def slow_extract(*a, **k):
        await asyncio.sleep(30)
    finalized = asyncio.Event()

    async def fake_finalize(iid):
        finalized.set()
    monkeypatch.setattr(session.wm, "extract", slow_extract)
    monkeypatch.setattr(session.scorecard, "finalize", fake_finalize)
    monkeypatch.setattr(llm, "client", lambda: FakeLLM([[
        _content("Okay, thanks for your time. A person will review this. Take care."),
        _tool(0, "w1", "advance_phase", '{"to": "wrap_up", "reason": "ending"}'),
        _tool(1, "e1", "end_interview", '{"reason": "candidate asked to end"}')]]))
    t0 = asyncio.get_running_loop().time()
    await rt.handle_text("I think that's enough, let's end here.")
    for _ in range(100):
        if rt.ws.of("ended"):
            break
        await asyncio.sleep(0.02)
    assert rt.ws.of("ended"), "browser was never told the interview ended"
    assert asyncio.get_running_loop().time() - t0 < 2.0
    assert rt.id not in session.RUNTIMES
    async with SessionLocal() as db:
        iv = await db.get(Interview, rt.id)
        assert iv.status == "completed"
        assert iv.memory["phase"] == "wrap_up" and iv.memory["phase_history"][-1]["phase"] == "wrap_up"
    assert not finalized.is_set()  # scoring waits for the in-flight extraction, in the background
    for task in list(rt.bg):
        task.cancel()

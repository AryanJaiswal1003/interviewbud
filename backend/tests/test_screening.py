"""Screening call, interviewer workspace tools and the About-you question queue, against scripted model output.

The capture rules matter most: an answer is kept only when its quote is really in the candidate's own words, option
answers must be one of the form's options, and the screener's own words never count as the candidate's."""

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app import auth, llm, session
from app.agent.memory import new_memory
from app.db import (Candidate, Interview, Opportunity, PrepQuestion, PrepSession, ProfileFact, SessionLocal, Turn,
                    User)
from app.main import app
from app.prep import screening, service
from app.prep.schemas import CoverageResult, NextQuestion, RequirementCoverage
from tests.test_session import FakeLLM, FakeWS, _content, _tool, wait_idle

EXTRACTION = {"title": "Backend Engineer", "company": "Example Co", "location": "Remote",
              "requirements": [{"id": "R1", "text": "5+ years of Python", "quote": "5+ years of Python",
                                "must_have": True, "category": "technical", "verified": True},
                               {"id": "R2", "text": "Kafka", "quote": "Kafka", "must_have": False,
                                "category": "technical", "verified": True}],
              "application_questions": []}
ITEMS = [{"id": "R1", "kind": "requirement", "ask": "5+ years of Python", "options": [], "required": True},
         {"id": "A:auth", "kind": "application", "ask": "Are you authorized to work in the US?",
          "options": ["Yes", "No"], "required": True}]


@pytest.fixture(autouse=True)
async def _clean(monkeypatch):
    for t in (auth.login_throttle, auth.signup_throttle, auth.create_throttle):
        t.hits.clear()
    monkeypatch.setattr(service, "spawn", lambda coro: coro.close())
    yield
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.display_name.like("Jordan screentest-%")))
        await db.commit()


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def make_prep(c) -> tuple[uuid.UUID, uuid.UUID]:
    uid = uuid.UUID((await c.post("/api/auth/start", json={
        "name": f"Jordan screentest-{uuid.uuid4().hex[:6]}"})).json()["id"])
    async with SessionLocal() as db:
        opp = Opportunity(owner_id=uid, source_text="x", fetch_status="ok", extraction=EXTRACTION)
        db.add(opp)
        await db.flush()
        prep = PrepSession(owner_id=uid, opportunity_id=opp.id, status="context")
        db.add(prep)
        await db.commit()
        return uid, prep.id


def test_captures_need_the_candidates_own_words_and_real_options():
    items = {it["id"]: it for it in ITEMS}
    said = "I've been writing Python for about six years. And yes, I'm a US citizen."
    ok = screening.Capture(item_id="R1", answer="About six years of Python", quote="writing Python for about six years")
    made_up = screening.Capture(item_id="R1", answer="Ten years", quote="I have ten years of Python")
    option = screening.Capture(item_id="A:auth", answer="yes", quote="yes, I'm a US citizen")
    not_option = screening.Capture(item_id="A:auth", answer="Probably", quote="yes, I'm a US citizen")
    unknown = screening.Capture(item_id="R9", answer="x", quote="writing Python for about six years")
    assert screening._valid(ok, items, said)
    assert not screening._valid(made_up, items, said)
    assert screening._valid(option, items, said) and option.answer == "Yes"  # normalised to the form's option
    assert not screening._valid(not_option, items, said)
    assert not screening._valid(unknown, items, said)


async def test_screening_call_starts_instantly_and_finalizes_into_facts(monkeypatch):
    async with client() as c:
        uid, pid = await make_prep(c)
        r = await c.post(f"/api/prep/{pid}/screening")
        assert r.status_code == 200, r.text
        iid = uuid.UUID(r.json()["interview_id"])
        iv = (await c.get(f"/api/interviews/{iid}")).json()
        assert iv["status"] == "ready" and iv["kind"] == "screening"  # no planning wait
        assert iv["plan"]["opening_line"].startswith("Hey Jordan")
        assert [i["id"] for i in iv["plan"]["items"]] == ["R1", "R2"]  # gaps, must-have first

    # The live call: screening prompt and tools, live checklist, capture after each answer.
    monkeypatch.setattr(session.get_settings(), "fish_api_key", "")
    rt = await session.get_runtime(iid)
    rt.ws = FakeWS()
    rt.started_at = 0.0
    assert "recruiter-style screener" in rt.system
    fake = FakeLLM([[_content("Oh nice, six years. "), _content("What are you working on these days?")]])
    monkeypatch.setattr(llm, "client", lambda: fake)

    async def fake_structured(schema, system, user, **kw):
        assert schema is screening.LiveCapture
        return screening.LiveCapture(captures=[
            screening.Capture(item_id="R1", answer="About six years", quote="Python for about six years"),
            screening.Capture(item_id="R2", answer="Lots of Kafka", quote="I love Kafka")])  # never said: dropped
    monkeypatch.setattr(llm, "structured", fake_structured)
    await rt.handle_text("I've done Python for about six years, mostly backend.")
    await wait_idle(rt)
    await asyncio.gather(*list(rt.bg), return_exceptions=True)
    call = fake.calls[0]
    assert {t["function"]["name"] for t in call["tools"]} == {"advance_phase", "end_interview"}
    assert "STILL TO COVER" in call["messages"][-1]["content"]
    assert rt.memory["captured"] == {"R1": {"answer": "About six years", "quote": "Python for about six years"}}
    assert rt.ws.of("captured")[-1]["items"][0]["captured"]["answer"] == "About six years"
    assert "5+ years of Python" not in rt._screening_state().split("ALREADY CAPTURED")[0].split("STILL TO COVER")[1]
    state = rt._screening_state()
    assert "YOU ALREADY ASKED" in state and "What are you working on these days?" in state  # no repeats
    rt.history.append({"role": "user", "content": "No questions from me, that's everything.", "seq": 99})
    assert "wrap up NOW" in rt._screening_state()
    session.RUNTIMES.pop(iid, None)

    # After the call: the final pass over the whole transcript; only quotes that are really theirs survive.
    async with SessionLocal() as db:
        iv = await db.get(Interview, iid)
        iv.status, iv.memory = "completed", rt.memory
        await db.commit()

    async def final_structured(schema, system, user, **kw):
        assert schema is screening.FinalCapture and "INTERVIEWER: Oh nice" in user
        return screening.FinalCapture(
            captures=[screening.Capture(item_id="R2", answer="Used Kafka", quote="Oh nice, six years")],  # interviewer
            extra_facts=[screening.ExtraFact(topic="Backend focus", value="Mostly backend work",
                                             quote="mostly backend"),
                         screening.ExtraFact(topic="Invented", value="Led a team of 40", quote="I led forty people")])
    monkeypatch.setattr(llm, "structured", final_structured)
    handover = []

    async def fake_next(prep_id, sid):
        handover.append((prep_id, sid))
    monkeypatch.setattr(screening, "_next_round", fake_next)
    await screening.finalize(iid)
    async with SessionLocal() as db:
        iv = await db.get(Interview, iid)
        facts = (await db.scalars(select(ProfileFact).where(ProfileFact.user_id == uid))).all()
    assert iv.status == "scored" and set(iv.scorecard["captured"]) == {"R1"}
    assert sorted((f.topic, f.status, f.source) for f in facts) == [
        ("5+ years of Python", "confirmed", "screening"), ("Backend focus", "confirmed", "screening")]
    assert handover == [(pid, iid)]  # then on to the technical round


async def test_workspace_tools_highlight_and_note(monkeypatch):
    async with SessionLocal() as db:
        cand = Candidate(name="T", profile={"name": "T"})
        db.add(cand)
        await db.flush()
        iv = Interview(candidate_id=cand.id, role="Backend", status="ready", plan={"opening_line": "Hi"},
                       memory=new_memory())
        db.add(iv)
        await db.commit()
        iid = iv.id
    monkeypatch.setattr(session.get_settings(), "fish_api_key", "")
    rt = await session.get_runtime(iid)
    rt.ws = FakeWS()
    rt.workspace["code"] = "a = 1\nb = a[0]\nprint(b)\n"
    monkeypatch.setattr(llm, "client", lambda: FakeLLM([[
        _content("Ah, line two: that's an index into an int. "),
        _tool(0, "h", "highlight_code", json.dumps({"start_line": 2, "end_line": 99, "note": "a is an int"})),
        _tool(1, "n", "write_note", json.dumps({"text": "Edge cases: empty list, duplicates"}))]]))
    monkeypatch.setattr(session.wm, "extract", lambda *a, **k: asyncio.sleep(0))
    await rt.handle_text("I get a TypeError, what does that mean?")
    await wait_idle(rt)
    await asyncio.gather(*list(rt.bg), return_exceptions=True)
    acts = {m["name"]: m["args"] for m in rt.ws.of("ui_action")}
    assert acts["highlight_code"] == {"start_line": 2, "end_line": 3, "note": "a is an int"}  # clamped to the code
    assert acts["write_note"]["text"] == "Alex: Edge cases: empty list, duplicates"
    assert rt.workspace["notes"].endswith("Alex: Edge cases: empty list, duplicates")
    session.RUNTIMES.pop(iid, None)
    async with SessionLocal() as db:
        await db.execute(delete(Candidate).where(Candidate.id == cand.id))
        await db.commit()


async def test_about_you_questions_are_queued_short_and_tappable(monkeypatch):
    async with client() as c:
        uid, pid = await make_prep(c)

        async def fake_structured(schema, system, user, **kw):
            assert schema is CoverageResult
            return CoverageResult(coverage=[RequirementCoverage(requirement_id="R1", status="unknown", fact_ids=[],
                                                                note="")],
                                  next_questions=[
                                      NextQuestion(requirement_id="R1", question="How many years of Python?",
                                                   why="It's a must-have.", quick_replies=["Under 2", "2-4", "5+"]),
                                      NextQuestion(requirement_id="R2", question="Used Kafka?", why="Nice to have.",
                                                   quick_replies=["Yes", "No"]),
                                      NextQuestion(requirement_id="R9", question="Bogus?", why="", quick_replies=[])])
        monkeypatch.setattr(llm, "structured", fake_structured)
        async with SessionLocal() as db:
            await service.refresh_coverage(db, await db.get(PrepSession, pid))
        d = (await c.get(f"/api/prep/{pid}")).json()
        assert [(q["question"], q["status"]) for q in d["questions"]] == [("How many years of Python?", "open")]
        assert d["questions"][0]["quick_replies"] == ["Under 2", "2-4", "5+"] and d["queued_questions"] == 1
        qid = d["questions"][0]["id"]
        assert (await c.post(f"/api/prep/{pid}/questions/{qid}/answer", json={"answer": "5+"})).status_code == 200
        d = (await c.get(f"/api/prep/{pid}")).json()
        nxt = [q for q in d["questions"] if q["status"] == "open"]
        assert [q["question"] for q in nxt] == ["Used Kafka?"]  # the next one is there instantly
        async with SessionLocal() as db:
            rows = (await db.scalars(select(PrepQuestion).where(PrepQuestion.prep_id == pid))).all()
        assert sorted(q.status for q in rows) == ["answered", "open"]


def test_screening_knows_when_the_candidate_is_done():
    from app.voice.turns import sounds_done
    assert sounds_done("No questions from me, thanks. That's everything!")
    assert sounds_done("I think that's all") and sounds_done("we can wrap up")
    assert not sounds_done("Is that all you need?") and not sounds_done("I have a question about the team")

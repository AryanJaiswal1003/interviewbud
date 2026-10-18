"""Live interview runtime: one per interview, transport-agnostic (browser WebSocket today; a meeting bot could
attach the same way).

Latency-critical path per candidate turn:
  utterance WAV -> [STT  ||  pre-open Fish TTS socket] -> end-of-turn gate -> LLM stream -> chunker -> TTS -> PCM out
Everything else (memory extraction, persistence, scoring) runs off that path.
"""

import asyncio
import re
import json
import logging
import struct
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy import select

from . import llm
from .agent import memory as wm
from .agent.prompts import FIRE_AND_FORGET, TOOLS, system_prompt
from .coding import service as coding
from .config import get_settings
from .db import Interview, Problem, SessionLocal, Turn, User, WorkspaceSnapshot
from .evaluation import scorecard
from .learning import playbook
from .voice import stt, turns

LISTEN_TAG = re.compile(r"<\s*/?\s*listen\s*/?\s*>", re.I)
from .voice.chunker import SentenceChunker
from .voice.tts import LocalTTSStream, TTSStream, new_stream, speakable, strip_tags

log = logging.getLogger(__name__)

HISTORY_TURNS = 40
SILENCE_CHECK_S = 150


class Sink:
    """Receives the interviewer's caption text for a typed (AG-UI) message."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def put(self, delta: str) -> None:
        await self.queue.put(delta)

    async def close(self) -> None:
        await self.queue.put(None)


class Reply:
    def __init__(self, turn_id: int, candidate_text: str, channel: str):
        self.turn_id = turn_id
        self.candidate_text = candidate_text
        self.channel = channel
        self.task: asyncio.Task | None = None
        self.tts: TTSStream | LocalTTSStream | None = None
        self.audio_started = False
        self.spoken: list[str] = []
        self.actions: list[str] = []
        self.metrics: dict[str, Any] = {}


_TERM = re.compile(r"\b(?:[A-Z][a-zA-Z0-9+#.]*[A-Z0-9][a-zA-Z0-9+#.]*|[A-Z][a-z]+[A-Z][a-zA-Z]*|[a-z]+[A-Z][A-Za-z]*"
                   r"|[A-Za-z]+\.(?:js|ts|io))\b")


def speech_vocabulary(profile: dict, plan: dict, focus: str) -> str:
    """Tech terms from the resume/profile, plan and job (FastAPI, PostgreSQL, TypeScript...) for Whisper's prompt."""
    text = " ".join([json.dumps(profile, ensure_ascii=False), json.dumps(plan.get("technical_topics", [])), focus or ""])
    seen: dict[str, None] = {}
    for key in ("skills", "primary_languages", "domains"):  # explicit lists: take them whole (React, Kafka...)
        for s in profile.get(key) or []:
            if isinstance(s, str) and 2 <= len(s) <= 30:
                seen.setdefault(s.strip(), None)
    for m in _TERM.findall(text):
        if 3 <= len(m) <= 30 and m.lower() not in ("the", "and", "you", "json", "http", "https"):
            seen.setdefault(m, None)
    return "Glossary: " + ", ".join(list(seen)[:40]) + "." if seen else ""


class SessionRuntime:
    def __init__(self, interview: Interview, problem: Problem | None, history: list[dict], workspace: dict,
                 interviewer_name: str = "Alex"):
        s = get_settings()
        self.owner_id = interview.owner_id
        self.interviewer_name = interviewer_name
        self.id: UUID = interview.id
        self.role = interview.role
        self.duration_min = interview.duration_min
        self.plan: dict = interview.plan
        self.profile: dict = interview.candidate.profile
        self.memory: dict = interview.memory or wm.new_memory()
        self.voice_id = interview.voice_id or s.fish_voice_id
        self.voice_enabled = s.tts_configured
        self.problem = problem
        self.history = history
        self.seq = max((h.get("seq", 0) for h in history), default=0)
        self.workspace = workspace
        self.last_run: dict | None = None
        self.started_at = interview.started_at.timestamp() if interview.started_at else None
        self.ended = interview.status in ("completed", "scored")
        self.system = system_prompt(self.role, self.plan, self.profile, interviewer_name)
        self.vocab = speech_vocabulary(self.profile, self.plan, interview.focus)
        self.ws: WebSocket | None = None
        self.ws_lock = asyncio.Lock()
        self.reply: Reply | None = None
        self.turn_counter = 0
        self.pending: list[str] = []
        self.pending_speech_ms = 0.0
        self.pending_received_at = 0.0
        self.hold_task: asyncio.Task | None = None
        self.prepared_tts: TTSStream | LocalTTSStream | None = None
        self.candidate_speaking = False
        self.last_activity = time.time()
        self.last_proactive = 0.0
        self.code_changed_at = time.time()
        self.bg: set[asyncio.Task] = set()
        self.watch_task: asyncio.Task | None = None
        self.extract_lock = asyncio.Lock()
        self.code_lock = asyncio.Lock()  # one sandbox run per interview at a time
        self.chat_dep = s.azure_openai_chat_deployment
        self.chat_effort = s.azure_openai_chat_reasoning_effort

    # ---------------- transport ----------------
    async def attach(self, ws: WebSocket) -> None:
        if self.ws is not None and self.ws is not ws:
            try:
                # 4000 tells the older page not to reconnect (else two tabs would keep replacing each other).
                await self.ws.close(code=4000, reason="replaced by a newer connection")
            except Exception:
                pass
        self.ws = ws
        if self.watch_task is None:
            self.watch_task = asyncio.create_task(self._watch_silence())
        await self.send_state()
        if self.problem and self.memory.get("problem_presented"):
            await self.send({"type": "problem", "problem": await self.problem_view()})
        await self.send({"type": "history", "turns": [
            {"seq": h["seq"], "speaker": "interviewer" if h["role"] == "assistant" else "candidate",
             "text": strip_tags(h["content"])}
            for h in self.history if not h["content"].startswith("[event]")
        ]})

    def detach(self, ws: WebSocket) -> None:
        if self.ws is ws:
            self.ws = None

    async def send(self, msg: dict) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            async with self.ws_lock:
                await ws.send_text(json.dumps(msg, default=str))
        except Exception:
            self.ws = None

    async def send_audio(self, turn_id: int, pcm: bytes) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            async with self.ws_lock:
                await ws.send_bytes(struct.pack("<I", turn_id) + pcm)
        except Exception:
            self.ws = None

    def spawn(self, coro: Awaitable) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.bg.add(t)
        t.add_done_callback(self.bg.discard)
        return t

    # ---------------- state ----------------
    def elapsed_min(self) -> float:
        return (time.time() - self.started_at) / 60 if self.started_at else 0.0

    def phase_budget(self, phase: str) -> float:
        for b in self.plan.get("phase_budgets", []):
            if b["phase"] == phase:
                return float(b["minutes"])
        return 5.0

    async def send_state(self, status: str | None = None) -> None:
        phase = self.memory["phase"]
        await self.send({
            "type": "state",
            "phase": phase,
            "status": status,
            "elapsed_min": round(self.elapsed_min(), 2),
            "duration_min": self.duration_min,
            "phase_budgets": self.plan.get("phase_budgets", []),
            "phase_history": self.memory.get("phase_history", []),
            "voice_enabled": self.voice_enabled,
            "ended": self.ended,
            "sample_rate": get_settings().tts_output_rate,
        })

    async def problem_view(self) -> dict:
        p = self.problem
        assert p
        async with SessionLocal() as db:
            tests = await coding.tests_for(db, p.id, self.id)
        visible = [{"id": t.id, "kind": t.kind, "input": t.call_args, "expected": t.expected_repr,
                    "why": t.rationale} for t in tests if t.kind in coding.VISIBLE_KINDS]
        from .coding.asserts import follow_up, statement_only
        return {
            "id": p.id, "slug": p.slug, "title": p.title, "difficulty": p.difficulty, "tags": p.tags,
            "statement": statement_only(p.description), "examples": p.examples, "constraints": p.constraints,
            "follow_up": follow_up(p.description), "starter_code": p.starter_code, "visible_tests": visible,
            "hidden_count": sum(t.kind == "hidden" for t in tests), "hard_count": sum(t.kind == "hard" for t in tests),
        }

    # ---------------- opening ----------------
    async def start(self) -> None:
        if self.ended:
            await self.send_state()
            return
        if self.started_at is None:
            self.started_at = time.time()
            self.memory["phase_started"] = time.time()
            async with SessionLocal() as db:
                iv = await db.get(Interview, self.id)
                iv.status = "live"
                iv.started_at = datetime.now(timezone.utc)
                await db.commit()
        await self.send_state()
        if self.history:
            # Reconnect: pick up naturally instead of restarting.
            await self.handle_event("[event] The candidate reconnected after a connection drop. Briefly welcome "
                                    "them back and continue where you left off.")
            return
        text = f"{self.plan.get('opening_line', '').strip()}"
        self.turn_counter += 1
        r = Reply(self.turn_counter, "", "voice")
        self.reply = r
        r.task = asyncio.create_task(self._speak_static(r, text))

    async def _speak_static(self, r: Reply, text: str) -> None:
        try:
            await self.send({"type": "agent_turn_start", "turn_id": r.turn_id})
            if self.voice_enabled:
                r.tts = new_stream(self.voice_id, lambda pcm: self._on_pcm(r, pcm))
                await r.tts.open()
                await r.tts.send_text(speakable(text))
            await self.send({"type": "agent_text_delta", "turn_id": r.turn_id, "delta": strip_tags(text)})
            r.spoken.append(text)
            if r.tts:
                await r.tts.finish()
            await self._finish_reply(r)
        except asyncio.CancelledError:
            await self._cancelled(r)
            raise
        except Exception as e:
            log.exception("opening failed")
            await self.send({"type": "error", "message": f"voice error: {e}"})
            await self._finish_reply(r)

    # ---------------- inbound events ----------------
    async def on_speech_start(self) -> None:
        self.candidate_speaking = True
        self.last_activity = time.time()
        if self.hold_task:
            self.hold_task.cancel()
            self.hold_task = None
        r = self.reply
        if r and r.candidate_text and not r.audio_started and r.task and not r.task.done():
            # They resumed before we said anything: drop the speculative reply, keep their words.
            r.metrics["cancelled_speculative"] = True
            r.task.cancel()
        if self.voice_enabled and self.prepared_tts is None:
            self.prepared_tts = new_stream(self.voice_id, self._noop_audio)
            self.spawn(self._prepare_tts(self.prepared_tts))

    async def on_speech_misfire(self) -> None:
        self.candidate_speaking = False
        if self.pending and not self.hold_task and not self.reply:
            await self._schedule_reply(0)

    async def on_barge_in(self) -> None:
        r = self.reply
        if r and r.task and not r.task.done():
            r.metrics["barged_in"] = True
            r.task.cancel()

    async def _noop_audio(self, pcm: bytes) -> None:
        return None

    async def _prepare_tts(self, t: TTSStream | LocalTTSStream) -> None:
        try:
            await t.open()
        except Exception as e:
            log.warning("tts prepare failed: %s", e)
            if self.prepared_tts is t:
                self.prepared_tts = None

    async def on_utterance(self, wav: bytes) -> None:
        received = time.perf_counter()
        self.candidate_speaking = False
        self.last_activity = time.time()
        speech_ms = max(0.0, (len(wav) - 44) / 32.0)  # 16 kHz * 16-bit mono
        context = (self.history[-1]["content"][-400:] if self.history else "")
        try:
            # Whisper's prompt biases spelling: the role's and candidate's tech terms, then what was just asked.
            text, stt_ms = await stt.transcribe(wav, prompt=f"{self.vocab}\n{context}")
        except Exception as e:
            log.exception("stt failed")
            await self.send({"type": "error", "message": f"Couldn't transcribe that ({e}). You can also type."})
            return
        if not text:
            await self.send({"type": "resume"})
            return
        r = self.reply
        if r and r.audio_started and r.task and not r.task.done():
            if turns.is_backchannel(text):
                await self.send({"type": "resume"})
                return
            r.metrics["barged_in"] = True
            r.task.cancel()
            try:
                await r.task
            except BaseException:
                pass
        await self.send({"type": "transcript", "speaker": "candidate", "text": text, "partial": True})
        self.pending.append(text)
        self.pending_speech_ms += speech_ms
        self.pending_received_at = received
        self.pending_stt_ms = stt_ms
        joined = " ".join(self.pending)
        if turns.wants_time(joined) and not (self.reply and self.reply.task and not self.reply.task.done()):
            await self._grant_time()
            return
        await self._schedule_reply(turns.hold_ms(joined, phase=self.memory["phase"], speech_ms=self.pending_speech_ms))

    async def _grant_time(self) -> None:
        """They asked for time: a short spoken OK straight to TTS (no LLM round trip), then stay quiet."""
        if self.hold_task:
            self.hold_task.cancel()
            self.hold_task = None
        text = " ".join(self.pending)
        self.pending = []
        self.turn_counter += 1
        r = Reply(self.turn_counter, text, "voice")
        r.metrics.update(stt_ms=round(self.pending_stt_ms), speech_ms=round(self.pending_speech_ms), channel="voice",
                         granted_time=True, t0=self.pending_received_at)
        self.pending_speech_ms = 0
        self.history.append({"role": "user", "content": text, "seq": 0})
        self.reply = r
        r.task = asyncio.create_task(self._speak_static(r, turns.TIME_ACKS[self.turn_counter % len(turns.TIME_ACKS)]))

    async def _schedule_reply(self, hold: int) -> None:
        self.pending_hold_ms = hold
        if self.hold_task:
            self.hold_task.cancel()
        if hold <= 0:
            self.hold_task = None
            await self._start_reply("voice")
            return

        async def waiter():
            try:
                await asyncio.sleep(hold / 1000)
                self.hold_task = None
                await self._start_reply("voice")
            except asyncio.CancelledError:
                pass

        await self.send({"type": "listening_hold", "ms": hold})
        self.hold_task = asyncio.create_task(waiter())

    async def handle_text(self, text: str, sink: Sink | None = None) -> None:
        """Typed message from the candidate (chat / AG-UI)."""
        self.last_activity = time.time()
        if self.reply and self.reply.task and not self.reply.task.done():
            self.reply.task.cancel()
            try:
                await self.reply.task
            except BaseException:
                pass
        self.pending.append(text)
        self.pending_received_at = time.perf_counter()
        self.pending_stt_ms = 0
        self.pending_speech_ms = 0
        await self.send({"type": "transcript", "speaker": "candidate", "text": text, "partial": False})
        if sink is None and turns.wants_time(text):
            await self._grant_time()
            return
        await self._start_reply("text", sink=sink)

    async def handle_event(self, text: str) -> None:
        """System event (code run, silence...). Only interjects when nobody is mid-turn."""
        if self.ended or self.candidate_speaking or self.hold_task or self.pending:
            return
        if self.reply and self.reply.task and not self.reply.task.done():
            return
        self.pending.append(text)
        self.pending_received_at = time.perf_counter()
        self.pending_stt_ms = 0
        await self._start_reply("event")

    async def _start_reply(self, channel: str, sink: Sink | None = None) -> None:
        if not self.pending or self.ended:
            return
        if self.reply and self.reply.task and not self.reply.task.done():
            if self.reply.audio_started:
                return
            self.reply.task.cancel()
            try:
                await self.reply.task
            except BaseException:
                pass
        text = " ".join(self.pending)
        self.pending = []
        self.turn_counter += 1
        r = Reply(self.turn_counter, text, channel)
        r.metrics.update(stt_ms=round(getattr(self, "pending_stt_ms", 0)), speech_ms=round(self.pending_speech_ms),
                         channel=channel, hold_ms=getattr(self, "pending_hold_ms", 0))
        self.pending_hold_ms = 0
        r.metrics["t0"] = self.pending_received_at
        self.pending_speech_ms = 0
        self.reply = r
        r.task = asyncio.create_task(self._generate(r, sink))

    # ---------------- generation ----------------
    def _live_state(self) -> str:
        mem = self.memory
        phase = mem["phase"]
        in_phase = (time.time() - mem.get("phase_started", time.time())) / 60
        budget = self.phase_budget(phase)
        lines = [
            "LIVE STATE (private to you)",
            f"Time: {self.elapsed_min():.1f} of {self.duration_min} min elapsed. Phase '{phase}' for "
            f"{in_phase:.1f} min (budget {budget:g}).",
        ]
        if in_phase > budget * 1.3 and phase not in ("wrap_up",):
            lines.append("You are over this phase's budget: wrap it up gracefully and move on.")
        remaining = self.duration_min - self.elapsed_min()
        if remaining < 6 and phase not in ("candidate_questions", "wrap_up"):
            lines.append(f"Only {remaining:.0f} min left overall: move toward candidate questions and wrap-up.")
        lines.append(wm.render(mem))
        if self.problem:
            if mem.get("problem_presented"):
                code = self.workspace.get("code", "")
                numbered = "\n".join(f"{i + 1:>3}| {ln}" for i, ln in enumerate(code.splitlines()[:150]))
                since = time.time() - self.code_changed_at
                lines.append(f"CODING PROBLEM (presented): {self.problem.title} [{self.problem.difficulty}] — "
                             f"plan focus: {self.plan.get('coding_focus')}")
                lines.append(f"EDITOR ({self.workspace.get('language', 'python')}, last edit {since:.0f}s ago):\n"
                             f"{numbered or '(empty)'}")
                if self.last_run:
                    lines.append(f"LAST TEST RUN (you see everything): {llm.dumps(self.last_run)}")
            else:
                lines.append(f"CODING PROBLEM (not yet presented): {self.problem.title} — "
                             f"{self.problem.description[:500]}")
        notes = self.workspace.get("notes", "").strip()
        if notes:
            lines.append(f"CANDIDATE NOTES:\n{notes[-1500:]}")
        wb = self.workspace.get("whiteboard_summary", "").strip()
        if wb:
            lines.append(f"WHITEBOARD:\n{wb[-1500:]}")
        lessons = getattr(self, "_lessons", [])
        if lessons:
            lines.append("LESSONS FROM PAST INTERVIEWS (apply when relevant):")
            lines += [f"  - {l['lesson']}" for l in lessons]
        lines.append("Respond with what you say next (or exactly <listen/>).")
        return "\n".join(lines)

    async def _refresh_lessons(self) -> None:
        async with SessionLocal() as db:
            rows = await playbook.active_lessons(db, scope=self.memory["phase"], limit=4)
        self._lessons = [{"id": r.id, "lesson": r.lesson} for r in rows]
        used = set(self.memory.get("lessons_used", []))
        used.update(r.id for r in rows)
        self.memory["lessons_used"] = sorted(used)

    def _messages(self) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": self.system}]
        msgs += [{"role": h["role"], "content": h["content"]} for h in self.history[-HISTORY_TURNS:]]
        msgs.append({"role": "system", "content": self._live_state()})
        return msgs

    async def _on_pcm(self, r: Reply, pcm: bytes) -> None:
        if not r.audio_started:
            r.audio_started = True
            r.metrics["first_audio_ms"] = round((time.perf_counter() - r.metrics.get("t0", time.perf_counter())) * 1000)
            await self.send({"type": "audio_start", "turn_id": r.turn_id})
        await self.send_audio(r.turn_id, pcm)

    async def _take_tts(self, r: Reply) -> TTSStream | LocalTTSStream | None:
        if not self.voice_enabled or r.channel == "text_only":
            return None
        t = self.prepared_tts
        self.prepared_tts = None
        if t is None or t.error or t.closed:
            t = new_stream(self.voice_id, self._noop_audio)
            self.spawn(self._prepare_tts(t))
        t.on_audio = lambda pcm: self._on_pcm(r, pcm)
        return t

    async def _generate(self, r: Reply, sink: Sink | None) -> None:
        is_event = r.channel == "event"
        user_msg = {"role": "user", "content": r.candidate_text, "seq": 0}
        self.history.append(user_msg)
        if not hasattr(self, "_lessons") or self.turn_counter % 6 == 0:
            await self._refresh_lessons()
        chunker = SentenceChunker()
        tts: TTSStream | LocalTTSStream | None = None
        listen = False
        try:
            await self.send({"type": "thinking", "turn_id": r.turn_id})
            msgs = self._messages()
            t_llm = time.perf_counter()
            for _round in range(3):
                stream = await llm.client().chat.completions.create(
                    model=self.chat_dep, messages=msgs, tools=TOOLS, stream=True,
                    **llm.sampling_kwargs(self.chat_dep, 0.7, 450, self.chat_effort),
                )
                head, decided = "", False
                calls: dict[int, dict] = {}
                round_text = []
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    if d.content:
                        if "llm_ttft_ms" not in r.metrics:
                            r.metrics["llm_ttft_ms"] = round((time.perf_counter() - t_llm) * 1000)
                        if not decided:
                            head += d.content
                            stripped = head.lstrip()
                            if stripped.startswith("<") and len(stripped) < 9:
                                continue
                            decided = True
                            if stripped.startswith("<listen"):
                                listen = True
                                continue
                            delta = head
                        else:
                            if listen:
                                continue
                            delta = d.content
                        round_text.append(delta)
                        if tts is None:
                            tts = await self._take_tts(r)
                            r.tts = tts
                            await self.send({"type": "agent_turn_start", "turn_id": r.turn_id})
                        for piece in chunker.feed(delta):
                            await self._emit(r, tts, piece, sink)
                    for tc in d.tool_calls or []:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
                rest = chunker.flush()
                if rest and not listen:
                    await self._emit(r, tts, rest, sink)
                if not calls:
                    break
                results = []
                for c in calls.values():
                    try:
                        args = json.loads(c["args"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    res = await self._tool(r, c["name"], args)
                    results.append((c, res))
                spoke = "".join(round_text).strip()
                if spoke and all(c["name"] in FIRE_AND_FORGET for c in calls.values()):
                    break
                msgs.append({"role": "assistant", "content": spoke or None, "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                    for c in calls.values()]})
                for c, res in results:
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": llm.dumps(res)})
                listen = False
            if tts:
                await tts.finish()
                if tts.error:
                    await self.send({"type": "error", "message": f"voice: {tts.error}"})
            if listen and not r.spoken:
                r.metrics["listen"] = True
            await self._finish_reply(r)
        except asyncio.CancelledError:
            await self._cancelled(r)
            raise
        except Exception as e:
            log.exception("generation failed")
            await self.send({"type": "error", "message": f"interviewer error: {e}"})
            if not is_event:
                # Put their words back so a retry (or the next utterance) includes them.
                if self.history and self.history[-1] is user_msg:
                    self.history.pop()
                self.pending.insert(0, r.candidate_text)
            if tts:
                await tts.close()
            self.reply = None
        finally:
            if sink:
                await sink.close()

    async def _emit(self, r: Reply, tts: TTSStream | LocalTTSStream | None, piece: str, sink: Sink | None) -> None:
        # The model sometimes appends its silence marker to real speech ("Sure, take your time. <listen/>").
        piece = LISTEN_TAG.sub("", piece).strip()
        if not piece:
            return
        r.spoken.append(piece)
        caption = strip_tags(piece)
        await self.send({"type": "agent_text_delta", "turn_id": r.turn_id, "delta": caption + " "})
        if sink:
            await sink.put(caption + " ")
        if tts:
            if "tts_first_text_ms" not in r.metrics:
                r.metrics["tts_first_text_ms"] = round((time.perf_counter() - r.metrics.get("t0", 0)) * 1000)
            await tts.send_text(speakable(piece) + " ")

    async def _cancelled(self, r: Reply) -> None:
        if r.tts:
            await r.tts.close()
        await self.send({"type": "interrupt", "turn_id": r.turn_id})
        if not r.audio_started and r.candidate_text and not r.metrics.get("barged_in"):
            # Speculative reply dropped: their words go back to the front of the queue.
            if self.history and self.history[-1].get("content") == r.candidate_text:
                self.history.pop()
            if not r.candidate_text.startswith("[event]"):
                self.pending.insert(0, r.candidate_text)
        else:
            r.metrics["interrupted"] = True
            await self._finish_reply(r, interrupted=True)
        if self.reply is r:
            self.reply = None

    async def _finish_reply(self, r: Reply, interrupted: bool = False) -> None:
        spoken = " ".join(s.strip() for s in r.spoken).strip()
        actions = " ".join(f"({a})" for a in r.actions)
        r.metrics.pop("t0", None)
        cand_seq = None
        if r.candidate_text:
            self.seq += 1
            cand_seq = self.seq
            for h in reversed(self.history):
                if h.get("content") == r.candidate_text and h["role"] == "user":
                    h["seq"] = cand_seq
                    break
            if not r.candidate_text.startswith("[event]"):
                await self.send({"type": "transcript", "speaker": "candidate", "text": r.candidate_text,
                                 "seq": cand_seq, "partial": False})
        content = (spoken + (" " + actions if actions else "")).strip()
        if interrupted and spoken:
            content += " [interrupted by candidate]"
        if content:
            self.seq += 1
            self.history.append({"role": "assistant", "content": content, "seq": self.seq})
        elif r.candidate_text:
            # <listen/>: keep the turn in context so the model knows it stayed quiet
            self.seq += 1
            self.history.append({"role": "assistant", "content": "<listen/>", "seq": self.seq})
        await self.send({"type": "agent_turn_end", "turn_id": r.turn_id, "text": strip_tags(spoken),
                         "listen": not spoken, "metrics": r.metrics})
        if self.reply is r:
            self.reply = None
        phase = self.memory["phase"]
        rows: list[Turn] = []
        if cand_seq is not None:
            rows.append(Turn(interview_id=self.id, seq=cand_seq, speaker="system" if r.candidate_text.startswith(
                "[event]") else "candidate", text=r.candidate_text, phase=phase,
                channel="text" if r.channel == "text" else "voice", metrics={}))
        if spoken:
            rows.append(Turn(interview_id=self.id, seq=self.seq, speaker="interviewer", text=strip_tags(spoken),
                             phase=phase, metrics=r.metrics))
        self.spawn(self._persist_turns(rows))
        if r.candidate_text and not r.candidate_text.startswith("[event]"):
            prev = next((h["content"] for h in reversed(self.history[:-1]) if h["role"] == "assistant"), "")
            self.spawn(self._extract(prev, r.candidate_text, cand_seq or self.seq))
        if self.memory.get("end_requested") and not self.ended:
            await self._end()
        elif self.pending and not self.hold_task and not self.candidate_speaking:
            await self._start_reply("voice")

    async def _persist_turns(self, rows: list[Turn]) -> None:
        if not rows:
            return
        async with SessionLocal() as db:
            db.add_all(rows)
            iv = await db.get(Interview, self.id)
            iv.memory = self.memory
            await db.commit()

    async def _extract(self, interviewer_text: str, candidate_text: str, seq: int) -> None:
        async with self.extract_lock:
            # Background work: let the live reply have the rate limit (50k TPM on this deployment) first.
            for _ in range(60):
                if not (self.reply and self.reply.task and not self.reply.task.done()):
                    break
                await asyncio.sleep(0.5)
            try:
                delta = await wm.extract(self.memory, interviewer_text, candidate_text, self.chat_dep)
                wm.apply_delta(self.memory, delta, seq)
                await self.send({"type": "memory", "memory": self.memory_view()})
            except Exception as e:
                log.warning("memory extraction failed: %s", e)

    def memory_view(self) -> dict:
        m = self.memory
        return {"threads": m["threads"][-10:], "claims": m["claims"][-10:], "sentiment": m["sentiment"],
                "evidence_count": len(m["evidence"]), "hints": m["hints"]}

    # ---------------- tools ----------------
    async def _tool(self, r: Reply, name: str, args: dict) -> dict:
        log.info("tool %s %s", name, args)
        try:
            if name == "advance_phase":
                to = args.get("to")
                if to and to != self.memory["phase"]:
                    now = time.time()
                    self.memory["phase_history"].append({
                        "phase": self.memory["phase"], "minutes": round((now - self.memory["phase_started"]) / 60, 2),
                        "ended_at_min": round(self.elapsed_min(), 2)})
                    self.memory["phase"] = to
                    self.memory["phase_started"] = now
                    r.actions.append(f"moved to {to}")
                    await self.send_state()
                    self.spawn(self._refresh_lessons())
                return {"ok": True, "phase": to}
            if name == "present_problem":
                if not self.problem:
                    return {"ok": False, "error": "no problem planned"}
                self.memory["problem_presented"] = True
                if self.memory["phase"] != "coding":
                    await self._tool(r, "advance_phase", {"to": "coding", "reason": "problem presented"})
                if not self.workspace.get("code"):
                    self.workspace["code"] = self.problem.starter_code
                    self.code_changed_at = time.time()
                await self.send({"type": "problem", "problem": await self.problem_view()})
                await self.send({"type": "ui_action", "name": "show_panel", "args": {"panel": "code"}})
                r.actions.append("presented the coding problem")
                return {"ok": True, "title": self.problem.title, "description": self.problem.description[:1500]}
            if name == "give_hint":
                level = int(args.get("level", 1))
                self.memory["hints"].append({"level": level, "hint": args.get("hint", "")[:400],
                                             "minute": round(self.elapsed_min(), 1)})
                r.actions.append(f"hint L{level}")
                await self.send({"type": "memory", "memory": self.memory_view()})
                return {"ok": True}
            if name == "run_tests":
                if not (self.problem and self.memory.get("problem_presented")):
                    return {"ok": False, "error": "problem not presented"}
                result = await self.run_code(self.workspace.get("code", ""), "submit", by_agent=True)
                return result
            if name == "add_test_case":
                if not self.problem:
                    return {"ok": False, "error": "no problem"}
                async with SessionLocal() as db:
                    t = await coding.inject_test(db, interview_id=self.id, problem=self.problem,
                                                 call_args=args.get("input", ""), rationale=args.get("why", ""))
                await self.send({"type": "problem", "problem": await self.problem_view()})
                r.actions.append(f"added test {t.call_args}")
                return {"ok": True, "input": t.call_args, "expected": t.expected_repr}
            if name == "show_panel":
                await self.send({"type": "ui_action", "name": "show_panel", "args": args})
                return {"ok": True}
            if name == "draw_on_whiteboard":
                await self.send({"type": "ui_action", "name": "draw_on_whiteboard", "args": args})
                await self.send({"type": "ui_action", "name": "show_panel", "args": {"panel": "whiteboard"}})
                r.actions.append("sketched on the whiteboard")
                return {"ok": True}
            if name == "end_interview":
                self.memory["end_requested"] = True
                return {"ok": True}
        except Exception as e:
            log.exception("tool %s failed", name)
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": f"unknown tool {name}"}

    # ---------------- workspace ----------------
    async def on_workspace(self, data: dict) -> None:
        if "code" in data and data["code"] != self.workspace.get("code"):
            self.workspace["code"] = data["code"][:20000]
            self.code_changed_at = time.time()
            self.last_activity = time.time()
        for k in ("notes", "whiteboard_summary", "language"):
            if k in data:
                self.workspace[k] = str(data[k])[:20000]
        if "whiteboard" in data:
            self.workspace["whiteboard"] = data["whiteboard"]
        self.spawn(self._save_workspace())

    async def _save_workspace(self) -> None:
        await asyncio.sleep(0)  # coalesce bursts on the loop
        async with SessionLocal() as db:
            snap = await db.get(WorkspaceSnapshot, self.id)
            if snap is None:
                snap = WorkspaceSnapshot(interview_id=self.id)
                db.add(snap)
            snap.code = self.workspace.get("code", "")
            snap.notes = self.workspace.get("notes", "")
            snap.whiteboard = self.workspace.get("whiteboard", []) or []
            snap.updated_at = datetime.now(timezone.utc)
            await db.commit()

    async def run_code(self, code: str, mode: str, by_agent: bool = False) -> dict:
        assert self.problem
        if self.code_lock.locked() and not by_agent:
            await self.send({"type": "error", "message": "Your last run is still going; try again in a moment."})
            return {"skipped": True}
        async with self.code_lock:
            return await self._run_code(code, mode, by_agent)

    async def _run_code(self, code: str, mode: str, by_agent: bool) -> dict:
        self.workspace["code"] = code
        await self.send({"type": "code_running", "mode": mode, "by_agent": by_agent})
        async with SessionLocal() as db:
            run = await coding.run_code(db, interview_id=self.id, problem=self.problem, code=code, mode=mode)
        view = coding.candidate_view(run)
        await self.send({"type": "code_result", "run": view, "by_agent": by_agent})
        ff = run.summary.get("first_failure")
        agent_view = {
            "mode": mode, "passed": run.summary["passed"], "total": run.summary["total"],
            "by_kind": run.summary["by_kind"], "compile_error": run.summary.get("compile_error"),
            "timeouts": run.summary.get("timeouts"), "killed": run.summary.get("killed"),
            "first_failure": {k: ff.get(k) for k in ("kind", "input", "expected", "actual", "error", "status")}
            if ff else None,
        }
        self.last_run = agent_view
        if not by_agent:
            bk = run.summary["by_kind"]
            desc = ", ".join(f"{k} {v['passed']}/{v['total']}" for k, v in bk.items())
            self.spawn(self.handle_event(
                f"[event] The candidate {'submitted' if mode == 'submit' else 'ran'} their code: {desc or 'no tests ran'}"
                + (f"; compile error: {run.summary['compile_error']}" if run.summary.get("compile_error") else "")
                + ". React briefly only if useful (e.g. nudge them to reason about a failure); otherwise <listen/>."
            ))
        return agent_view

    async def _watch_silence(self) -> None:
        while not self.ended:
            await asyncio.sleep(15)
            try:
                if self.memory["phase"] != "coding" or not self.memory.get("problem_presented"):
                    continue
                now = time.time()
                quiet = now - self.last_activity
                if quiet > SILENCE_CHECK_S and now - self.last_proactive > SILENCE_CHECK_S and self.ws:
                    self.last_proactive = now
                    since_code = now - self.code_changed_at
                    await self.handle_event(
                        f"[event] The candidate has been quiet for about {quiet / 60:.0f} minutes; code last changed "
                        f"{since_code / 60:.1f} minutes ago. If they seem stuck, check in gently (offer to talk it "
                        f"through); if they're clearly making progress, <listen/>."
                    )
            except Exception:
                log.exception("silence watcher")

    # ---------------- end ----------------
    async def _end(self) -> None:
        """Finish now: persist, tell the browser, release the runtime. Slow follow-up work (pending memory
        extraction, then scoring) runs in the background so the goodbye isn't followed by minutes of limbo."""
        self.ended = True
        if self.memory["phase_history"] == [] or self.memory["phase_history"][-1]["phase"] != self.memory["phase"]:
            self.memory["phase_history"].append({
                "phase": self.memory["phase"],
                "minutes": round((time.time() - self.memory["phase_started"]) / 60, 2),
                "ended_at_min": round(self.elapsed_min(), 2)})
        if self.watch_task:
            self.watch_task.cancel()
        async with SessionLocal() as db:
            iv = await db.get(Interview, self.id)
            iv.status = "completed"
            iv.memory = self.memory
            iv.ended_at = datetime.now(timezone.utc)
            await db.commit()
        await self.send({"type": "ended"})
        await self.send_state()
        RUNTIMES.pop(self.id, None)
        asyncio.create_task(self._after_end(list(self.bg)))

    async def _after_end(self, pending: list[asyncio.Task]) -> None:
        """Let in-flight memory extraction land (bounded), save the final memory, then score."""
        if pending:
            done, slow = await asyncio.wait(pending, timeout=90)
            for task in slow:
                task.cancel()
            if slow:
                log.warning("interview %s: %d background tasks still running at end, cancelled", self.id, len(slow))
            async with SessionLocal() as db:
                iv = await db.get(Interview, self.id)
                iv.memory = self.memory
                await db.commit()
        await scorecard.finalize(self.id)

    async def end_now(self) -> None:
        if self.reply and self.reply.task and not self.reply.task.done():
            self.reply.task.cancel()
        if not self.ended:
            await self._end()


RUNTIMES: dict[UUID, SessionRuntime] = {}
_rt_lock = asyncio.Lock()


async def get_runtime(interview_id: UUID) -> SessionRuntime:
    async with _rt_lock:
        rt = RUNTIMES.get(interview_id)
        if rt:
            return rt
        async with SessionLocal() as db:
            iv = await db.get(Interview, interview_id)
            if iv is None:
                raise KeyError("interview not found")
            if iv.status not in ("ready", "live", "completed", "scored"):
                raise ValueError(f"interview is not ready (status={iv.status})")
            problem = await db.get(Problem, iv.problem_id) if iv.problem_id else None
            rows = (await db.scalars(select(Turn).where(Turn.interview_id == iv.id).order_by(Turn.seq))).all()
            history = [{"role": "assistant" if t.speaker == "interviewer" else "user",
                        "content": t.text, "seq": t.seq} for t in rows]
            snap = await db.get(WorkspaceSnapshot, iv.id)
            workspace = {"code": snap.code, "notes": snap.notes, "whiteboard": snap.whiteboard} if snap else {}
            owner = await db.get(User, iv.owner_id) if iv.owner_id else None
            name = ((owner.preferences or {}).get("interviewer_name") if owner else None) or "Alex"
            rt = SessionRuntime(iv, problem, history, workspace, interviewer_name=name)
        RUNTIMES[interview_id] = rt
        return rt

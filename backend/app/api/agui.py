"""AG-UI endpoints consumed by CopilotKit (via the Node CopilotRuntime's HttpAgent).

* interviewer: the typed-chat channel into the live interview runtime (same brain as the voice channel).
* analyst: a dashboard copilot that answers questions about past interviews and can drive the dashboard UI
  through CopilotKit frontend tools.
"""

import json
import logging
import uuid
from uuid import UUID

from ag_ui.core import (
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .. import llm
from ..config import get_settings
from .. import auth
from ..db import Candidate, Interview, SessionLocal, Turn
from ..session import Sink, get_runtime
from .interviews import _summary_row

log = logging.getLogger(__name__)
router = APIRouter(prefix="/agui")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(getattr(p, "text", "") or "" for p in content)
    return ""


def _last_user(inp: RunAgentInput) -> str:
    for m in reversed(inp.messages):
        if m.role == "user":
            return _text(m.content).strip()
    return ""


@router.post("/interviewer")
async def interviewer(inp: RunAgentInput, request: Request):
    enc = EventEncoder(accept=request.headers.get("accept"))

    async def stream():
        yield enc.encode(RunStartedEvent(thread_id=inp.thread_id, run_id=inp.run_id))
        msg_id = str(uuid.uuid4())
        try:
            # The last message may be a tool result follow-up; only user messages drive the interview.
            if not inp.messages or inp.messages[-1].role != "user":
                yield enc.encode(RunFinishedEvent(thread_id=inp.thread_id, run_id=inp.run_id))
                return
            props = inp.forwarded_props or {}
            user_id = auth.verify_agent_token(props.get("agentToken"))
            iid = UUID(str(props.get("interviewId") or inp.thread_id))
            async with SessionLocal() as db:
                iv = await db.get(Interview, iid)
            if user_id is None or iv is None or iv.owner_id != user_id:
                raise PermissionError("Reload the page to chat with the interviewer")
            rt = await get_runtime(iid)
            sink = Sink()
            rt.spawn(rt.handle_text(_last_user(inp), sink=sink))
            opened = False
            while True:
                delta = await sink.queue.get()
                if delta is None:
                    break
                if not opened:
                    yield enc.encode(TextMessageStartEvent(message_id=msg_id, role="assistant"))
                    opened = True
                yield enc.encode(TextMessageContentEvent(message_id=msg_id, delta=delta))
            if opened:
                yield enc.encode(TextMessageEndEvent(message_id=msg_id))
            yield enc.encode(RunFinishedEvent(thread_id=inp.thread_id, run_id=inp.run_id))
        except Exception as e:
            log.exception("agui interviewer")
            yield enc.encode(RunErrorEvent(message=str(e)))

    return StreamingResponse(stream(), media_type=enc.get_content_type())


# ---------------- analyst ----------------
ANALYST_SYSTEM = """You are the hiring analyst copilot inside InterviewBud's dashboard. You answer questions about
interviews the AI interviewer has conducted, using the server tools to fetch real data; never invent candidates,
scores or quotes. Be concise and specific; cite interview ids and evidence. When the user wants to see something,
use the UI tools (e.g. open an interview) instead of describing how to click. Never make or suggest decisions
based on protected characteristics."""

SERVER_TOOLS = [
    {"type": "function", "function": {
        "name": "list_interviews", "description": "List all interviews with status, recommendation and scores.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_interview", "description": "Full scorecard, critique and transcript for one interview.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
]


async def _run_server_tool(name: str, args: dict, user_id: UUID) -> dict | list:
    async with SessionLocal() as db:
        if name == "list_interviews":
            rows = (await db.execute(select(Interview, Candidate).join(Candidate).where(Interview.owner_id == user_id)
                                     .order_by(Interview.created_at.desc()).limit(100))).all()
            return [_summary_row(iv, c) for iv, c in rows]
        if name == "get_interview":
            iv = await db.get(Interview, UUID(args["id"]))
            if not iv or iv.owner_id != user_id:
                return {"error": "not found"}
            cand = await db.get(Candidate, iv.candidate_id)
            turns = (await db.scalars(select(Turn).where(Turn.interview_id == iv.id).order_by(Turn.seq))).all()
            return {**_summary_row(iv, cand), "scorecard": iv.scorecard, "critique": iv.critique,
                    "transcript": [f"[turn {t.seq}] {t.speaker}: {t.text}" for t in turns][:300]}
    return {"error": f"unknown tool {name}"}


def _oa_messages(inp: RunAgentInput) -> list[dict]:
    sys = ANALYST_SYSTEM
    if inp.context:
        sys += "\n\nAPP CONTEXT:\n" + "\n".join(f"- {c.description}: {c.value}" for c in inp.context)
    out: list[dict] = [{"role": "system", "content": sys}]
    for m in inp.messages:
        if m.role == "assistant":
            d: dict = {"role": "assistant", "content": _text(m.content) or None}
            if getattr(m, "tool_calls", None):
                d["tool_calls"] = [{"id": t.id, "type": "function",
                                    "function": {"name": t.function.name, "arguments": t.function.arguments}}
                                   for t in m.tool_calls]
            if d["content"] or d.get("tool_calls"):
                out.append(d)
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": _text(m.content)})
        elif m.role == "user":
            out.append({"role": "user", "content": _text(m.content)})
    return out


@router.post("/analyst")
async def analyst(inp: RunAgentInput, request: Request):
    enc = EventEncoder(accept=request.headers.get("accept"))
    dep = get_settings().azure_openai_chat_deployment

    async def stream():
        yield enc.encode(RunStartedEvent(thread_id=inp.thread_id, run_id=inp.run_id))
        try:
            user_id = auth.verify_agent_token((inp.forwarded_props or {}).get("agentToken"))
            if user_id is None:
                raise PermissionError("Reload the page to use the analyst")
            frontend = [{"type": "function", "function": {
                "name": t.name, "description": t.description,
                "parameters": t.parameters or {"type": "object", "properties": {}}}} for t in (inp.tools or [])]
            front_names = {t.name for t in (inp.tools or [])}
            msgs = _oa_messages(inp)
            for _ in range(5):
                resp = await llm.client().chat.completions.create(
                    model=dep, messages=msgs, tools=SERVER_TOOLS + frontend, stream=True,
                    **llm.sampling_kwargs(dep, 0.2, 900, get_settings().azure_openai_chat_reasoning_effort))
                msg_id, opened, calls = str(uuid.uuid4()), False, {}
                text_parts = []
                async for chunk in resp:
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    if d.content:
                        if not opened:
                            yield enc.encode(TextMessageStartEvent(message_id=msg_id, role="assistant"))
                            opened = True
                        text_parts.append(d.content)
                        yield enc.encode(TextMessageContentEvent(message_id=msg_id, delta=d.content))
                    for tc in d.tool_calls or []:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        slot["id"] = tc.id or slot["id"]
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
                if opened:
                    yield enc.encode(TextMessageEndEvent(message_id=msg_id))
                if not calls:
                    break
                fe = [c for c in calls.values() if c["name"] in front_names]
                if fe:
                    # Hand frontend tools to CopilotKit; it runs them in the browser and starts a follow-up run.
                    for c in fe:
                        yield enc.encode(ToolCallStartEvent(tool_call_id=c["id"], tool_call_name=c["name"],
                                                            parent_message_id=msg_id))
                        yield enc.encode(ToolCallArgsEvent(tool_call_id=c["id"], delta=c["args"] or "{}"))
                        yield enc.encode(ToolCallEndEvent(tool_call_id=c["id"]))
                    break
                msgs.append({"role": "assistant", "content": "".join(text_parts) or None, "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                    for c in calls.values()]})
                for c in calls.values():
                    try:
                        args = json.loads(c["args"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    res = await _run_server_tool(c["name"], args, user_id)
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": llm.dumps(res)[:60000]})
            yield enc.encode(RunFinishedEvent(thread_id=inp.thread_id, run_id=inp.run_id))
        except Exception as e:
            log.exception("agui analyst")
            yield enc.encode(RunErrorEvent(message=str(e)))

    return StreamingResponse(stream(), media_type=enc.get_content_type())

"""Browser <-> interview runtime WebSocket.

client -> server  text JSON: start | speech_start | speech_misfire | barge_in | text | workspace | run_code |
                             playback_started | end | ping
                  binary: one WAV utterance (16 kHz mono, 16-bit) per VAD segment
server -> client  text JSON events (see session.py); binary: uint32 LE turn_id + PCM16 mono audio
"""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth
from ..db import Interview, SessionLocal, Turn
from ..session import RUNTIMES, get_runtime

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/session/{interview_id}")
async def session_ws(ws: WebSocket, interview_id: UUID):
    # Cross-site WebSocket hijacking: browsers attach cookies to WS handshakes from any site; only ours may connect.
    if not auth.origin_allowed(ws.headers.get("origin")):
        await ws.close(code=4403)
        return
    await ws.accept()
    user = await auth.websocket_user(ws)
    async with SessionLocal() as db:
        iv = await db.get(Interview, interview_id)
    if user is None or iv is None or iv.owner_id != user.id:
        msg = "Sign in to join this interview" if user is None else "interview not found"
        await ws.send_text(json.dumps({"type": "error", "message": msg, "fatal": True}))
        await ws.close(code=4401 if user is None else 4404)
        return
    if iv.status in ("completed", "scored") and interview_id not in RUNTIMES:
        # Finished: no runtime to start. Tell the page, then close with a code that means "don't reconnect".
        await ws.send_text(json.dumps({"type": "state", "ended": True, "status": iv.status, "phase": "wrap_up"}))
        await ws.send_text(json.dumps({"type": "ended"}))
        await ws.close(code=4001, reason="interview ended")
        return
    try:
        rt = await get_runtime(interview_id)
    except (KeyError, ValueError) as e:
        await ws.send_text(json.dumps({"type": "error", "message": str(e), "fatal": True}))
        await ws.close()
        return
    await rt.attach(ws)
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                rt.spawn(rt.on_utterance(msg["bytes"]))
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind == "start":
                await rt.start()
            elif kind == "speech_start":
                await rt.on_speech_start()
            elif kind == "speech_misfire":
                await rt.on_speech_misfire()
            elif kind == "barge_in":
                await rt.on_barge_in()
            elif kind == "text":
                rt.spawn(rt.handle_text(str(data.get("text", ""))[:4000]))
            elif kind == "workspace":
                await rt.on_workspace(data)
            elif kind == "run_code":
                if rt.problem and rt.memory.get("problem_presented"):
                    rt.spawn(rt.run_code(str(data.get("code", ""))[:20000], "submit" if data.get("mode") == "submit"
                                         else "run"))
            elif kind == "playback_started":
                v2v = data.get("voice_to_voice_ms")
                if isinstance(v2v, (int, float)) and 0 < v2v < 60000:
                    rt.spawn(_record_v2v(rt.id, int(v2v)))
            elif kind == "end":
                await rt.end_now()
            elif kind == "ping":
                await rt.send({"type": "pong", "t": data.get("t")})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws loop")
    finally:
        rt.detach(ws)


async def _record_v2v(interview_id: UUID, ms: int) -> None:
    """Client-measured end-of-speech -> first audible audio, attached to the latest interviewer turn."""
    from sqlalchemy import select

    async with SessionLocal() as db:
        t = await db.scalar(select(Turn).where(Turn.interview_id == interview_id, Turn.speaker == "interviewer")
                            .order_by(Turn.seq.desc()).limit(1))
        if t and "voice_to_voice_ms" not in (t.metrics or {}):
            t.metrics = {**(t.metrics or {}), "voice_to_voice_ms": ms}
            await db.commit()

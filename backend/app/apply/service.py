"""Applying to a job on the candidate's behalf, as four separate, explicit steps:

1. read:    open the real application form and list its fields (browser.read_form).
2. map:     propose a value for each field and say where it came from (application details, the screening call,
            confirmed facts, approved drafts). Nothing is invented: no source, no value. Voluntary self-ID questions
            are never answered for them; sensitive fields (IDs, date of birth, bank details) are never filled.
3. approve: the candidate reviews every value, edits any, and approves. The approved snapshot is what gets used.
4. fill:    fill the real form and show a screenshot; nothing is sent.
   submit:  only on the candidate's explicit click: fill the approved values again and press the form's own submit
            button. A human check (CAPTCHA) stops it and hands over to the candidate; we never try to get past one.
"""

import asyncio
import logging
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm
from ..config import get_settings
from ..db import Application, Interview, Opportunity, PrepPlan, PrepSession, SessionLocal, Turn, User
from ..prep import screening
from ..prep import service as prep_service
from . import browser
from .fields import classify, contact_key

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_tasks: set[asyncio.Task] = set()

DETAIL_KEYS = ["first_name", "last_name", "preferred_name", "email", "phone", "country", "location", "linkedin",
               "github", "website", "work_authorization", "needs_sponsorship", "relocation", "notice_period",
               "how_heard"]
DETAIL_LABELS = {"work_authorization": "Work authorization", "needs_sponsorship": "Needs visa sponsorship",
                 "relocation": "Open to relocating", "notice_period": "Notice period / earliest start",
                 "how_heard": "How you heard about jobs"}


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def now() -> datetime:
    return datetime.now(timezone.utc)


def log_event(app: Application, event: str, detail: str = "") -> None:
    app.history = [*(app.history or []), {"at": now().isoformat(), "event": event, "detail": detail[:500]}]
    app.updated_at = now()


# ---------------- where the form lives ----------------
def apply_url(url: str) -> tuple[str, str]:
    """(ats, url of the page with the application form)."""
    u = url.split("#")[0]
    if re.search(r"greenhouse\.io/", u):
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", u)
        return "greenhouse", (f"https://job-boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}" if m else u)
    m = re.search(r"(https://jobs(?:\.eu)?\.lever\.co/[\w.-]+/[0-9a-f-]{36})", u)
    if m:
        return "lever", m.group(1) + "/apply"
    m = re.search(r"(https://jobs\.ashbyhq\.com/[\w.-]+/[0-9a-f-]{36})", u)
    if m:
        return "ashby", m.group(1) + "/application"
    return "generic", u


# ---------------- application details and resume file ----------------
def details_for(user: User) -> dict:
    d = dict((user.preferences or {}).get("applicant") or {})
    name = (user.display_name or "").split()
    d.setdefault("first_name", name[0] if name else "")
    d.setdefault("last_name", " ".join(name[1:]) if len(name) > 1 else "")
    d.setdefault("email", user.email or "")
    return {k: str(d.get(k) or "").strip() for k in DETAIL_KEYS}


def resume_path(user_id: UUID) -> Path:
    return DATA_DIR / "resumes" / str(user_id)


def resume_file(user: User) -> dict | None:
    meta = (user.preferences or {}).get("resume_file")
    if not meta:
        return None
    p = resume_path(user.id) / meta["stored"]
    if not p.exists():
        return None
    return {"name": meta["name"], "mime": meta.get("mime") or mimetypes.guess_type(meta["name"])[0]
            or "application/octet-stream", "data": p.read_bytes()}


def save_resume(user: User, filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in (".pdf", ".docx", ".doc", ".txt", ".rtf"):
        raise ValueError("Upload a PDF, Word or text file")
    folder = resume_path(user.id)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("resume.*"):
        old.unlink()
    (folder / f"resume{ext}").write_bytes(data)
    safe = re.sub(r"[^\w.\- ]", "_", Path(filename).name)[:120] or f"resume{ext}"
    return {"name": safe, "stored": f"resume{ext}", "mime": mimetypes.guess_type(safe)[0] or "application/octet-stream",
            "size": len(data), "uploaded_at": now().isoformat()}


# ---------------- 1. read ----------------
def _tidy_label(f: dict) -> None:
    if f["type"] == "file" and re.fullmatch(r"(attach|upload|choose file|browse)?", f["label"].strip(), re.I):
        f["label"] = f["key"].replace("_", " ").replace("-", " ").strip().capitalize()
    if f["key"] in ("country",) or re.fullmatch(r"country", f["label"].strip(), re.I):
        f["contact"] = "country"


async def start(db: AsyncSession, prep: PrepSession) -> Application:
    opp = await db.get(Opportunity, prep.opportunity_id)
    ats, url = apply_url(opp.final_url or opp.source_url)
    app = Application(owner_id=prep.owner_id, prep_id=prep.id, apply_url=url, ats=ats, status="reading")
    log_event(app, "read_requested", url)
    db.add(app)
    await db.commit()
    spawn(read(app.id))
    return app


async def read(app_id: UUID) -> None:
    async with SessionLocal() as db:
        app = await db.get(Application, app_id)
        try:
            form = await browser.read_form(app.apply_url)
            fields = form["fields"]
            for f in fields:
                _tidy_label(f)
                f["kind"] = "contact" if f.get("contact") else classify(f)
            if not fields:
                app.status, app.error = "needs_you", "We couldn't find an application form on that page"
                log_event(app, "no_form")
                await db.commit()
                return
            app.fields = fields
            app.fill_report = {"submit_button": form.get("submit", ""), "form_url": form.get("final_url", "")}
            log_event(app, "form_read", f"{len(fields)} fields")
            await db.commit()
            await map_values(db, app)
        except Exception as e:
            log.exception("reading the application form failed")
            app.status, app.error = "failed", f"Couldn't read the form ({type(e).__name__})"
            log_event(app, "read_failed", str(e))
            await db.commit()


# ---------------- 2. map ----------------
class FieldAnswer(BaseModel):
    key: str
    value: str = Field(description="The answer, or empty if no source supports one. For fields with options: "
                                   "exactly one option")
    sources: list[str] = Field(description="Ids from SOURCES that support the value; empty if value is empty")
    note: str = Field(description="A few words: why this value, or what the candidate needs to add")


class FieldAnswers(BaseModel):
    answers: list[FieldAnswer]


MAP_SYSTEM = """You fill in a job application form for a candidate, using ONLY the SOURCES given.
- Every non-empty value must be supported by at least one source id. If nothing supports an answer, leave the value
  empty and say in `note` what the candidate needs to add. Never guess or invent experience, dates or numbers.
- Short text fields: a short answer. Long questions ("Why do you want to work here?"): first person, at most
  120 words, built only from the sources, in the candidate's voice.
- Fields with options: the value must be exactly one of the options, and only if a source clearly says it.
- Yes/no eligibility questions (work authorization, sponsorship, relocation, location) only from the candidate's
  own statements or application details."""


def _match_option(value: str, options: list[str]) -> str | None:
    v = value.strip().lower()
    for o in options:
        if o.strip().lower() == v:
            return o
    for o in options:  # "United States" -> "United States +1" / "United States+1" (country-code pickers)
        if re.match(re.escape(v) + r"(?![a-z])", o.strip().lower()):
            return o
    return None


async def _sources(db: AsyncSession, app: Application, user: User) -> tuple[dict[str, str], dict[str, dict]]:
    """Every thing a value may come from, keyed by a stable id; plus the screening answers by field key."""
    src: dict[str, str] = {}
    for k, v in details_for(user).items():
        if v and k in DETAIL_LABELS:
            src[f"details:{k}"] = f"{DETAIL_LABELS[k]}: {v}"
    for f in await prep_service.user_facts(db, user.id):
        if f.status == "confirmed":
            src[f"fact:{f.id}"] = f"{f.topic}: {f.value}"
    captured = await screening.screening_answers(db, app.prep_id)
    for item_id, c in captured.items():
        src[f"screening:{item_id}"] = f"Asked \"{c['question'][:120]}\" on the screening call, said: {c['answer']}"
    # Everything else they said on the call, word for word, so form questions that weren't on the call's checklist
    # can still be answered from their own words.
    calls = (await db.scalars(select(Interview.id).where(Interview.prep_id == app.prep_id, Interview.kind == "screening",
                                                         Interview.status.in_(("completed", "scored"))))).all()
    if calls:
        turns = (await db.scalars(select(Turn).where(Turn.interview_id.in_(calls), Turn.speaker == "candidate")
                                  .order_by(Turn.interview_id, Turn.seq))).all()
        for tr in turns:
            if len(tr.text) >= 15:
                src[f"said:{tr.interview_id}:{tr.seq}"] = f"Said on the screening call: {tr.text[:600]}"
    plan = await db.scalar(select(PrepPlan).where(PrepPlan.prep_id == app.prep_id).order_by(PrepPlan.version.desc())
                           .limit(1))
    for d in (plan.plan.get("answer_drafts", []) if plan else []):
        if d.get("approved"):
            src[f"draft:{d['question_id']}"] = f"Approved application answer draft: {d['draft']}"
    return src, captured


def _describe_source(sid: str, captured: dict, src: dict[str, str]) -> tuple[str, str]:
    kind = sid.split(":", 1)[0]
    if kind == "said":
        said = src[sid].split(": ", 1)[1]
        return "screening", f"You said on the screening call: \u201c{said[:240]}\u201d"
    if kind == "screening":
        c = captured.get(sid.split(":", 1)[1], {})
        return "screening", f"You said on the screening call: “{c.get('quote', '')}”"
    return {"details": ("details", "Your application details"), "fact": ("fact", "Something you told us"),
            "draft": ("draft", "Your approved draft answer")}.get(kind, ("other", sid))


async def map_values(db: AsyncSession, app: Application) -> None:
    user = await db.get(User, app.owner_id)
    details = details_for(user)
    resume = (user.preferences or {}).get("resume_file")
    values: dict[str, dict] = {}
    questions = []
    src, captured = await _sources(db, app, user)
    for f in app.fields:
        key, kind = f["key"], f["kind"]
        if kind == "contact":
            ck = f.get("contact") or contact_key(f["label"], f.get("name", ""), f.get("autocomplete", ""))
            v = details.get(ck or "", "")
            if ck == "full_name":
                v = " ".join(x for x in (details["first_name"], details["last_name"]) if x)
            if v and f.get("options"):
                v = _match_option(v, f["options"]) or ""
            values[key] = {"value": v, "state": "auto" if v else "missing", "source": "details" if v else "",
                           "detail": "Your application details" if v else "Add it to your application details"}
        elif kind == "file":
            is_resume = re.search(r"resume|cv", f"{f['label']} {key}", re.I)
            if is_resume and resume:
                values[key] = {"value": resume["name"], "state": "auto", "source": "resume", "detail": "Your resume"}
            else:
                values[key] = {"value": "", "state": "missing" if f.get("required") else "empty", "source": "",
                               "detail": "Upload your resume in application details" if is_resume else "Optional"}
        elif kind == "voluntary":
            values[key] = {"value": "", "state": "voluntary", "source": "",
                           "detail": "Voluntary self-identification: we never answer this for you. Choose an answer "
                                     "yourself if you want to (or if the form requires one)."}
        elif kind == "sensitive":
            values[key] = {"value": "", "state": "blocked", "source": "",
                           "detail": "We don't handle this kind of information. Fill it in on the site yourself."}
        elif kind == "consent":
            values[key] = {"value": "", "state": "consent", "source": "", "detail": "Tick it yourself if you agree"}
        else:
            hit = captured.get(f"A:{key}")
            if hit:  # asked this exact question on the screening call
                v = _match_option(hit["answer"], f["options"]) if f.get("options") else hit["answer"]
                if v:
                    values[key] = {"value": v, "state": "auto", "source": "screening",
                                   "detail": f"You said on the screening call: “{hit['quote']}”"}
                    continue
            questions.append(f)
    if questions and src:
        res = await llm.structured(
            FieldAnswers, MAP_SYSTEM,
            "FIELDS:\n" + "\n".join(f"- key={f['key']} | {f['label']}{' (required)' if f.get('required') else ''}"
                                    + (f" | OPTIONS: {' | '.join(f['options'][:40])}" if f.get("options") else "")
                                    for f in questions)
            + "\n\nSOURCES:\n" + llm.untrusted("candidate sources", "\n".join(f"- {k}: {v}" for k, v in src.items())),
            temperature=0, reasoning_effort=get_settings().prep_reasoning_effort)
        by_key = {a.key: a for a in res.answers}
        for f in questions:
            a = by_key.get(f["key"])
            sids = [s for s in (a.sources if a else []) if s in src]
            v = (a.value.strip() if a else "")
            if v and f.get("options"):
                v = _match_option(v, f["options"]) or ""
            if v and sids:
                kind, detail = _describe_source(sids[0], captured, src)
                values[f["key"]] = {"value": v[:4000], "state": "auto", "source": kind, "detail": detail,
                                    "sources": sids}
            else:
                values[f["key"]] = {"value": "", "state": "missing" if f.get("required") else "empty", "source": "",
                                    "detail": (a.note if a and a.note else "Nothing you've told us answers this yet")}
    for f in questions:
        values.setdefault(f["key"], {"value": "", "state": "missing" if f.get("required") else "empty", "source": "",
                                     "detail": "Nothing you've told us answers this yet"})
    app.values = values
    app.status = "ready"
    log_event(app, "values_proposed", f"{sum(1 for v in values.values() if v['value'])} of {len(values)} filled")
    await db.commit()


# ---------------- 3. approve ----------------
class ApproveError(ValueError):
    pass


def apply_edits(app: Application, edits: dict[str, str]) -> dict[str, dict]:
    """The candidate's final values. Their own choices are allowed anywhere except sensitive fields."""
    fields = {f["key"]: f for f in app.fields}
    out = {}
    for key, cur in app.values.items():
        f = fields.get(key)
        if not f:
            continue
        v = edits.get(key, cur["value"])
        v = (v or "").strip()
        if f["kind"] == "sensitive" and v:
            raise ApproveError(f"“{f['label']}” can't be filled through InterviewBud")
        if v and f.get("options"):
            match = _match_option(v, f["options"])
            if not match:
                raise ApproveError(f"“{v}” isn't one of the options for “{f['label']}”")
            v = match
        if f["type"] == "file" and v and v != cur["value"]:
            raise ApproveError("Files come from your uploaded resume")
        changed = v != cur["value"]
        state = ("you" if v else "empty") if changed else cur["state"]
        out[key] = {**cur, "value": v, "state": state, "detail": "You entered this" if changed and v else cur["detail"]}
    return out


def unanswered_required(app: Application, values: dict[str, dict]) -> list[str]:
    return [f["label"] for f in app.fields if f.get("required") and not values.get(f["key"], {}).get("value")]


# ---------------- 4. fill / submit ----------------
def _files(user: User) -> dict:
    r = resume_file(user)
    return {r["name"]: r} if r else {}


def shot_path(app: Application, name: str) -> Path:
    p = DATA_DIR / "applications" / str(app.id)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{name}.png"


async def fill(app_id: UUID) -> None:
    async with SessionLocal() as db:
        app = await db.get(Application, app_id)
        user = await db.get(User, app.owner_id)
        values = {k: v["value"] for k, v in app.approved.items() if v.get("value")}
        try:
            res = await browser.fill_and_capture(app.apply_url, app.fields, values, _files(user))
            shot_path(app, "filled").write_bytes(res["screenshot"])
            failed = {k: v for k, v in res["report"].items() if v != "filled"}
            app.fill_report = {**app.fill_report, "fields": res["report"], "filled_at": now().isoformat(),
                               "screenshot": "filled", "failed": len(failed)}
            app.status = "filled"
            log_event(app, "filled", f"{len(res['report']) - len(failed)} filled, {len(failed)} couldn't be")
        except Exception as e:
            log.exception("filling the form failed")
            app.status, app.error = "failed", f"Couldn't fill the form ({type(e).__name__})"
            log_event(app, "fill_failed", str(e))
        await db.commit()


async def submit(app_id: UUID) -> None:
    async with SessionLocal() as db:
        app = await db.get(Application, app_id)
        user = await db.get(User, app.owner_id)
        values = {k: v["value"] for k, v in app.approved.items() if v.get("value")}
        try:
            res = await browser.submit(app.apply_url, app.fields, values, _files(user),
                                       (app.fill_report or {}).get("submit_button", ""))
            shot_path(app, "submitted").write_bytes(res["screenshot"])
            app.fill_report = {**app.fill_report, "submit_fields": res["report"], "screenshot": "submitted"}
            app.confirmation = res["detail"]
            app.status = res["outcome"]
            if res["outcome"] == "submitted":
                app.submitted_at = now()
            elif res["outcome"] == "failed":
                app.error = res["detail"]
            log_event(app, f"submit_{res['outcome']}", res["detail"])
        except Exception as e:
            log.exception("submitting failed")
            app.status, app.error = "failed", f"Couldn't submit ({type(e).__name__}); nothing was sent"
            log_event(app, "submit_error", str(e))
        await db.commit()

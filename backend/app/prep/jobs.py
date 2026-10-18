"""Similar jobs the candidate could apply to, from real public job boards.

Sources: the posting's own company board (when it's on Greenhouse, Lever or Ashby) plus the boards listed in
`JOB_BOARDS`. Each board's public job list is read (cached for an hour), ranked by title and location against the
target role, and the best few are re-ranked by the model with their descriptions, against the job's requirements
and the candidate's confirmed facts. Every suggestion links to the real posting; nothing is invented.
"""

import asyncio
import html
import logging
import re
import time
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from .. import llm
from ..config import get_settings
from ..db import Opportunity, PrepSession, SessionLocal
from . import service

log = logging.getLogger(__name__)

BOARD_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)")),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})")),
]
STOP = {"and", "or", "the", "of", "for", "to", "a", "an", "in", "at", "with", "-", "&", "/", "|", "i", "ii", "iii",
        "remote", "hybrid", "new", "grad"}
SENIOR = {"senior", "sr", "staff", "principal", "lead", "head", "director", "manager"}
CACHE_S = 3600
_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_running: set[UUID] = set()


def board_of(url: str) -> tuple[str, str, str] | None:
    for ats, pat in BOARD_PATTERNS:
        m = pat.search(url or "")
        if m:
            return ats, m.group(1), m.group(2)
    return None


def boards() -> list[tuple[str, str]]:
    out = []
    for item in get_settings().job_boards.split(","):
        ats, _, token = item.strip().partition(":")
        if ats in ("greenhouse", "lever", "ashby") and re.fullmatch(r"[\w.-]+", token or ""):
            out.append((ats, token))
    return out


def _text(s: str) -> str:
    s = html.unescape(html.unescape(s or ""))
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


async def fetch_board(c: httpx.AsyncClient, ats: str, token: str) -> list[dict]:
    key = (ats, token)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    jobs: list[dict] = []
    try:
        if ats == "greenhouse":
            r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                jobs.append({"ats": ats, "board": token, "id": str(j["id"]), "title": j.get("title", ""),
                             "company": j.get("company_name") or token.title(),
                             "location": (j.get("location") or {}).get("name", ""), "url": j.get("absolute_url", ""),
                             "posted": j.get("first_published") or j.get("updated_at") or ""})
        elif ats == "ashby":
            r = await c.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                if j.get("isListed") is False:
                    continue
                jobs.append({"ats": ats, "board": token, "id": j["id"], "title": j.get("title", ""),
                             "company": token.replace("-", " ").title(),
                             "location": j.get("location", "") + (" (remote)" if j.get("isRemote") else ""),
                             "url": j.get("jobUrl", ""), "posted": j.get("publishedAt", ""),
                             "team": j.get("department") or j.get("team") or "",
                             "description": (j.get("descriptionPlain") or "")[:6000]})
        elif ats == "lever":
            r = await c.get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
            r.raise_for_status()
            for j in r.json():
                cat = j.get("categories") or {}
                desc = (j.get("descriptionPlain") or "") + "\n" + "\n".join(
                    f"{x.get('text', '')}: {_text(x.get('content', ''))}" for x in j.get("lists") or [])
                jobs.append({"ats": ats, "board": token, "id": j["id"], "title": j.get("text", ""),
                             "company": token.replace("-", " ").title(), "location": cat.get("location", ""),
                             "url": j.get("hostedUrl", ""), "team": cat.get("team") or cat.get("department") or "",
                             "posted": "", "description": desc[:6000]})
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("job board %s:%s unavailable: %s", ats, token, e)
        return []
    _cache[key] = (time.time(), jobs)
    return jobs


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#.]+", (s or "").lower()) if w not in STOP and len(w) > 1}


def lexical_rank(target: dict, jobs: list[dict], n: int = 14) -> list[dict]:
    title = _tokens(target["title"])
    skills = {s.lower() for s in target["skills"]}
    loc = _tokens(target["location"])
    senior = bool(title & SENIOR)
    scored = []
    for j in jobs:
        jt = _tokens(j["title"])
        if not jt:
            continue
        overlap = len(title & jt) / max(1, len(title | jt))
        if overlap == 0 and not (jt & skills):
            continue
        s = overlap * 3 + 0.4 * len(jt & skills)
        s += 0.4 if (jt & SENIOR) and senior else (-0.3 if (jt & SENIOR) != (title & SENIOR) and senior else 0)
        s += 0.4 if loc & _tokens(j["location"]) else 0
        s += 0.3 if j.get("same_company") else 0
        scored.append((s, j))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for _, j in scored:
        k = (j["company"].lower(), j["title"].lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
        if len(out) >= n:
            break
    return out


async def _describe(c: httpx.AsyncClient, j: dict) -> None:
    if j.get("description") or j["ats"] != "greenhouse":
        return
    try:
        r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{j['board']}/jobs/{j['id']}")
        r.raise_for_status()
        d = r.json()
        j["description"] = _text(d.get("content", ""))[:6000]
        j["team"] = ", ".join(x.get("name", "") for x in d.get("departments") or [])
    except (httpx.HTTPError, ValueError) as e:
        log.info("no description for %s: %s", j["url"], e)


class Match(BaseModel):
    index: int = Field(description="Index of the job in the CANDIDATE JOBS list")
    fit: str = Field(description="strong | good | stretch")
    why: str = Field(description="One or two sentences tying the job's stated needs to what this candidate has "
                                 "confirmed (cite fact topics or the target job's requirement ids)")
    gap: str = Field(description="The main thing they'd need to show or learn for it, or empty")


class Matches(BaseModel):
    matches: list[Match] = Field(description="Up to 8 best matches, best first; leave out poor fits")


MATCH_SYSTEM = """You suggest other real job openings for a candidate preparing for a target job.
- Judge fit from each job's title and description against the target job's requirements and the candidate's
  CONFIRMED facts only. Never invent experience or job details; if a description is missing, judge on the title and
  team and say less.
- Prefer roles at a similar level. Leave out jobs that clearly don't fit (different field, far more senior)."""


async def generate(prep_id: UUID) -> None:
    if prep_id in _running:
        return
    _running.add(prep_id)
    try:
        async with SessionLocal() as db:
            prep = await db.get(PrepSession, prep_id)
            opp = await db.get(Opportunity, prep.opportunity_id)
            prep.similar_jobs = {**(prep.similar_jobs or {}), "status": "working"}
            await db.commit()
            ex = opp.extraction or {}
            target = {"title": ex.get("title") or opp.title, "location": ex.get("location", ""),
                      "skills": [s["text"] for s in ex.get("skills", [])]}
            own = board_of(opp.final_url or opp.source_url)
            sources = list(dict.fromkeys(([own[:2]] if own else []) + boards()))
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "InterviewBud/1.0"}) as c:
                lists = await asyncio.gather(*(fetch_board(c, a, t) for a, t in sources))
                pool = []
                for (a, t), jobs in zip(sources, lists):
                    for j in jobs:
                        if own and j["ats"] == own[0] and j["board"] == own[1] and j["id"] == own[2]:
                            continue  # the job itself
                        pool.append({**j, "same_company": bool(own and (a, t) == own[:2])})
                short = lexical_rank(target, pool)
                await asyncio.gather(*(_describe(c, j) for j in short))
            facts = [f for f in await service.user_facts(db, prep.owner_id) if f.status == "confirmed"]
            items: list[dict] = []
            if short:
                res = await llm.structured(
                    Matches, MATCH_SYSTEM,
                    f"TARGET JOB: {target['title']} at {ex.get('company')} ({target['location']})\nREQUIREMENTS:\n"
                    + "\n".join(f"- {r['id']}: {r['text']}" for r in ex.get("requirements", []))
                    + "\n\nCANDIDATE CONFIRMED FACTS:\n"
                    + llm.untrusted("candidate facts", "\n".join(f"- {f.topic}: {f.value}" for f in facts) or "(none)")
                    + "\n\nCANDIDATE JOBS:\n" + llm.untrusted("job board listings", "\n\n".join(
                        f"[{i}] {j['title']} | {j['company']} | {j['location']} | {j.get('team', '')}\n"
                        f"{(j.get('description') or '(no description)')[:1500]}" for i, j in enumerate(short))),
                    temperature=0, reasoning_effort=get_settings().prep_reasoning_effort)
                for m in res.matches[:8]:
                    if 0 <= m.index < len(short):
                        j = short[m.index]
                        items.append({k: j.get(k, "") for k in ("title", "company", "location", "url", "team", "ats",
                                                                "posted")}
                                     | {"fit": m.fit if m.fit in ("strong", "good", "stretch") else "good",
                                        "why": m.why, "gap": m.gap, "same_company": j.get("same_company", False)})
            prep.similar_jobs = {"status": "ready", "generated_at": service.now().isoformat(), "items": items,
                                 "searched": [f"{a}:{t}" for a, t in sources], "pool": len(pool)}
            await db.commit()
    except Exception as e:
        log.exception("similar jobs failed")
        async with SessionLocal() as db:
            prep = await db.get(PrepSession, prep_id)
            prep.similar_jobs = {**(prep.similar_jobs or {}), "status": "failed", "error": f"{type(e).__name__}"}
            await db.commit()
    finally:
        _running.discard(prep_id)


def running(prep_id: UUID) -> bool:
    return prep_id in _running

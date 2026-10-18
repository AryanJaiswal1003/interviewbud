"""Preparation journey: grounding, URL safety, ownership, persistence of corrections, practice isolation, recovery.

Model calls are kept out (spawned background steps are captured, not run); the model-driven steps were exercised
end to end against the live API with a real posting (see docs/prep-journey-test-log.md)."""

import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app import auth
from app.db import Interview, PrepPlan, ProfileFactRevision, SessionLocal, User
from app.main import app
from app.prep import fetcher, service

PW = "correct horse battery"
JOB = ("Senior Backend Engineer at Example Co. You will design APIs and own services end to end. "
       "Requirements: 5+ years of Python; experience with Postgres; you have shipped distributed systems. "
       "Nice to have: Kafka. ") * 3


@pytest.fixture(autouse=True)
async def _clean(monkeypatch):
    for t in (auth.login_throttle, auth.signup_throttle, auth.create_throttle, auth.analytics_throttle):
        t.hits.clear()
    spawned = []
    monkeypatch.setattr(service, "spawn", lambda coro: (spawned.append(coro.__qualname__), coro.close()))
    yield spawned
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.email.like("preptest-%@example.com")))
        await db.commit()


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def signup(c) -> str:
    r = await c.post("/api/auth/signup", json={"email": f"preptest-{uuid.uuid4().hex[:8]}@example.com", "password": PW})
    return r.json()["id"]


def test_quotes_must_really_be_in_the_posting():
    page = "## Requirements\n* **5+ years** of Python\n* Experience with “Postgres” — at scale"
    assert service.verify("5+ years of Python", page)  # markdown decoration ignored
    assert service.verify('Experience with "Postgres" - at scale', page)  # smart quotes and dashes
    assert not service.verify("10+ years of Rust", page)
    assert not service.verify("Python", page)  # too short to count as evidence


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
                                 "http://10.1.2.3/", "http://192.168.0.1/", "https://user:pw@example.com/",
                                 "http://localhost:5432/", "ftp://example.com/"])
async def test_internal_and_unsafe_urls_are_refused(url):
    with pytest.raises(fetcher.FetchRefused):
        await fetcher.safe_get(url)


async def test_preparations_are_private_and_recover_after_refresh(_clean):
    async with client() as a, client() as b:
        await signup(a)
        await signup(b)
        r = await a.post("/api/prep", json={"text": JOB})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        assert "run_intake" in _clean[-1]  # extraction runs in the background
        # "Refresh": a new request reads the same saved state.
        d = (await a.get(f"/api/prep/{pid}")).json()
        assert d["status"] == "intake" and d["opportunity"]["fetch_method"] == "pasted"
        assert (await b.get(f"/api/prep/{pid}")).status_code == 404
        assert (await b.post(f"/api/prep/{pid}/text", json={"text": JOB})).status_code == 404
        assert pid not in [x["id"] for x in (await b.get("/api/prep")).json()]
        assert (await a.post("/api/prep", json={"url": "http://10.0.0.5/jobs/1"})).status_code == 400


async def test_corrections_persist_with_history_and_stay_private():
    async with client() as a, client() as b:
        await signup(a)
        await signup(b)
        f = (await a.post("/api/profile/facts", json={"topic": "Python", "value": "4 years"})).json()
        assert f["status"] == "confirmed"
        r = await a.patch(f"/api/profile/facts/{f['id']}", json={"value": "5 years, mostly FastAPI"})
        assert r.json()["value"] == "5 years, mostly FastAPI"
        revs = (await a.get(f"/api/profile/facts/{f['id']}/revisions")).json()
        assert revs[-1]["old_value"] == "4 years" and revs[-1]["new_value"] == "5 years, mostly FastAPI"
        assert (await b.patch(f"/api/profile/facts/{f['id']}", json={"value": "x"})).status_code == 404
        await a.patch(f"/api/profile/facts/{f['id']}", json={"status": "retracted"})
        assert f["id"] not in [x["id"] for x in (await a.get("/api/profile/facts")).json()]
        async with SessionLocal() as db:
            n = len((await db.scalars(select(ProfileFactRevision).where(ProfileFactRevision.fact_id == f["id"]))).all())
        assert n == 2


async def test_practice_needs_a_plan_and_never_overwrites_earlier_practice():
    async with client() as a:
        await signup(a)
        pid = (await a.post("/api/prep", json={"text": JOB})).json()["id"]
        assert (await a.post(f"/api/prep/{pid}/practice", json={})).status_code == 409
        async with SessionLocal() as db:
            db.add(PrepPlan(prep_id=uuid.UUID(pid), version=1, plan={
                "summary": "s", "topics": [], "skills_to_assess": [], "question_types": [], "include_coding": False,
                "coding_focus": [], "seniority": "senior", "gaps_to_practice": [], "answer_drafts": []}))
            await db.commit()
        i1 = (await a.post(f"/api/prep/{pid}/practice", json={"duration_min": 15})).json()["interview_id"]
        async with SessionLocal() as db:
            iv = await db.get(Interview, uuid.UUID(i1))
            iv.status, iv.scorecard = "scored", {"recommendation": "hire", "dimensions": []}
            await db.commit()
        i2 = (await a.post(f"/api/prep/{pid}/practice", json={"duration_min": 15})).json()["interview_id"]
        d = (await a.get(f"/api/prep/{pid}")).json()
        by_id = {p["id"]: p for p in d["practices"]}
        assert set(by_id) == {i1, i2} and by_id[i1]["recommendation"] == "hire" and by_id[i2]["status"] == "created"
        async with SessionLocal() as db:
            assert "NO CODING ROUND" in (await db.get(Interview, uuid.UUID(i2))).focus


async def test_interrupted_scoring_resumes_on_startup(monkeypatch):
    from app import main
    from app.evaluation import scorecard
    async with client() as a:
        uid = await signup(a)
    async with SessionLocal() as db:
        from app.db import Candidate
        cand = Candidate(resume_filename="x", resume_text="x")
        db.add(cand)
        await db.flush()
        iv = Interview(owner_id=uuid.UUID(uid), candidate_id=cand.id, role="QA", status="completed")
        db.add(iv)
        await db.commit()
        iid = iv.id
    resumed = await main.resume_interrupted_work()
    assert resumed.get("score", 0) >= 1
    async with SessionLocal() as db:
        await db.execute(delete(Interview).where(Interview.id == iid))
        await db.commit()
    assert scorecard  # finalize itself is covered elsewhere; here we check the stuck interview gets picked up

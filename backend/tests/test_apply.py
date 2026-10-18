"""Applying on the candidate's behalf, end to end in a real browser against a local test form (never a real
employer): read the form, propose values with sources, approve, fill with a screenshot, submit only on
confirmation. Also: voluntary self-ID is never answered for them, sensitive fields are refused, the browser can't
reach private addresses, and submitting needs a fresh preview of the approved values."""

import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from sqlalchemy import delete, select

from app import auth
from app.api import apply as apply_api
from app.apply import browser
from app.apply import service as apply
from app.config import get_settings
from app.db import Application, Candidate, Interview, Opportunity, PrepSession, SessionLocal, User
from app.main import app
from app.prep import service

FORM = b"""<!doctype html><html><head><title>Apply: Backend Engineer</title></head><body>
<h1>Backend Engineer</h1>
<form method="post" action="/submit" enctype="multipart/form-data">
  <label for="first_name">First Name*</label><input id="first_name" name="first_name" required>
  <label for="last_name">Last Name*</label><input id="last_name" name="last_name" required>
  <label for="email">Email*</label><input id="email" name="email" type="email" required>
  <label for="phone">Phone</label><input id="phone" name="phone" type="tel">
  <label for="resume">Resume/CV*</label><input id="resume" name="resume" type="file" required>
  <label for="why">Why do you want to work here?*</label><textarea id="why" name="why" required></textarea>
  <label for="auth">Are you legally authorized to work in the United States?*</label>
  <select id="auth" name="auth" required><option value="">Select...</option><option>Yes</option><option>No</option></select>
  <fieldset><legend>Are you willing to relocate?</legend>
    <input type="radio" name="relocate" id="r1" value="yes"><label for="r1">Yes</label>
    <input type="radio" name="relocate" id="r2" value="no"><label for="r2">No</label></fieldset>
  <section><h3>Voluntary Self-Identification</h3>
    <label for="gender">Gender</label>
    <select id="gender" name="gender"><option value="">Select...</option><option>Male</option><option>Female</option>
    <option>I don't wish to answer</option></select></section>
  <label for="ssn">Social security number</label><input id="ssn" name="ssn">
  <input type="checkbox" id="agree" name="agree"><label for="agree">I agree to the privacy policy</label>
  <button type="submit">Submit application</button>
</form></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    posts: list[bytes] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(FORM)

    def do_POST(self):
        _Handler.posts.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Thank you for applying!</h1>")


@pytest.fixture
def form_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Handler.posts = []
    yield f"http://127.0.0.1:{srv.server_port}/apply"
    srv.shutdown()


@pytest.fixture(autouse=True)
async def _clean(monkeypatch):
    for t in (auth.login_throttle, auth.signup_throttle, auth.create_throttle, apply_api.fill_throttle):
        t.hits.clear()
    spawned = []
    monkeypatch.setattr(service, "spawn", lambda coro: coro.close())
    monkeypatch.setattr(apply, "spawn", lambda coro: (spawned.append(coro.__qualname__), coro.close()))
    yield spawned
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.display_name.like("applytest-%")))
        await db.commit()


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_the_browser_cant_reach_private_addresses(form_url, monkeypatch):
    monkeypatch.setattr(get_settings(), "apply_allow_private", False)
    with pytest.raises(Exception):
        await browser.read_form(form_url)  # 127.0.0.1: blocked before the request leaves the browser


async def test_apply_end_to_end(form_url, monkeypatch, _clean):
    monkeypatch.setattr(get_settings(), "apply_allow_private", True)
    async with client() as c:
        email = f"applytest-{uuid.uuid4().hex[:8]}@example.com"
        uid = (await c.post("/api/auth/start", json={"name": "applytest-Jordan Rivera"})).json()["id"]
        r = await c.put("/api/profile/application", json={"details": {"first_name": "Jordan", "last_name": "Rivera",
                                                                       "email": email, "phone": "+1 555 0100"}})
        assert r.status_code == 200, r.text
        r = await c.post("/api/profile/application/resume",
                         files={"file": ("Jordan Rivera CV.pdf", b"%PDF-1.4 test resume", "application/pdf")})
        assert r.status_code == 200, r.text

        # A preparation whose job lives at the local test form, and a finished screening call in which the
        # candidate answered the work-authorization question.
        async with SessionLocal() as db:
            opp = Opportunity(owner_id=uuid.UUID(uid), source_url=form_url, final_url=form_url, fetch_status="ok",
                              source_text="x", extraction={"title": "Backend Engineer", "company": "Example"})
            db.add(opp)
            await db.flush()
            prep = PrepSession(owner_id=uuid.UUID(uid), opportunity_id=opp.id, status="context")
            db.add(prep)
            cand = Candidate(name="Jordan Rivera")
            db.add(cand)
            await db.flush()
            db.add(Interview(owner_id=uuid.UUID(uid), prep_id=prep.id, kind="screening", candidate_id=cand.id,
                             role="Backend Engineer", status="scored",
                             plan={"items": [{"id": "A:auth", "kind": "application", "ask": "Authorized in the US?"}]},
                             scorecard={"captured": {"A:auth": {"answer": "Yes", "quote": "yes, I'm a US citizen"}}}))
            await db.commit()
            pid = prep.id

        r = await c.post(f"/api/prep/{pid}/application")
        assert r.status_code == 200, r.text
        aid = r.json()["id"]
        assert _clean == ["read"]
        await apply.read(uuid.UUID(aid))  # real browser, real form
        v = (await c.get(f"/api/prep/{pid}/application")).json()
        assert v["status"] == "ready", v
        kinds = {f["key"]: f["kind"] for f in v["fields"]}
        assert kinds == {"first_name": "contact", "last_name": "contact", "email": "contact", "phone": "contact",
                         "resume": "file", "why": "question", "auth": "question", "relocate": "question",
                         "gender": "voluntary", "ssn": "sensitive", "agree": "consent"}
        vals = v["values"]
        assert vals["first_name"]["value"] == "Jordan" and vals["first_name"]["source"] == "details"
        assert vals["resume"]["value"] == "Jordan Rivera CV.pdf"
        assert vals["auth"] == {**vals["auth"], "value": "Yes", "source": "screening"}
        assert "US citizen" in vals["auth"]["detail"]  # shows the candidate's own words
        assert vals["gender"]["value"] == "" and vals["gender"]["state"] == "voluntary"
        assert vals["ssn"]["state"] == "blocked" and vals["agree"]["value"] == ""
        assert vals["why"]["state"] == "missing"  # nothing they've said answers it: left for them, not invented

        # Sensitive fields can't be filled even by the candidate through us; options must be real options.
        assert (await c.post(f"/api/applications/{aid}/approve", json={"values": {"ssn": "123-45-6789"}})).status_code == 400
        assert (await c.post(f"/api/applications/{aid}/approve", json={"values": {"auth": "Maybe"}})).status_code == 400
        assert (await c.post(f"/api/applications/{aid}/submit", json={"confirm": True})).status_code == 409
        edits = {"why": "I like building reliable APIs.", "relocate": "Yes", "agree": "Yes"}
        r = await c.post(f"/api/applications/{aid}/approve", json={"values": edits})
        assert r.status_code == 200, r.text
        assert r.json()["values"]["why"]["state"] == "you" and r.json()["missing_required"] == []
        assert (await c.post(f"/api/applications/{aid}/submit", json={"confirm": True})).status_code == 409  # no preview

        assert (await c.post(f"/api/applications/{aid}/fill")).status_code == 200
        await apply.fill(uuid.UUID(aid))
        v = (await c.get(f"/api/prep/{pid}/application")).json()
        assert v["status"] == "filled" and set(v["fill_fields"].values()) == {"filled"}, v["fill_fields"]
        shot = await c.get(f"/api/applications/{aid}/screenshot/filled")
        assert shot.status_code == 200 and shot.content[:4] == b"\x89PNG"
        assert _Handler.posts == []  # filling never sends anything

        assert (await c.post(f"/api/applications/{aid}/submit", json={"confirm": False})).status_code == 400
        assert (await c.post(f"/api/applications/{aid}/submit", json={"confirm": True})).status_code == 200
        await apply.submit(uuid.UUID(aid))
        v = (await c.get(f"/api/prep/{pid}/application")).json()
        assert v["status"] == "submitted", v
        assert v["submitted_at"] and "Thank you for applying" in v["confirmation"]
        sent = _Handler.posts[0]
        for expected in (b"Jordan", b"Rivera", email.encode(), b"I like building reliable APIs.", b"%PDF-1.4 test"):
            assert expected in sent
        assert b'name="gender"\r\n\r\n\r\n' in sent and b'name="ssn"\r\n\r\n\r\n' in sent  # left empty
        assert [h["event"] for h in v["history"]][-2:] == ["submit_requested", "submit_submitted"]

        # Other accounts can't see or drive it.
        async with client() as b:
            await b.post("/api/auth/start", json={"name": f"applytest-{uuid.uuid4().hex[:8]}"})
            assert (await b.get(f"/api/prep/{pid}/application")).status_code == 404
            assert (await b.post(f"/api/applications/{aid}/submit", json={"confirm": True})).status_code == 404
            assert (await b.get(f"/api/applications/{aid}/screenshot/filled")).status_code == 404


async def test_changing_answers_after_the_preview_needs_a_new_preview(form_url, monkeypatch):
    monkeypatch.setattr(get_settings(), "apply_allow_private", True)
    async with client() as c:
        email = f"applytest-{uuid.uuid4().hex[:8]}@example.com"
        uid = uuid.UUID((await c.post("/api/auth/start", json={"name": "applytest-other"})).json()["id"])
        async with SessionLocal() as db:
            opp = Opportunity(owner_id=uid, source_url=form_url, fetch_status="ok", extraction={})
            db.add(opp)
            await db.flush()
            prep = PrepSession(owner_id=uid, opportunity_id=opp.id, status="context")
            db.add(prep)
            await db.flush()
            fields = [{"key": "why", "label": "Why?", "type": "textarea", "kind": "question", "required": False,
                       "selector": "#why"}]
            app_row = Application(owner_id=uid, prep_id=prep.id, apply_url=form_url, status="filled", fields=fields,
                                  values={"why": {"value": "a", "state": "you", "source": "", "detail": ""}},
                                  approved={"why": {"value": "a", "state": "you", "source": "", "detail": ""}},
                                  approved_at=apply.now(), fill_report={"filled_at": apply.now().isoformat(),
                                                                        "fields": {"why": "filled"}})
            db.add(app_row)
            await db.commit()
            aid = app_row.id
        assert (await c.post(f"/api/applications/{aid}/approve", json={"values": {"why": "b"}})).status_code == 200
        async with SessionLocal() as db:  # pretend a fill happened before that approval
            row = await db.get(Application, aid)
            row.status = "filled"
            await db.commit()
        r = await c.post(f"/api/applications/{aid}/submit", json={"confirm": True})
        assert r.status_code == 409 and "preview" in r.json()["detail"]
        async with SessionLocal() as db:
            assert (await db.scalar(select(Application).where(Application.id == aid))).status == "filled"


def test_option_matching_is_exact_or_a_clean_prefix():
    m = apply._match_option
    countries = ["Afghanistan+93", "United States+1", "United States Minor Outlying Islands+1"]
    assert m("united states", countries) == "United States+1"  # dialling-code pickers, with or without a space
    assert m("United States", ["United States +1"]) == "United States +1"
    assert m("No", ["Norway+47", "No"]) == "No" and m("No", ["Norway+47"]) is None
    assert m("Unit", countries) is None

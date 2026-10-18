"""Running candidate code against a problem's tests, and validating agent-injected tests."""

import json
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import CodeRun, Problem, ProblemTest
from . import asserts, sandbox

VISIBLE_KINDS = {"sample", "injected"}


def _spec(t: ProblemTest) -> dict:
    return json.loads(t.assertion)


def _expected_expr(t: ProblemTest) -> str:
    spec = _spec(t)
    return spec.get("expected_literal") or spec["expected_expr"]


async def ensure_expected(db: AsyncSession, problem: Problem, tests: list[ProblemTest]) -> None:
    """Fill ProblemTest.expected_json by evaluating the dataset's expected expressions in a run that contains no
    candidate code (just the problem's preamble). Cached, so each problem pays this once."""
    todo = [t for t in tests if t.expected_json is None]
    if not todo:
        return
    raw = await sandbox.execute({"mode": "eval", "preamble": problem.preamble, "code": "", "entry_point": "None",
                                 "exprs": [_expected_expr(t) for t in todo], "per_case_timeout": 3.0},
                                timeout_s=60)
    if raw.get("compile_error"):
        raise RuntimeError(f"problem preamble failed to load: {raw['compile_error']}")
    for t, v in zip(todo, raw.get("value") or []):
        t.expected_json = {"v": v["value"]} if v.get("ok") else {"err": v.get("error", "")}
    await db.commit()


def _short(v, limit=300) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + f"... ({len(s)} chars)"


def grade(r: dict, t: ProblemTest) -> dict:
    """Host-side verdict for one harness result. The harness only reports what the code returned."""
    exp = (t.expected_json or {}).get("v")
    status = r["status"]
    if status == "ok":
        status = "pass" if r.get("value") == exp else "fail"
    return {"id": r["id"], "status": status, "ms": r.get("ms", 0),
            "actual": _short(r.get("value")) if status in ("pass", "fail") else None,
            "expected": _short(exp), "stdout": r.get("stdout", ""), "error": r.get("error", "")}


async def tests_for(db: AsyncSession, problem_id: int, interview_id: UUID | None) -> list[ProblemTest]:
    q = select(ProblemTest).where(ProblemTest.problem_id == problem_id)
    if interview_id:
        q = q.where(or_(ProblemTest.interview_id.is_(None), ProblemTest.interview_id == interview_id))
    else:
        q = q.where(ProblemTest.interview_id.is_(None))
    return list((await db.scalars(q.order_by(ProblemTest.kind, ProblemTest.ordinal))).all())


async def run_code(
    db: AsyncSession,
    *,
    interview_id: UUID,
    problem: Problem,
    code: str,
    mode: str,  # run -> sample + injected ; submit -> everything
) -> CodeRun:
    tests = await tests_for(db, problem.id, interview_id)
    if mode == "run":
        tests = [t for t in tests if t.kind in VISIBLE_KINDS]
    # Visible tests run first, so nothing the code sees in a hidden test can surface in a visible test's output.
    order = {"sample": 0, "injected": 1, "hidden": 2, "hard": 3}
    tests.sort(key=lambda t: (order.get(t.kind, 9), t.ordinal))
    await ensure_expected(db, problem, tests)
    tests = [t for t in tests if "v" in (t.expected_json or {})]
    payload = {
        "preamble": problem.preamble,
        "code": code,
        "entry_point": problem.entry_point,
        # Inputs only: expected answers never enter the sandbox.
        "tests": [{"id": t.id, "actual_expr": _spec(t)["actual_expr"]} for t in tests],
        "per_case_timeout": 1.5,
    }
    raw = await sandbox.execute(payload, timeout_s=25 if mode == "submit" else 12)
    by_id = {t.id: t for t in tests}
    results = []
    for r in raw.get("results", []):
        t = by_id.get(r.get("id"))
        if t is None:
            continue  # an id we never sent: forged output
        results.append({**grade(r, t), "kind": t.kind, "input": t.call_args[:600], "rationale": t.rationale})
    summary = summarize(results, raw)
    run = CodeRun(
        interview_id=interview_id, problem_id=problem.id, code=code, mode=mode, summary=summary, results=results
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


def summarize(results: list[dict], raw: dict) -> dict:
    by_kind: dict[str, dict] = {}
    for r in results:
        k = by_kind.setdefault(r["kind"], {"passed": 0, "total": 0})
        k["total"] += 1
        k["passed"] += r["status"] == "pass"
    statuses = [r["status"] for r in results]
    first_fail = next((r for r in results if r["status"] != "pass"), None)
    return {
        "compile_error": raw.get("compile_error"),
        "killed": raw.get("error") if raw.get("killed") or raw.get("tampered") else None,
        "tampered": bool(raw.get("tampered")),
        "passed": statuses.count("pass"),
        "total": len(results),
        "timeouts": statuses.count("timeout"),
        "errors": statuses.count("error"),
        "by_kind": by_kind,
        "max_ms": max((r["ms"] for r in results), default=0),
        "first_failure": first_fail,
    }


def candidate_view(run: CodeRun) -> dict:
    """What the candidate sees: full detail for visible tests, only pass/fail counts for hidden/hard."""
    visible = [r for r in run.results if r["kind"] in VISIBLE_KINDS]
    return {
        "id": run.id,
        "mode": run.mode,
        "summary": {k: v for k, v in run.summary.items() if k != "first_failure"},
        "visible": visible,
        "hidden": [{"id": r["id"], "kind": r["kind"], "status": r["status"], "ms": r["ms"]}
                   for r in run.results if r["kind"] not in VISIBLE_KINDS],
    }


async def inject_test(
    db: AsyncSession, *, interview_id: UUID, problem: Problem, call_args: str, rationale: str
) -> ProblemTest:
    """Agent-authored test: expected output is computed by the dataset's reference solution, never guessed."""
    call = asserts.build_call(call_args, problem.starter_code)
    raw = await sandbox.execute(
        {"mode": "eval", "preamble": problem.preamble, "code": problem.reference_solution,
         "entry_point": problem.entry_point, "exprs": [call], "per_case_timeout": 3.0},
        timeout_s=12,
    )
    if raw.get("compile_error"):
        raise ValueError(f"reference solution failed to load: {raw['compile_error']}")
    res = (raw.get("value") or [{}])[0]
    if not res.get("ok"):
        raise ValueError(f"input rejected by reference solution: {res.get('error')}")
    existing = await tests_for(db, problem.id, interview_id)
    test = ProblemTest(
        problem_id=problem.id,
        kind="injected",
        ordinal=1000 + sum(t.kind == "injected" for t in existing),
        call_args=call_args,
        assertion=json.dumps({"actual_expr": call, "expected_expr": json.dumps(res["value"]), "cmp": "eq"}),
        expected_repr=json.dumps(res["value"])[:2000],
        expected_json={"v": res["value"]},
        interview_id=interview_id,
        rationale=rationale,
    )
    db.add(test)
    await db.commit()
    await db.refresh(test)
    return test

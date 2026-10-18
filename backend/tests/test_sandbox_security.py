"""Cheating and isolation attacks against the real sandbox (Docker) and the real problem bank.

Each test is an attack a motivated candidate could type into the editor. All of them must FAIL to help the candidate.
Problem 1 is Two Sum.
"""

import uuid

import pytest
from sqlalchemy import delete

from app.coding import sandbox, service
from app.db import Candidate, CodeRun, Interview, Problem, SessionLocal


@pytest.fixture
async def ctx():
    async with SessionLocal() as db:
        problem = await db.get(Problem, 1)
        cand = Candidate(resume_filename="x", resume_text="x")
        db.add(cand)
        await db.flush()
        iv = Interview(candidate_id=cand.id, role="QA", status="live", problem_id=1)
        db.add(iv)
        await db.commit()
        yield db, problem, iv.id
        await db.execute(delete(CodeRun).where(CodeRun.interview_id == iv.id))
        await db.execute(delete(Interview).where(Interview.id == iv.id))
        await db.execute(delete(Candidate).where(Candidate.id == cand.id))
        await db.commit()


async def submit(db, problem, iid, code):
    run = await service.run_code(db, interview_id=iid, problem=problem, code=code, mode="submit")
    return run.summary


async def test_correct_solution_still_passes(ctx):
    db, problem, iid = ctx
    s = await submit(db, problem, iid, problem.reference_solution)
    assert s["passed"] == s["total"] > 10


async def test_always_equal_object_does_not_pass(ctx):
    db, problem, iid = ctx
    code = """
class Solution:
    def twoSum(self, nums, target):
        class Anything(list):
            def __eq__(self, other): return True
            def __ne__(self, other): return False
        return Anything()
"""
    s = await submit(db, problem, iid, code)
    assert s["passed"] == 0, s["by_kind"]


async def test_reading_expected_answers_from_memory_does_not_pass(ctx):
    db, problem, iid = ctx
    # Walk the interpreter's objects for the test list and answer from the expected values.
    code = """
import gc, ast
class Solution:
    def twoSum(self, nums, target):
        for o in gc.get_objects():
            if isinstance(o, dict) and "tests" in o and isinstance(o["tests"], list):
                for t in o["tests"]:
                    if repr(nums) in t.get("actual_expr", "") and "expected_expr" in t:
                        return eval(t["expected_expr"])
        return []
"""
    s = await submit(db, problem, iid, code)
    assert s["by_kind"]["hidden"]["passed"] == 0, s["by_kind"]


async def test_forged_result_block_is_rejected(ctx):
    db, problem, iid = ctx
    # Print a fake "everything passed" block after the harness's own, at interpreter exit.
    code = """
import atexit, sys, gc, json
def forge():
    payload = next(o for o in gc.get_objects() if isinstance(o, dict) and "tests" in o)
    res = [{"id": t["id"], "status": "pass", "ms": 1, "actual": "x", "expected": "x", "value": None,
            "stdout": "", "error": ""} for t in payload["tests"]]
    marker = payload.get("marker", "__IB_RESULT__")
    sys.stdout.write("\\n" + marker + json.dumps({"compile_error": None, "results": res, "value": None}) + "\\n")
atexit.register(forge)
class Solution:
    def twoSum(self, nums, target):
        return []
"""
    s = await submit(db, problem, iid, code)
    assert s["passed"] < s["total"] // 2 or s.get("tampered"), s


async def test_nothing_survives_between_runs(ctx):
    db, problem, iid = ctx
    plant = """
import os
open("/tmp/stash", "w").write("hidden inputs")
if os.fork() == 0:
    os.setsid()
    import time
    time.sleep(30)
class Solution:
    def twoSum(self, nums, target):
        return [0, 1]
"""
    await submit(db, problem, iid, plant)
    probe = """
import os
class Solution:
    def twoSum(self, nums, target):
        stash = os.path.exists("/tmp/stash")
        others = [p for p in os.listdir("/proc") if p.isdigit() and int(p) not in (1, os.getpid(), os.getppid())]
        raise RuntimeError(f"stash={stash} others={len(others)}")
"""
    run = await service.run_code(db, interview_id=iid, problem=problem, code=probe, mode="run")
    err = run.results[0]["error"]
    assert "stash=False" in err and "others=0" in err, err


async def test_container_has_no_network():
    out = await sandbox.execute({"mode": "eval", "preamble": "", "code": "", "entry_point": "None",
                                 "exprs": ["__import__('socket').create_connection(('1.1.1.1', 53), 2)"]})
    assert out["value"][0]["ok"] is False

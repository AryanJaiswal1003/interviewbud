"""Ingest newfacade/LeetCodeDataset (Apache-2.0) into Postgres.

Each problem's `check(candidate)` asserts are split into:
  * sample  - asserts whose arguments match the description's worked examples (visible to the candidate)
  * hard    - the largest-input asserts (stress / boundary cases)
  * hidden  - everything else
"""

import asyncio
import json
import logging
from pathlib import Path

import httpx
from sqlalchemy import delete, func, select

from ..db import Problem, ProblemTest, SessionLocal, init_db
from . import asserts

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FILES = {
    "train": "LeetCodeDataset-train.jsonl",
    "test": "LeetCodeDataset-test.jsonl",
}
BASE_URL = "https://huggingface.co/datasets/newfacade/LeetCodeDataset/resolve/main/"
HARD_COUNT = 5
INITIAL_ELO = {"Easy": 1200.0, "Medium": 1500.0, "Hard": 1800.0}


def ensure_files() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for name in FILES.values():
        path = DATA_DIR / name
        if path.exists() and path.stat().st_size > 0:
            continue
        log.info("downloading %s", name)
        with httpx.stream("GET", BASE_URL + name, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with path.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)


def classify_tests(row: dict) -> tuple[list[dict], list[dict]]:
    """Return (examples, tests) where tests are dicts ready for ProblemTest."""
    parsed = asserts.parse_check(row["test"])
    examples = asserts.parse_examples(row["problem_description"])
    example_keys = {asserts.example_args_key(e["input"]) for e in examples} - {None}

    sample_idx = [i for i, p in enumerate(parsed) if p.args_key and p.args_key in example_keys]
    if len(sample_idx) < 2:
        # Fall back to the smallest asserts so the candidate always has something visible to run.
        by_size = sorted(range(len(parsed)), key=lambda i: len(parsed[i].source))
        for i in by_size:
            if i not in sample_idx:
                sample_idx.append(i)
            if len(sample_idx) >= 2:
                break
    rest = [i for i in range(len(parsed)) if i not in sample_idx]
    hard_idx = set(sorted(rest, key=lambda i: len(parsed[i].source), reverse=True)[:HARD_COUNT])

    tests = []
    for i, p in enumerate(parsed):
        kind = "sample" if i in sample_idx else "hard" if i in hard_idx else "hidden"
        tests.append(
            {
                "kind": kind,
                "ordinal": i,
                "call_args": p.call_args,
                "assertion": json.dumps(
                    {"actual_expr": p.actual_expr, "expected_expr": p.expected_expr, "cmp": p.cmp}
                ),
                "expected_repr": p.expected_expr[:2000],
            }
        )
    return examples, tests


async def ingest(force: bool = False) -> int:
    await init_db()
    async with SessionLocal() as db:
        count = await db.scalar(select(func.count()).select_from(Problem))
        if count and not force:
            return count
        await db.execute(delete(ProblemTest).where(ProblemTest.interview_id.is_(None)))
        await db.execute(delete(Problem))
        await db.commit()

    ensure_files()
    total = 0
    for split, name in FILES.items():
        batch_p, batch_t = [], []
        with (DATA_DIR / name).open() as f:
            for line in f:
                row = json.loads(line)
                try:
                    examples, tests = classify_tests(row)
                except Exception as e:  # malformed test source: skip rather than invent tests
                    log.warning("skip %s: %s", row.get("task_id"), e)
                    continue
                desc = row["problem_description"]
                batch_p.append(
                    Problem(
                        id=row["question_id"],
                        slug=row["task_id"],
                        title=asserts.title_from_slug(row["task_id"]),
                        difficulty=row["difficulty"],
                        tags=row["tags"],
                        description=desc,
                        constraints=asserts.parse_constraints(desc),
                        examples=examples,
                        starter_code=row["starter_code"],
                        preamble=row["prompt"],
                        entry_point=row["entry_point"],
                        reference_solution=row["completion"],
                        estimated_date=row.get("estimated_date", ""),
                        split=split,
                        elo=INITIAL_ELO.get(row["difficulty"], 1500.0),
                    )
                )
                batch_t.extend((row["question_id"], t) for t in tests)
        async with SessionLocal() as db:
            db.add_all(batch_p)
            await db.flush()
            db.add_all(ProblemTest(problem_id=pid, **t) for pid, t in batch_t)
            await db.commit()
        total += len(batch_p)
        log.info("ingested %d problems from %s", len(batch_p), split)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(ingest(force=True)))

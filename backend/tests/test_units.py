import json
from pathlib import Path

import pytest

from app.coding import asserts, dataset
from app.learning import elo
from app.voice import turns
from app.voice.chunker import SentenceChunker
from app.voice.tts import strip_tags

DATA = Path(__file__).resolve().parents[1] / "data" / "LeetCodeDataset-train.jsonl"


def feed_all(text: str, step: int = 3) -> list[str]:
    c = SentenceChunker()
    out = []
    for i in range(0, len(text), step):
        out += c.feed(text[i : i + step])
    rest = c.flush()
    return out + ([rest] if rest else [])


def test_chunker_releases_first_clause_early_and_keeps_text():
    text = "Okay, so you moved the fan-out into a queue. Why a queue there, rather than batching? Curious."
    chunks = feed_all(text)
    assert chunks[0] == "Okay, so you moved the fan-out into a queue."
    assert " ".join(chunks).split() == text.split()


def test_chunker_eager_first_chunk_on_long_clause():
    chunks = feed_all("Let me walk you through how this next part of the interview is going to work for us today")
    assert len(chunks) >= 2
    assert len(chunks[0].split()) <= 10


def test_chunker_does_not_split_decimals():
    chunks = feed_all("Latency went from 1.5 seconds to 0.3 seconds. Nice.")
    assert chunks[0].startswith("Latency went from 1.5 seconds")


def test_strip_tags_removes_voice_cues_only():
    assert strip_tags("[chuckle] Fair enough. Arrays [i] stay") == "Fair enough. Arrays [i] stay"


@pytest.mark.parametrize(
    "text,phase,expect_hold",
    [
        ("We used Kafka for that.", "projects", False),
        ("So the way we handled it was, um", "projects", True),
        ("and then", "projects", True),
        ("Let me think about this for a sec", "coding", True),
        ("I'd use a hash map here.", "coding", True),  # coding always gets a little extra patience
    ],
)
def test_hold_ms(text, phase, expect_hold):
    assert (turns.hold_ms(text, phase=phase, speech_ms=3000) > 0) == expect_hold


def test_backchannel():
    assert turns.is_backchannel("mm-hmm")
    assert turns.is_backchannel("Yeah.")
    assert not turns.is_backchannel("Yeah, but actually I disagree")


def test_examples_constraints_and_signature():
    desc = (
        "Given nums...\n \nExample 1:\n\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]\n"
        "Explanation: Because nums[0] + nums[1] == 9.\n\nExample 2:\n\nInput: nums = [3,2,4], target = 6\n"
        "Output: [1,2]\n\n \nConstraints:\n\n2 <= nums.length <= 104\n-109 <= nums[i] <= 109\n\n \n"
        "Follow-up: Can you come up with an algorithm that is less than O(n2) time complexity?"
    )
    ex = asserts.parse_examples(desc)
    assert [e["input"] for e in ex] == ["nums = [2,7,11,15], target = 9", "nums = [3,2,4], target = 6"]
    assert ex[0]["explanation"].startswith("Because")
    assert asserts.parse_constraints(desc) == ["2 <= nums.length <= 104", "-109 <= nums[i] <= 109"]
    assert asserts.follow_up(desc).startswith("Follow-up")


def test_build_call_wraps_linked_lists():
    starter = (
        "class Solution:\n    def addTwoNumbers(self, l1: Optional[ListNode], l2: Optional[ListNode])"
        " -> Optional[ListNode]:\n        "
    )
    assert asserts.build_call("l1 = [9,9], l2 = [1]", starter) == "candidate(l1 = list_node([9, 9]), l2 = list_node([1]))"
    with pytest.raises(ValueError):
        asserts.build_call("l1 = [1]", starter)
    with pytest.raises(ValueError):
        asserts.build_call("l1 = __import__('os'), l2 = [1]", starter)


@pytest.mark.skipif(not DATA.exists(), reason="dataset not downloaded")
def test_classify_real_dataset_rows():
    with DATA.open() as f:
        rows = [json.loads(next(f)) for _ in range(60)]
    for row in rows:
        examples, tests = dataset.classify_tests(row)
        kinds = [t["kind"] for t in tests]
        assert kinds.count("sample") >= 2, row["task_id"]
        assert len(tests) == row["test"].count("assert ")
        assert all(json.loads(t["assertion"])["actual_expr"] for t in tests)


def test_elo_moves_in_the_right_direction():
    theta, d = elo.update(1500, 1500, s=1.0, item_attempts=0)
    assert theta > 1500 and d < 1500
    theta, d = elo.update(1500, 1500, s=0.0, item_attempts=0)
    assert theta < 1500 and d > 1500
    assert elo.outcome(1.0, 1.0, 5, True) < elo.outcome(1.0, 1.0, 0, True)


def test_wants_time():
    from app.voice.turns import wants_time
    assert wants_time("No, I have not seen this before. Let me try.")
    assert wants_time("Hmm, give me a second.")
    assert wants_time("let me think")
    assert not wants_time("Let me think, is the input sorted?")  # a question still gets an answer
    assert not wants_time("We used Kafka for that.")
    assert not wants_time("Let me tell you about the outbox table.")


def test_code_brackets_survive_and_are_spoken():
    from app.voice.tts import speakable, strip_tags
    assert strip_tags("nums[i] and nums[i minus one] have") == "nums[i] and nums[i minus one] have"
    assert strip_tags("arr[left] vs arr[right]") == "arr[left] vs arr[right]"
    assert strip_tags("[chuckle] Fair enough.") == "Fair enough."
    assert speakable("compare nums[i] with nums[i-1].") == "compare nums at i with nums at i minus 1."

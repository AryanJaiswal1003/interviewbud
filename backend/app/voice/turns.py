"""End-of-turn heuristics.

Candidates pause mid-thought ("so the way we handled it was... um"). The VAD fires on ~600 ms of silence; this
gate decides whether that silence is a finished turn or a thinking pause that deserves a longer hold.
Cheap and synchronous on purpose: it sits on the latency-critical path.
"""

import re

_TRAILING_CONTINUERS = {
    "and", "but", "so", "or", "because", "cause", "like", "um", "uh", "uhm", "er", "the", "a", "an", "to",
    "of", "with", "for", "which", "that", "then", "if", "when", "where", "while", "basically", "actually",
    "we", "i", "my", "our", "is", "was", "were", "are", "in", "on", "at", "it's", "its", "kind", "sort",
}
_THINKING = re.compile(
    r"\b(let me think|give me a (sec|second|moment|minute)|hold on|one sec|hmm+|let me see|"
    r"thinking about it|let me (try|figure)|i'?m thinking)\b[\s.,]*$",
    re.I,
)
_FILLER_ONLY = re.compile(r"^\s*(um+|uh+|hmm+|mm+|er+|ah+|okay|ok|yeah|right|so)[\s.,!?]*$", re.I)


def hold_ms(text: str, *, phase: str, speech_ms: float) -> int:
    """Extra milliseconds to wait for continuation before replying (0 = reply now)."""
    t = text.strip()
    if not t:
        return 0
    if _THINKING.search(t):
        return 6000  # explicitly asked for time
    words = re.findall(r"[a-zA-Z']+", t.lower())
    last = words[-1] if words else ""
    # STT punctuates: a full stop / question mark means the thought is complete, unless it trails off ("...").
    terminal = t[-1] in ".?!" and not t.endswith("...")
    ends_open = t[-1] in ",-–—" or t.endswith("...") or (not terminal and last in _TRAILING_CONTINUERS)
    base = 0
    if ends_open:
        base = 1400
    elif _FILLER_ONLY.match(t) and phase == "coding":
        base = 1500
    if phase == "coding":
        base += 400  # think-aloud while coding: be patient
    if speech_ms > 15000 and not ends_open:
        base = max(0, base - 200)  # long, complete answer: respond promptly
    return base


def wants_time(text: str) -> bool:
    """'Let me think' / 'give me a sec' with no question attached: acknowledge and go quiet, don't take the turn."""
    t = text.strip()
    return bool(_THINKING.search(t)) and "?" not in t


TIME_ACKS = ("Sure, take your time.", "Of course, go for it.", "Sure thing, take your time.", "Yeah, go ahead.")


def is_backchannel(text: str) -> bool:
    """Short acknowledgements while the interviewer talks shouldn't be treated as interruptions."""
    return bool(re.fullmatch(r"\s*(mm-?hmm|mhm|uh-?huh|yeah|yep|right|okay|ok|sure|got it|i see)[\s.!]*", text, re.I))


_DONE = re.compile(r"\b(no (more )?questions|nothing (else|more)|that'?s (everything|all|it)|i'?m (all )?(good|done)|"
                   r"all set|we can (wrap|end|stop)|let'?s (wrap|end|stop))\b", re.I)


def sounds_done(text: str) -> bool:
    """The candidate signalling they've nothing more to add (screening calls wrap up on this)."""
    return bool(_DONE.search(text or ""))

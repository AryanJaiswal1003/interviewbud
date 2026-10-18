"""Incremental text chunking: LLM token stream -> speakable chunks for TTS.

The first chunk is released eagerly (at a clause boundary or after a handful of words) so audio starts
while the model is still generating; later chunks wait for sentence ends, which gives TTS enough context
for natural prosody.
"""

import re

_SENT_END = re.compile(r"([.!?…]+[\"')\]]*)(\s+|$)")
_CLAUSE = re.compile(r"([,;:—–])\s+")


class SentenceChunker:
    def __init__(self, first_min_words: int = 4, first_max_words: int = 9, max_chars: int = 220):
        self.buf = ""
        self.emitted = 0
        self.first_min_words = first_min_words
        self.first_max_words = first_max_words
        self.max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            chunk = self._take()
            if chunk is None:
                break
            out.append(chunk)
        return out

    def _cut(self, idx: int) -> str:
        chunk, self.buf = self.buf[:idx], self.buf[idx:]
        self.emitted += 1
        return chunk.strip()

    def _take(self) -> str | None:
        b = self.buf
        if not b.strip():
            return None
        m = _SENT_END.search(b)
        # Ignore "e.g." / decimals / "O(n)." false sentence ends by requiring whitespace or end after the mark.
        if m and m.group(2):
            if len(b[: m.end()].split()) >= 2 or self.emitted:
                return self._cut(m.end())
        if self.emitted == 0:
            words = b.split()
            c = _CLAUSE.search(b)
            if c and len(b[: c.end()].split()) >= self.first_min_words:
                return self._cut(c.end())
            if len(words) > self.first_max_words:
                # cut right after the Nth complete word
                m = re.match(r"\s*(?:\S+\s+){%d}" % self.first_max_words, b)
                if m:
                    return self._cut(m.end())
        if len(b) > self.max_chars:
            c = None
            for c in _CLAUSE.finditer(b):
                pass
            if c and c.end() > 40:
                return self._cut(c.end())
            sp = b.rfind(" ", 0, self.max_chars)
            if sp > 0:
                return self._cut(sp + 1)
        return None

    def flush(self) -> str | None:
        rest = self.buf.strip()
        self.buf = ""
        return rest or None

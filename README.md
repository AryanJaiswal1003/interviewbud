# InterviewBud

A personal AI technical-screening platform. A candidate uploads a resume and holds a natural, low-latency voice
conversation with an AI interviewer that has read it: background → project deep-dives → deeper technical questions
→ a live coding round (editor + sandboxed tests, whiteboard, notes) → their questions → wrap-up. Every interview
lands in a dashboard with an evidence-backed scorecard, and the interviewer improves itself from each one.

Design and research grounding: [`docs/requirements.md`](docs/requirements.md). How it all works, with diagrams and
measured latency: [`docs/architecture.md`](docs/architecture.md).

## Run it

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
# create .env with your settings (see Configuration in docs/architecture.md); it is gitignored
./dev.sh                  # Postgres, sandbox image, API :8765, CopilotKit runtime :8766, web :5199,
                          # and the Pocket TTS server :8792 when TTS_PROVIDER=pocket
```

Open http://localhost:5199. The first API start downloads the
[LeetCodeDataset](https://huggingface.co/datasets/newfacade/LeetCodeDataset) (Apache-2.0, 2,869 problems with
reference solutions and ~280k tests) into Postgres.

Backend tests: `cd backend && uv run pytest`.

## How it fits together

| Piece | Where | Notes |
|---|---|---|
| Voice loop | `backend/app/session.py`, `frontend/src/lib/useInterview.ts` | Silero VAD in the browser → WAV utterance over WebSocket → Whisper on device (or Fish / Azure ASR) → Azure OpenAI stream → sentence chunker → TTS (self-hosted Pocket TTS on CPU by default; Chatterbox, fish-speech or Fish Audio cloud also supported) → PCM16 → AudioWorklet. Barge-in, ducking for "mm-hmm", end-of-turn hold for thinking pauses, speculative replies cancelled if the candidate keeps talking, `<listen/>` silence during think-aloud. |
| Interviewer brain | `backend/app/agent/` | Resume → structured profile → personalised plan (probe ladders seeded with resume claims, problem picked from an Elo-filtered shortlist). Working memory (facts, open threads, claims, evidence) updated off the critical path. |
| Coding | `backend/app/coding/` | Dataset asserts split into sample / hidden / hard tests; each case run individually with per-case timeouts in a hardened warm Docker container (no network, read-only, non-root, mem/cpu/pid caps). Agent-injected tests get their expected output from the reference solution. |
| Workspace | `frontend/src/components/` | Monaco, Excalidraw (agent can sketch; board summarised to text so the agent can "see" it), notes. All shared with the interviewer. |
| CopilotKit | `copilot-runtime/server.mjs`, `backend/app/api/agui.py` | v2 runtime proxies to Python AG-UI agents: `interviewer` (typed chat into the live interview) and `analyst` (dashboard copilot with frontend tools to open/filter interviews). |
| Evaluation | `backend/app/evaluation/` | 4-level anchored rubric, evidence quotes per score, hint-ladder cap, reviewer overrides. |
| Self-improvement | `backend/app/learning/` | Post-interview critic grades the *interviewer* → ACE-style lessons (pg_trgm dedupe, helpful/harmful counters) gated by reviewer approval or repeated confirmation → injected per phase. Elo calibration of problem difficulty. |

## Not built yet
* Joining Google Meet (needs a meeting bot such as Recall.ai or the Meet Media API developer preview); the session
  runtime is transport-agnostic so it can be added as another transport.
* Hidden-test grading for languages other than Python (the dataset's tests are Python).

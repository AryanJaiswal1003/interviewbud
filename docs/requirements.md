# InterviewBud — Requirements & Design (2026-10-18)

A personal AI technical-screening platform. The candidate uploads a resume, then holds a natural,
low-latency **voice conversation** with an AI interviewer that knows their background, digs into their
projects, and runs a live coding round inside an agent-aware workspace (code editor + sandbox,
whiteboard, notepad). Every interview lands in a dashboard with an evidence-backed scorecard, and the
interviewer improves itself from each interview.

---

## 1. Research grounding (what the design is built on)

| Area | Finding | Source | Consequence in product |
|---|---|---|---|
| Interview validity | Structured interviews are the top predictor (≈.42 corrected validity) vs unstructured ≈.19; work samples ≈.33 | Sackett et al. 2022 (JAP 107(11)); Schmidt & Hunter 1998 | Fixed phase skeleton + same rubric for everyone, adaptive follow-ups inside it ("controlled variance") |
| Scoring | Score each dimension separately, observable behaviour only, with evidence; score right after | OPM structured-interview guide; Google re:Work; Karat 2026 rubrics | Scorecard stores `{score 1–4, evidence quotes + turn ids, confidence, insufficient_signal}` per dimension |
| Coding rubric | Problem solving / Coding / Verification / Communication (Meta), 4-level anchors | techinterviewhandbook.org/coding-signals; Meta (secondary) | Rubric anchors encoded in `rubric.py`, shown in dashboard |
| Hints | Ladder L0 scope → L1 redirect → L2 general → L3 specific input → L4 name bug → L5 give approach; hint after ~3–5 min stuck | Holloway "Hinting"; Tech Interview Handbook | `give_hint(level)` tool; hints logged; problem-solving score capped (L4+ ⇒ ≤2) |
| Project deep-dive | STAR + ownership ("I vs we"), numbers, trade-offs, one layer down ×2–3, failures, reflection | Amazon STAR (secondary), Lou Adler MSA question | Per-project probe ladder in the interview plan; `claims_to_verify` in working memory |
| Stress | Being watched halves performance (Behroozi et al., FSE 2020) | FSE 2020 | Agent explicitly offers silent thinking time; doesn't narrate mistakes; stays quiet during think-aloud |
| AI interviewers | 70k-applicant field experiment: AI voice interviews +12% offers, +18% starts, 78% chose AI; 7% technical glitches | Jabarian & Henkel, "Voice AI in Firms" | Robust failure handling (text fallback, reconnect), consistent structure |
| Legal | EEOC prohibited topics (age, religion, national origin, marital/family, disability, etc.); NYC LL144, Illinois AI Video Interview Act notice/consent | eeoc.gov; ILGA HB2557 | Guardrails in system prompt; consent screen before start; protected info never stored |
| Self-improvement | Reflexion (verbal self-critique memory); ACE evolving playbooks with helpful/harmful counters and delta updates; **degrades without real feedback** | arXiv 2303.11366, 2510.04618 | Post-interview critic → proposed lessons → gated by human review / candidate feedback → retrieved per phase |
| Difficulty | Elo for person × item | Pelánek, "Elo-based learner modelling" | Problem Elo + candidate θ updated after each coding round; used for problem selection |
| Memory | MemGPT/Letta core vs archival memory | arXiv 2310.08560 | Core blocks always in prompt; transcript + resume + playbook archival |

### Vendor facts verified (Oct 2026)
* **Fish Audio**: TTS model `s2.1-pro` (s1 retired 2026-12-31); live TTS `wss://api.fish.audio/v1/tts/live`, MessagePack
  frames `start/text/flush/stop` → `audio/finish/error`; PCM 16-bit mono at chosen sample rate; S2 uses `[bracket]` emotion
  tags; ASR `POST /v1/asr` with `model: transcribe-1-pro` header, **non-streaming**, multipart; voice cloning `POST /model`
  (`type=tts, train_mode=fast, voices[]`); concurrency-limited (5 slots on starter tier). No CORS ⇒ server proxy required.
* **Coding data**: HuggingFace `newfacade/LeetCodeDataset` (Apache-2.0), 2,641 train + 228 test problems with
  description, starter code, reference solution, Python preamble (ListNode/TreeNode helpers), `check(candidate)` asserts and
  `input_output` pairs. Loaded verbatim into Postgres — no invented problems.
* **Turn detection**: `@ricky0123/vad-web` 0.0.31 (Silero v5/v6 in browser): `redemptionMs`, `minSpeechMs`,
  `preSpeechPadMs`, `onSpeechStart`, `onSpeechRealStart`, `onSpeechEnd(Float32Array@16k)`.

---

## 2. Goals / non-goals / anti-goals

**Goals**
1. Feels like talking to a sharp, warm human interviewer who has *read your resume*.
2. Voice-to-voice latency target: **p50 < 1.2 s, p90 < 1.8 s** from end of candidate speech to first interviewer audio.
3. Natural flow: rapport → background → projects → technical depth → coding → candidate questions → wrap-up,
   with the agent deciding transitions, not a timer.
4. Agent-aware workspace: the interviewer sees the code, test results, whiteboard, notes in real time and can act on them
   (present a problem, inject a test, draw, give a hint).
5. Real data only: problems/tests from the dataset; everything else derived from the actual resume and conversation.
6. Dashboard of all interviews with evidence-backed scorecards, transcripts, code history, latency metrics.
7. Self-improvement loop that is gated by real feedback.

**Non-goals (v1)**: multi-tenant auth/SSO; Google Meet bot (see §9); grading non-Python languages against hidden tests;
storing raw audio.

**Anti-goals**: robotic "question → answer → next question" ping-pong; interrupting a candidate who is thinking;
the agent reading out code/test output verbatim; scores without evidence; asking protected-category questions.

---

## 3. Architecture

```
Browser (React 19 + Vite + CopilotKit)
 ├─ MicVAD (Silero) ── speech_start / WAV utterances ──┐
 ├─ AudioWorklet PCM player  ◄── PCM 24k chunks ───────┤  WebSocket /ws/session/{id}
 ├─ Workspace: Monaco · Excalidraw · Notepad ── state ─┤  (voice + workspace events + ui_actions)
 ├─ CopilotKit chat/sidebar ───────────── AG-UI ───────┤  POST /agui  (text channel, frontend tools)
 └─ Dashboard (+ CopilotKit analyst)  ── REST ─────────┘
FastAPI (Python 3.12)
 ├─ SessionRuntime (one per live interview): turn manager, barge-in, speculative reply
 ├─ STT  (Fish ASR transcribe-1-pro | Azure gpt-4o-mini-transcribe)
 ├─ Interviewer LLM (Azure OpenAI, streaming + tools)  ─► SentenceChunker ─► Fish TTS live WS (pre-opened)
 ├─ Background workers: memory extractor, evidence logger, scorecard, critic/curator, Elo
 ├─ Sandbox runner: Docker per run (--network none, ro fs, mem/cpu/pids limits, non-root, timeout)
 └─ Postgres 17 (+pg_trgm, pgvector image)
```

### 3.1 Turn-taking & latency design
1. **Client VAD** (Silero v5, `redemptionMs≈600`, `minSpeechMs≈250`, `preSpeechPadMs≈300`). On `onSpeechRealStart`
   the client stops local playback immediately (barge-in) and tells the server, which cancels LLM + TTS for the turn.
2. On `onSpeechEnd` the 16 kHz Float32 utterance is WAV-encoded client-side and sent as one binary frame.
3. Server immediately (in parallel): starts STT **and** pre-opens a Fish live-TTS socket.
4. **Turn-completeness gate**: transcripts ending in a dangling conjunction/filler ("so…", "and", "um", "let me think")
   get a hold window (≈1.5 s; longer during coding) — if speech resumes, utterances are merged.
5. **Speculative reply**: the reply starts as soon as the gate passes; if the candidate resumes speaking before audio
   plays, the reply is cancelled and the new speech is appended to the same candidate turn.
6. LLM tokens → `SentenceChunker` (flush at sentence/clause boundary, first chunk eagerly at ~6 words) → Fish `text` events
   + `flush`; PCM chunks streamed straight to the browser.
7. **Listen mode**: during coding think-aloud the model may answer `<listen/>` (stay silent) or a short backchannel.
8. Memory extraction, evidence logging and planning updates run **after** the reply starts, never before it.
9. Every turn records `stt_ms, llm_ttft_ms, tts_ttfb_ms, voice_to_voice_ms` (client reports playback start).

### 3.2 Interview brain
* **Candidate profile** (LLM structured extraction from resume text, pypdf/python-docx): basics, work, projects
  (tech, scale numbers, claimed impact), skills, education, `claims_to_verify`.
* **Interview plan** (generated once, structured output): personalised opener, background questions, 2 projects with
  probe ladders seeded from resume claims, technical deep-dive topics tied to their stack, chosen coding problem
  (from a shortlist ranked by tags × role × Elo, preferring recent problems to reduce memorisation) with a
  domain-flavoured framing, phase time budgets (60-min default: 3/5/15/—/28/6/3).
* **Working memory** (core blocks, persisted each turn): `phase_state`, `candidate_facts`, `open_threads[]`,
  `claims_to_verify[]`, `evidence_log{dimension:[quotes]}`, `hints_given[]`, `rapport_notes`.
* **Interviewer prompt** = persona + guardrails + phase goals + working memory + top-k playbook lessons for the
  phase + live workspace snapshot + recent transcript.
* **Agent tools**: `advance_phase`, `present_problem`, `give_hint(level, text)`, `add_test_case(input, why)`
  (expected output computed by running the reference solution), `run_tests`, `open_panel`, `draw_on_whiteboard`,
  `end_interview`.

### 3.3 Coding environment
* Problem panel: description, constraints (parsed), examples, starter code.
* Tests: **sample** (from description examples, visible), **hidden** (dataset asserts), **hard** (largest-input /
  boundary cases from the dataset + agent-injected adversarial cases validated against the reference solution).
* Runner: Python 3.12 image; each assert executed individually with per-case timing; stdout captured; JSON result.
  Candidate sees sample details + hidden/hard pass counts; the agent sees everything and decides what to reveal.

### 3.4 Evaluation
* Scorecard dims: Communication, Technical depth (projects), Ownership & impact, Problem solving, Coding,
  Verification/testing — 4-level anchored rubric; objective coding metrics; hint cap; recommendation band
  (Strong hire / Hire / No hire / Strong no hire); strengths, concerns, verified/unverified claims.
* Human reviewer can override any score in the dashboard (feeds learning loop).

### 3.5 Self-improvement
1. Post-interview **critic** grades the *interviewer* (timing vs budget, missed probes, hint timing, leading questions,
   guardrail slips, dimensions left without signal) → delta lessons `{scope, lesson, evidence}`.
2. **Curator** (deterministic): dedupe with pg_trgm similarity, merge counters, cap per scope; lessons start
   `proposed` and become `active` only after reviewer approval or positive feedback signal.
3. Retrieval: active lessons for the current phase ranked by (helpful − harmful) and recency.
4. Feedback signals: candidate post-interview rating, reviewer score overrides, lesson approve/reject.
5. **Elo**: problem difficulty `d` (init Easy 1200 / Medium 1500 / Hard 1800) and candidate θ updated from the
   hint-adjusted coding outcome.

---

## 4. Data model (Postgres)
`voices`, `candidates`, `interviews` (status, role, plan jsonb, memory jsonb, scorecard jsonb, timings),
`turns` (speaker, text, phase, metrics jsonb), `problems` (dataset fields + constraints + elo),
`problem_tests` (kind sample/hidden/hard, input, expected/assert source), `code_runs`, `workspace_snapshots`
(notes, whiteboard elements), `playbook_lessons` (scope, text, status, helpful, harmful), `feedback`.

## 5. Screens
1. **Home / new interview** — resume upload, target role & seniority, duration, interviewer voice; consent notice.
2. **Interview room** — interviewer orb + live captions, mic state, phase timeline; tabs: Code · Whiteboard · Notes;
   CopilotKit chat for typed messages; mute / text-only fallback.
3. **Dashboard** — list with filters, KPIs; detail: scorecard + rubric evidence, transcript, code runs, whiteboard,
   latency chart; reviewer overrides. CopilotKit analyst sidebar over the data.
4. **Agent learning** — playbook lessons (approve/reject), problem Elo table.
5. **Settings / voices** — clone a voice from a sample (Fish `POST /model`), pick default voice.

## 6. Configuration
`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_CHAT_DEPLOYMENT` (fast, interviewer voice),
`AZURE_OPENAI_REASONING_DEPLOYMENT` (plan/scorecard/critic), `AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT` (optional),
`FISH_API_KEY`, `FISH_TTS_MODEL=s2.1-pro`, `FISH_VOICE_ID`, `STT_PROVIDER=fish|azure`, `DATABASE_URL`.

## 7. Ports
Postgres 5544 · API 8765 · Web 5199 (avoid existing local services).

## 8. Acceptance
* Resume → profile → plan works end-to-end with real Azure calls.
* Voice loop: barge-in stops audio < 150 ms; voice-to-voice metrics recorded per turn.
* Coding: dataset problems run in sandbox, per-test results, agent-injected tests validated by reference solution.
* Dashboard shows completed interview with scorecard and transcript; lessons appear after interview.

## 9. Later (documented, not built in v1)
* **Google Meet**: requires a meeting bot (Recall.ai / open-source Attendee / headless Chrome) or the Meet Media API
  (developer-preview, requires enrollment). The SessionRuntime is transport-agnostic (audio in, audio out), so a Meet
  adapter plugs in as another transport.
* Multi-language graded tests; audio recording; ATS integration; offline DSPy/GEPA prompt optimisation against
  human-scored transcripts.

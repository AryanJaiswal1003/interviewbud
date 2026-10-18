# Preparation journey: test log (workbook section 06)

Run on 2026-10-18 against the live local stack: real job pages fetched over the internet, Azure `grok-4.6`, Pocket TTS,
Whisper, Postgres. Fictional candidate "Jordan Rivera" (`prep-qa@example.com`, credentials in the gitignored
`backend/data/dev_prep_account.json`). Postings: Discord, *Senior Full-Stack Software Engineer, Ads*
(`job-boards.greenhouse.io/discord/jobs/8659978002`) and Anthropic, *Applied AI Engineer*
(`job-boards.greenhouse.io/anthropic/jobs/5390799008`).

## Checklist

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Provide a job URL and extract the role requirements | **Pass** | Discord: read via plain HTTP, 10 requirements + 7 skills + 6 responsibilities; **23/23 quotes found verbatim** in the page. Anthropic: 19 requirements, 19/19 verified. |
| 2 | Handle a broken or inaccessible URL safely | **Pass** | Dead Greenhouse job (redirects to the board index): recognised as "not a single job posting", journey stops at *Paste the job description*. 404 page: "That page doesn't exist". Internal/unsafe URLs (loopback, 10/8, 169.254.169.254, `[::1]`, credentials in URL) refused (`tests/test_prep.py`). |
| 3 | Identify missing candidate information and ask a relevant follow-up | **Pass** | First question targeted R1 (must-have full-stack TypeScript/React/Python): "Could you share a recent full-stack project and which languages/frameworks you used…". Next one targeted R5 (years of experience). One question at a time. |
| 4 | Correct an answer and verify the correction persists | **Pass** | Fact #1 "about four months" corrected to "about six months, with one other engineer"; revision row recorded; the regenerated plan's question reads "…after six months?". Automated: `test_corrections_persist_with_history_and_stay_private`. |
| 5 | Refresh the page and recover the same preparation session | **Pass** | Reloading `/prep/{id}` in the browser returns to the *Practice* step with both practices listed; all state is read from Postgres. Automated: `test_preparations_are_private_and_recover_after_refresh`. |
| 6 | Generate a role-specific interview plan | **Pass** | Plan v1 in 39 s: six topics each citing requirement ids (e.g. "Ads products and campaign-management platforms [system_design] R1"), gaps = exactly the requirements without confirmed evidence (R2, R3, R4, R6). |
| 7 | Start and complete a practice interview | **Pass** | Practice from plan v1 run over the real session WebSocket; ended with the end action; scored. (Candidate turns were typed in this run; voice input was exercised in earlier live sessions.) |
| 8 | Spoken responses use Pocket TTS | **Pass** | 41.2 s of 24 kHz PCM received from the session; Pocket TTS server logged every synthesis request. |
| 9 | Inspect the transcript and resulting scorecard | **Pass** | `/dashboard/{interview}`: transcript with turn numbers, six-dimension scorecard (No hire: 2/2/2 on communication, depth, ownership; coding not attempted). |
| 10 | Feedback refers to actual candidate responses | **Pass** | Every strength/gap/improvement cites a turn and quotes it, e.g. "When asked [turn 9] 'What do you want the next role to look like?' you said [turn 10] 'The dashboard was a React and TypeScript app…'. Answer the question first…". It also caught that two scripted answers didn't match the questions asked. |
| 11 | Start another practice without overwriting the previous result | **Pass** | Practice #2 and #3 created from the same plan; #1 keeps its scorecard and feedback. Automated: `test_practice_needs_a_plan_and_never_overwrites_earlier_practice`. |

## Failures found, root cause, fix, retest

| Failure | Root cause | Smallest fix | Retest |
|---|---|---|---|
| Job intake took **263 s** | `grok-4.6` reasons at full depth on every structured call; extraction is mostly careful copying | `PREP_REASONING_EFFORT=low` for journey steps (`llm.structured(..., reasoning_effort=...)`) | **48 s** on a different posting, still 23/23 quotes verified |
| Practice setup took **254 s** | Same, inside the reused interview planner | Pass the journey's effort through `planner.make_plan(..., reasoning_effort=)` | **44 s** |
| Practice interview stuck at "completed", never scored | The API restarted while scoring ran as an in-process background task (I restarted it mid-run) | Startup sweep `resume_interrupted_work()` resumes scoring, planning, intake and plan generation left mid-way | Resumed `{'score': 1}`; feedback produced 137 s later. Automated: `test_interrupted_scoring_resumes_on_startup` |
| A private IP link (`http://10.0.0.5/…`) was accepted at submission (refused only later, at fetch) | URL check validated shape; addresses were checked only at connect time | Reject non-public IP literals in `check_url` | `test_preparations_are_private_and_recover_after_refresh` passes |
| Another account's preparation showed a bare red "Not Found" | No empty/error state on the journey page | Friendly "We couldn't find that preparation" state | Checked in the browser |

## Known limits

* The live interviewer's first-token time was 2.7–8 s in this run: `grok-4.6` always reasons, and the journey's
  background calls share the deployment's 50k tokens/minute. A fast non-reasoning chat deployment fixes this.
* Firecrawl and Browserless are wired as fallbacks for JavaScript-rendered pages but have no keys configured, so they
  weren't exercised; both test postings were readable over plain HTTP.
* Neither posting had application questions, so answer drafting was only exercised by schema, not with real
  questions.

import { ArrowLeft, CheckCircle2, CircleAlert, Lightbulb, Quote } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Avatar, LatencyChip, RecPill, ScoreMeter, ScoreStrip } from "../components/ui";
import { api, DIMENSIONS, fmtDate, PHASES, RECOMMENDATION } from "../lib/api";
import { token, useTheme } from "../lib/theme";

type Tab = "scorecard" | "transcript" | "code" | "interviewer";
const TABS: [Tab, string][] = [["scorecard", "Scorecard"], ["transcript", "Transcript"], ["code", "Code"], ["interviewer", "Interviewer's self-review"]];

export default function InterviewDetail() {
  const { id = "" } = useParams();
  const [d, setD] = useState<any>(null);
  const [tab, setTab] = useState<Tab>("scorecard");
  const [focusTurn, setFocusTurn] = useState<number | null>(null);

  const load = () => api(`/api/interviews/${id}`).then(setD);
  useEffect(() => {
    load();
    const t = setInterval(() => {
      if (d?.status !== "scored" || !d?.critique?.overall) load();
    }, 5000);
    return () => clearInterval(t);
  }, [id, d?.status, d?.critique?.overall]); // eslint-disable-line react-hooks/exhaustive-deps

  const jump = (seq: number) => {
    setFocusTurn(seq);
    setTab("transcript");
    // Wait for the transcript (and its chart) to lay out, then jump; a smooth scroll gets cut short by the resize.
    setTimeout(() => {
      document.getElementById(`turn-${seq}`)?.scrollIntoView({ block: "center" }); // returns a Promise in current Chrome
    }, 250);
  };

  if (!d) return <div className="p-10 text-graphite">Loading…</div>;
  const card = d.scorecard || {};
  const rec = d.reviewer_overrides?.recommendation || card.recommendation;
  const scores = Object.fromEntries((card.dimensions ?? []).map((x: any) =>
    [x.dimension, d.reviewer_overrides?.scores?.[x.dimension] ?? x.score]));

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 md:px-10">
      <Link to="/dashboard" className="inline-flex items-center gap-1.5 text-sm text-graphite hover:text-ink">
        <ArrowLeft size={15} /> Interviews
      </Link>
      <header className="mt-4 flex flex-wrap items-start justify-between gap-6 rounded-2xl border border-line bg-surface p-6">
        <div className="flex min-w-0 items-start gap-4">
          <Avatar name={d.candidate || "?"} size={56} />
          <div className="min-w-0">
            <h1 className="font-display text-[30px] leading-tight font-semibold tracking-tight">{d.candidate || "Unnamed candidate"}</h1>
            <p className="text-graphite">{d.headline}</p>
            <p className="mt-1 text-sm text-mute">
              {d.role}, <span className="capitalize">{d.seniority}</span> · {fmtDate(d.started_at || d.created_at)} · {d.duration_min} min
              {d.problem && <> · {d.problem.title}</>}
            </p>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <RecPill rec={rec} status={d.status} big />
          {card.dimensions && <ScoreStrip scores={scores} size="lg" />}
          {d.reviewer_overrides?.recommendation && <span className="text-xs text-mute">outcome set by a reviewer</span>}
          {d.error && <span className="max-w-xs text-right text-sm text-alarm">{d.error}</span>}
        </div>
      </header>

      <nav className="mt-6 flex gap-6 border-b border-line" role="tablist">
        {TABS.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={"-mb-px border-b-2 pb-2.5 text-[15px] " + (tab === t ? "border-ink font-semibold" : "border-transparent text-graphite hover:text-ink")}>
            {label}
          </button>
        ))}
      </nav>

      {tab === "scorecard" && <Scorecard d={d} onSaved={load} onJump={jump} />}
      {tab === "transcript" && <Transcript d={d} focus={focusTurn} />}
      {tab === "code" && <Code d={d} />}
      {tab === "interviewer" && <InterviewerReview d={d} onJump={jump} />}
    </div>
  );
}

/** Render "[turn 12]" references as links into the transcript. */
function WithTurns({ text, onJump }: { text: string; onJump: (seq: number) => void }) {
  const parts = text.split(/(\[turns? \d+(?:\s*[,/–-]\s*\d+)*\])/g);
  return (
    <>
      {parts.map((p, i) => {
        const nums = p.match(/^\[turns? ([\d\s,/–-]+)\]$/);
        if (!nums) return <Fragment key={i}>{p}</Fragment>;
        const first = Number(nums[1].match(/\d+/)![0]);
        return (
          <button key={i} onClick={() => onJump(first)}
            className="mx-0.5 rounded bg-paper-2 px-1.5 text-[13px] text-graphite hover:bg-amber-soft hover:text-ink">
            {p.slice(1, -1)}
          </button>
        );
      })}
    </>
  );
}

function Scorecard({ d, onSaved, onJump }: { d: any; onSaved: () => void; onJump: (n: number) => void }) {
  const card = d.scorecard || {};
  const [edit, setEdit] = useState(false);
  const [scores, setScores] = useState<Record<string, number>>(d.reviewer_overrides?.scores ?? {});
  const [rec, setRec] = useState<string>(d.reviewer_overrides?.recommendation ?? card.recommendation ?? "");
  const [note, setNote] = useState<string>(d.reviewer_overrides?.note ?? "");
  if (!card.dimensions) {
    return <p className="mt-10 text-graphite">{d.status === "completed" ? "Scoring the interview now; this page updates by itself." : "The scorecard appears here once the interview is scored."}</p>;
  }
  return (
    <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_330px]">
      <div>
        <p className="max-w-[70ch] text-lg leading-relaxed">{card.summary}</p>
        <div className="mt-8 space-y-4">
          {card.dimensions.map((dim: any) => {
            const overridden = scores[dim.dimension] !== undefined && scores[dim.dimension] !== dim.score;
            return (
              <section key={dim.dimension} className="rounded-2xl border border-line bg-surface p-5">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <h3 className="font-display text-lg font-semibold">{DIMENSIONS[dim.dimension]}</h3>
                  <ScoreMeter score={scores[dim.dimension] ?? dim.score} />
                  {overridden && <span className="text-sm text-mute">model said {dim.score}</span>}
                  <span className="ml-auto text-sm text-mute">{dim.confidence} confidence</span>
                  {edit && (
                    <select aria-label={`Override ${DIMENSIONS[dim.dimension]}`} className="rounded-lg border border-line px-2 py-1 text-sm"
                      value={scores[dim.dimension] ?? dim.score}
                      onChange={(e) => setScores({ ...scores, [dim.dimension]: Number(e.target.value) })}>
                      {[0, 1, 2, 3, 4].map((n) => <option key={n} value={n}>{n || "No signal"}</option>)}
                    </select>
                  )}
                </div>
                <p className="mt-2 max-w-[72ch] text-graphite"><WithTurns text={dim.rationale} onJump={onJump} /></p>
                {dim.evidence?.length > 0 && (
                  <ul className="mt-3 space-y-1.5">
                    {dim.evidence.map((e: string, i: number) => (
                      <li key={i} className="flex gap-2 text-[15px]">
                        <Quote size={14} className="mt-1 shrink-0 text-mute" />
                        <span><WithTurns text={e} onJump={onJump} /></span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      </div>

      <aside className="space-y-4 self-start">
        <Side icon={<CheckCircle2 size={16} className="text-marine" />} title="Strengths" items={card.strengths} onJump={onJump} />
        <Side icon={<CircleAlert size={16} className="text-alarm" />} title="Concerns" items={card.concerns} onJump={onJump} />
        <Side icon={<CheckCircle2 size={16} className="text-marine" />} title="Resume claims that held up" items={card.claims_verified} onJump={onJump} />
        <Side icon={<CircleAlert size={16} className="text-amber" />} title="Claims not verified or inconsistent" items={card.claims_unverified_or_inconsistent} onJump={onJump} />
        <Side icon={<Lightbulb size={16} className="text-amber" />} title="Focus for the next round" items={card.suggested_next_round_focus} onJump={onJump} />
        {card.coding?.attempted && (
          <div className="rounded-2xl border border-line bg-surface p-4 text-[15px]">
            <h4 className="font-semibold">Coding</h4>
            <p className="mt-1 text-graphite">{card.coding.problem}: {card.coding.runs} runs, {card.coding.submits} submits</p>
            {Object.keys(card.coding.by_kind ?? {}).length > 0 &&
              <p className="text-graphite">{Object.entries(card.coding.by_kind).map(([k, v]: any) => `${k} ${v.passed}/${v.total}`).join(", ")}</p>}
            {card.coding.compile_error && <p className="mt-1 font-mono text-[13px] text-alarm">{card.coding.compile_error}</p>}
            {card.elo && <p className="mt-1 text-sm text-mute">Problem rating now {card.elo.problem_elo}; candidate rated {card.elo.theta}</p>}
          </div>
        )}
        <div className="rounded-2xl border border-line bg-surface p-4">
          {!edit ? (
            <>
              <h4 className="font-semibold">Your review</h4>
              <p className="mt-1 text-sm text-graphite">
                {d.reviewer_overrides?.note || "Agree or disagree with the model. Disagreements are logged and used to calibrate future scoring."}
              </p>
              <button className="mt-3 rounded-lg border border-ink/25 px-3 py-1.5 text-sm font-medium hover:bg-paper" onClick={() => setEdit(true)}>
                Review and override
              </button>
            </>
          ) : (
            <form className="space-y-3" onSubmit={async (e) => {
              e.preventDefault();
              await api(`/api/interviews/${d.id}/review`, { method: "POST", body: JSON.stringify({ scores, recommendation: rec || null, note }) });
              setEdit(false);
              onSaved();
            }}>
              <p className="text-sm text-graphite">Change any score on the left, then:</p>
              <label className="block text-sm font-semibold">Outcome
                <select className="mt-1 w-full rounded-lg border border-line px-2 py-2 font-normal" value={rec} onChange={(e) => setRec(e.target.value)}>
                  {Object.entries(RECOMMENDATION).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                </select>
              </label>
              <label className="block text-sm font-semibold">Note
                <textarea className="mt-1 w-full rounded-lg border border-line p-2 font-normal" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="flex gap-2">
                <button className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-on-primary">Save review</button>
                <button type="button" className="rounded-lg px-3 py-1.5 text-sm text-graphite" onClick={() => setEdit(false)}>Cancel</button>
              </div>
            </form>
          )}
        </div>
      </aside>
    </div>
  );
}

function Side({ icon, title, items, onJump }: { icon: React.ReactNode; title: string; items?: string[]; onJump: (n: number) => void }) {
  if (!items?.length) return null;
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <h4 className="flex items-center gap-2 font-semibold">{icon}{title}</h4>
      <ul className="mt-2 space-y-2 text-[15px] text-graphite">{items.map((x, i) => <li key={i}><WithTurns text={x} onJump={onJump} /></li>)}</ul>
    </div>
  );
}

function Transcript({ d, focus }: { d: any; focus: number | null }) {
  useTheme(); // re-render the chart's literal colours when the theme changes
  const turns = d.turns.filter((t: any) => t.speaker !== "system");
  const latency = d.turns
    .filter((t: any) => t.speaker === "interviewer" && (t.metrics?.voice_to_voice_ms || t.metrics?.first_audio_ms))
    .map((t: any) => ({ seq: t.seq, v2v: t.metrics.voice_to_voice_ms, server: t.metrics.first_audio_ms, llm: t.metrics.llm_ttft_ms }));
  const vals = latency.map((l: any) => l.v2v ?? l.server).filter(Boolean).sort((a: number, b: number) => a - b);
  const median = vals.length ? vals[Math.floor(vals.length / 2)] : null;
  const name = d.candidate?.split(" ")[0] || "Candidate";
  let lastPhase = "";
  return (
    <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_320px]">
      <ol className="space-y-3">
        {turns.map((t: any) => {
          const header = t.phase !== lastPhase ? PHASES.find((p) => p.key === t.phase)?.label ?? t.phase : null;
          lastPhase = t.phase;
          const mine = t.speaker === "candidate";
          const m = t.metrics || {};
          return (
            <li key={t.seq} id={`turn-${t.seq}`}>
              {header && (
                <div className="my-5 flex items-center gap-3 text-sm font-semibold text-graphite">
                  <span>{header}</span><span className="h-px flex-1 bg-line" />
                </div>
              )}
              <div className={"flex items-end gap-2.5 " + (mine ? "flex-row-reverse" : "")}>
                <Avatar name={mine ? name : "Alex"} tone={mine ? "marine" : "amber"} size={30} />
                <div className={"max-w-[78%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed " +
                  (mine ? "rounded-br-md bg-marine-soft/70" : "rounded-bl-md border border-line bg-surface") +
                  (focus === t.seq ? " ring-2 ring-amber" : "")}>
                  {t.text}
                  <div className={"mt-1 flex items-center gap-2 text-xs text-mute " + (mine ? "justify-end" : "")}>
                    <span>turn {t.seq}{t.channel === "text" ? " · typed" : ""}</span>
                    {!mine && <LatencyChip ms={m.voice_to_voice_ms ?? m.first_audio_ms} />}
                    {m.barged_in && <span>cut off</span>}
                    {m.granted_time && <span>gave thinking time</span>}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      <aside className="self-start rounded-2xl border border-line bg-surface p-5 lg:sticky lg:top-6">
        <h3 className="font-semibold">Reply speed</h3>
        <p className="text-sm text-mute">Candidate stops talking → interviewer's first word</p>
        {median != null && <p className="mt-2 font-display text-3xl font-semibold">{(median / 1000).toFixed(1)}s <span className="text-base font-normal text-mute">median</span></p>}
        {latency.length ? (
          <div className="mt-3 h-52">
            <ResponsiveContainer>
              <LineChart data={latency} margin={{ left: -14, right: 6, top: 6 }}>
                <CartesianGrid stroke={token("line")} strokeDasharray="3 3" />
                <XAxis dataKey="seq" tick={{ fontSize: 11, fill: token("mute") }} stroke={token("line")} />
                <YAxis tick={{ fontSize: 11, fill: token("mute") }} stroke={token("line")} tickFormatter={(v) => `${v / 1000}s`} />
                <Tooltip formatter={(v: any) => `${(Number(v) / 1000).toFixed(2)}s`} labelFormatter={(l) => `turn ${l}`}
                  contentStyle={{ background: token("surface"), border: `1px solid ${token("line")}`, color: token("ink") }} />
                <Line type="monotone" dataKey="v2v" name="heard in browser" stroke={token("marine")} dot={false} strokeWidth={2} connectNulls />
                <Line type="monotone" dataKey="llm" name="model first token" stroke={token("graphite")} dot={false} strokeDasharray="4 3" connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : <p className="mt-3 text-sm text-graphite">No voice turns recorded.</p>}
        <p className="mt-2 text-xs text-mute">The dashed line is the language model alone: the gap between the lines is speech-to-text and voice.</p>
      </aside>
    </div>
  );
}

function Code({ d }: { d: any }) {
  const runs = d.code_runs;
  const [sel, setSel] = useState(runs.length - 1);
  if (!d.problem) return <p className="mt-10 text-graphite">No coding problem was planned.</p>;
  const run = runs[sel];
  return (
    <div className="mt-8">
      <p className="text-lg"><span className="font-semibold">{d.problem.title}</span> <span className="text-mute">{d.problem.difficulty}, rated {Math.round(d.problem.elo)}</span></p>
      {!runs.length ? <p className="mt-4 text-graphite">The candidate didn't run any code.</p> : (
        <div className="mt-4 grid gap-6 lg:grid-cols-[230px_1fr]">
          <ol className="space-y-1.5">
            {runs.map((r: any, i: number) => {
              const s = r.summary || {};
              const ratio = s.total ? s.passed / s.total : 0;
              return (
                <li key={r.id}>
                  <button onClick={() => setSel(i)}
                    className={"w-full rounded-xl border px-3 py-2 text-left " + (i === sel ? "border-ink bg-surface" : "border-line bg-white/60 hover:bg-surface")}>
                    <div className="flex justify-between text-sm">
                      <span className="font-semibold">{r.mode === "submit" ? "Submit" : "Run"} {i + 1}</span>
                      <span className="text-mute tabular-nums">{s.compile_error ? "error" : `${s.passed ?? 0}/${s.total ?? 0}`}</span>
                    </div>
                    <div className="mt-1.5 h-1 rounded-full bg-line"><div className={"h-full rounded-full " + (ratio === 1 ? "bg-marine" : "bg-amber")} style={{ width: `${ratio * 100}%` }} /></div>
                    <div className="mt-1 text-xs text-mute">{fmtDate(r.at)}</div>
                  </button>
                </li>
              );
            })}
          </ol>
          <div className="min-w-0">
            <pre className="overflow-x-auto rounded-2xl bg-[#272727] p-5 font-mono text-[13px] leading-relaxed text-[#e6e6e6]">{run.code}</pre>
            {run.summary?.compile_error && <p className="mt-3 font-mono text-[13px] text-alarm">{run.summary.compile_error}</p>}
            {run.first_failure && (
              <p className="mt-3 text-sm"><span className="font-semibold">First failure ({run.first_failure.kind})</span>: {run.first_failure.status}
                {run.first_failure.error && `: ${run.first_failure.error}`}<br />
                <span className="font-mono text-[13px] break-all text-graphite">{run.first_failure.input}</span></p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function InterviewerReview({ d, onJump }: { d: any; onJump: (n: number) => void }) {
  const c = d.critique || {};
  if (!c.overall) return <p className="mt-10 text-graphite">After scoring, the interviewer reviews how it ran this interview. It takes a few minutes; this page updates by itself.</p>;
  return (
    <div className="mt-8 grid gap-6">
      <p className="max-w-[72ch] text-lg leading-relaxed">{c.overall}</p>
      <div className="grid gap-4 md:grid-cols-3">
        <Block title="Timing" text={c.timing} />
        <Block title="Hinting" text={c.hinting} />
        <Block title="Candidate experience" text={c.candidate_experience} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Side icon={<CircleAlert size={16} className="text-amber" />} title="Probes it missed" items={c.missed_probes} onJump={onJump} />
        <Side icon={<CircleAlert size={16} className="text-alarm" />} title="Leading questions" items={c.leading_questions} onJump={onJump} />
        <Side icon={<CircleAlert size={16} className="text-alarm" />} title="Guardrail issues" items={c.guardrail_issues} onJump={onJump} />
      </div>
      {c.new_lessons?.length > 0 && (
        <section className="rounded-2xl border border-line bg-surface p-5">
          <h3 className="font-display text-lg font-semibold">Lessons it proposed for future interviews</h3>
          <ul className="mt-3 space-y-3">
            {c.new_lessons.map((l: any, i: number) => (
              <li key={i}>
                <span className="mr-2 rounded-full bg-paper-2 px-2 py-0.5 text-xs text-graphite">{PHASES.find((p) => p.key === l.scope)?.label ?? l.scope}</span>
                <span className="font-medium">{l.lesson}</span>
              </li>
            ))}
          </ul>
          <Link to="/learning" className="mt-4 inline-block rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-on-primary">Review lessons</Link>
        </section>
      )}
    </div>
  );
}

function Block({ title, text }: { title: string; text?: string }) {
  if (!text) return null;
  return <div className="rounded-2xl border border-line bg-surface p-4"><h4 className="font-semibold">{title}</h4><p className="mt-1 text-[15px] text-graphite">{text}</p></div>;
}

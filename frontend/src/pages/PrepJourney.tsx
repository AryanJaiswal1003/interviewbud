import { ArrowLeft, Check, ChevronDown, ExternalLink, FileUp, Pencil, Play, Plus, Quote, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { RecPill, ScoreStrip } from "../components/ui";
import { track } from "../lib/analytics";
import { api, fmtDate } from "../lib/api";
import { PREP_STATUS } from "./Prep";

type Step = "job" | "you" | "plan" | "practice" | "feedback";
const STEPS: { key: Step; label: string }[] = [
  { key: "job", label: "The job" }, { key: "you", label: "About you" }, { key: "plan", label: "Plan" },
  { key: "practice", label: "Practice" }, { key: "feedback", label: "Feedback" },
];
const TRANSIENT = new Set(["intake", "planning"]);
const NEXT_LABEL: Record<string, string> = {
  needs_text: "Paste the text", failed: "Fix it", context: "Answer the question", plan_ready: "Practise now",
  practicing: "Go to practice", reviewed: "See feedback",
};
const COVER: Record<string, { label: string; cls: string }> = {
  supported: { label: "You've shown this", cls: "bg-marine-soft text-marine-ink" },
  needs_clarification: { label: "Needs detail", cls: "bg-amber-soft" },
  unknown: { label: "Not covered yet", cls: "bg-paper-2 text-graphite" },
};

function stepFor(status: string, d: any): Step {
  if (["intake", "needs_text", "failed"].includes(status)) return "job";
  if (status === "context") return "you";
  if (status === "planning" || status === "plan_ready") return d.practices?.length ? "practice" : "plan";
  if (status === "practicing") return "practice";
  return "feedback";
}

export default function PrepJourney() {
  const { id = "" } = useParams();
  const [d, setD] = useState<any>(null);
  const [step, setStep] = useState<Step | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const x = await api(`/api/prep/${id}`);
      setD(x);
      setStep((s) => s ?? stepFor(x.status, x));
      return x;
    } catch (e: any) {
      setErr(e.message);
    }
  }, [id]);

  // Poll faster while something is being worked on in the background.
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      const x = await load();
      if (stop) return;
      const busy = x && (TRANSIENT.has(x.status) || x.practices?.some((p: any) => !["scored", "failed"].includes(p.status) || p.reviewing)
        || (x.status === "context" && !x.questions.some((q: any) => q.status === "open") && x.pending_action.startsWith("Answer")));
      timer.current = window.setTimeout(tick, busy ? 3000 : 12000);
    };
    tick();
    return () => { stop = true; window.clearTimeout(timer.current); };
  }, [load]);

  if (err) {
    return (
      <div className="mx-auto max-w-xl px-6 py-16 text-center">
        <h1 className="font-display text-2xl font-semibold">{err === "Not Found" ? "We couldn't find that preparation" : "Something went wrong"}</h1>
        <p className="mt-2 text-graphite">{err === "Not Found" ? "It may have been deleted, or it belongs to another account." : err}</p>
        <Link to="/prep" className="mt-5 inline-block rounded-lg bg-primary px-4 py-2 font-semibold text-on-primary">Your preparations</Link>
      </div>
    );
  }
  if (!d) return <div className="p-10 text-graphite">Loading…</div>;
  const o = d.opportunity, ex = o.extraction || {};
  const s = PREP_STATUS[d.status] ?? { label: d.status, tone: "bg-paper-2" };
  const current = stepFor(d.status, d);

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 md:px-10">
      <Link to="/prep" className="inline-flex items-center gap-1.5 text-sm text-graphite hover:text-ink"><ArrowLeft size={15} /> Preparations</Link>
      <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-[30px] leading-tight font-semibold tracking-tight">
            {ex.title || o.title || (TRANSIENT.has(d.status) ? "Reading the posting…" : "Job description")}
          </h1>
          <p className="text-graphite">
            {[ex.company, ex.location].filter(Boolean).join(" · ")}
            {o.source_url && <> · <a className="inline-flex items-center gap-1 underline" href={o.final_url || o.source_url} target="_blank" rel="noreferrer noopener">source <ExternalLink size={13} /></a></>}
            {o.fetch_method && <span className="ml-2 text-sm text-mute">read via {o.fetch_method === "pasted" ? "pasted text" : o.fetch_method}</span>}
          </p>
        </div>
        <span className={`rounded-full px-3 py-1 text-sm whitespace-nowrap ${s.tone}`}>{s.label}</span>
      </header>
      <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl bg-paper-2 px-4 py-2.5 text-[15px]">
        <span className="min-w-0 flex-1"><span className="font-semibold">Next:</span> {d.pending_action || "—"}
          {TRANSIENT.has(d.status) && <span className="text-graphite"> (about a minute; you can leave and come back)</span>}</span>
        {!TRANSIENT.has(d.status) && step !== current && (
          <button onClick={() => setStep(current)} className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-on-primary hover:bg-primary-2">
            {NEXT_LABEL[d.status] ?? "Go"}
          </button>
        )}
      </div>

      <nav className="mt-6 flex flex-wrap gap-2" aria-label="Preparation steps">
        {STEPS.map((x, i) => {
          const idx = STEPS.findIndex((y) => y.key === current);
          return (
            <button key={x.key} onClick={() => setStep(x.key)} aria-current={step === x.key ? "step" : undefined}
              className={"flex items-center gap-2 rounded-full px-3.5 py-1.5 text-sm " + (step === x.key ? "bg-primary text-on-primary font-semibold" : "bg-surface border border-line hover:bg-paper")}>
              <span className={"flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold " + (i < idx ? "bg-marine text-white" : i === idx ? "bg-amber text-onaccent" : "bg-paper-2 text-graphite")}>
                {i < idx ? <Check size={12} /> : i + 1}
              </span>
              {x.label}
            </button>
          );
        })}
      </nav>

      <div className="mt-6">
        {step === "job" && <JobStep d={d} reload={load} />}
        {step === "you" && <YouStep d={d} reload={load} goPlan={() => setStep("plan")} />}
        {step === "plan" && <PlanStep d={d} reload={load} goPractice={() => setStep("practice")} />}
        {step === "practice" && <PracticeStep d={d} reload={load} />}
        {step === "feedback" && <FeedbackStep d={d} goPractice={() => setStep("practice")} />}
      </div>
    </div>
  );
}

function Card({ title, sub, children, right }: { title: string; sub?: React.ReactNode; children?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="font-display text-lg font-semibold">{title}</h2>{sub && <p className="mt-0.5 text-sm text-graphite">{sub}</p>}</div>
        {right}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </section>
  );
}

function Sourced({ item }: { item: any }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="inline">
      {item.verified ? (
        <button onClick={() => setOpen(!open)} className="ml-2 inline-flex items-center gap-1 rounded-full bg-marine-soft px-2 py-0.5 align-middle text-xs text-marine-ink" aria-expanded={open}
          title="Found word for word in the posting">
          <Quote size={11} /> from the posting <ChevronDown size={11} />
        </button>
      ) : (
        <span className="ml-2 rounded-full bg-amber-soft px-2 py-0.5 align-middle text-xs" title="Couldn't find this wording in the page: treat as interpretation">interpretation</span>
      )}
      {open && <span className="mt-1 block border-l-2 border-marine pl-3 text-sm text-graphite italic">“{item.quote}”</span>}
    </span>
  );
}

function JobStep({ d, reload }: { d: any; reload: () => void }) {
  const o = d.opportunity, ex = o.extraction || {};
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  if (d.status === "needs_text" || d.status === "failed") {
    return (
      <Card title="We couldn't read that page" sub={o.fetch_error || d.error || "Paste the job description and we'll carry on."}>
        <form onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          await api(`/api/prep/${d.id}/text`, { method: "POST", body: JSON.stringify({ text }) });
          setBusy(false);
          reload();
        }}>
          <textarea required minLength={200} rows={10} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="Paste the posting: title, responsibilities, requirements and any application questions."
            className="w-full rounded-lg border border-line bg-surface px-3 py-2.5" />
          <div className="mt-3 flex gap-2">
            <button disabled={busy} className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary disabled:opacity-50">{busy ? "Reading…" : "Use this text"}</button>
            {o.source_url && <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm" onClick={async () => { await api(`/api/prep/${d.id}/retry`, { method: "POST" }); reload(); }}><RotateCcw size={14} /> Try the link again</button>}
          </div>
        </form>
      </Card>
    );
  }
  if (!ex.requirements) return <Card title="Reading the posting…" sub="Fetching the page and pulling out the requirements. About a minute." />;
  const reqs = ex.requirements as any[];
  const verified = [...reqs, ...(ex.skills || []), ...(ex.responsibilities || [])].filter((x) => x.verified).length;
  const total = reqs.length + (ex.skills || []).length + (ex.responsibilities || []).length;
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
      <div className="space-y-5">
        <Card title="Requirements" sub={`${verified} of ${total} extracted items were found word for word in the posting. Tap "from the posting" to see the exact text.`}>
          <ul className="space-y-2.5">
            {reqs.map((r) => {
              const c = d.coverage?.[r.id];
              return (
                <li key={r.id} className="flex gap-3">
                  <span className="mt-0.5 w-8 shrink-0 text-sm text-mute">{r.id}</span>
                  <div className="min-w-0 flex-1 text-[15px]">
                    <span className={r.must_have ? "font-medium" : ""}>{r.text}</span>
                    {!r.must_have && <span className="ml-2 text-xs text-mute">nice to have</span>}
                    <Sourced item={r} />
                  </div>
                  {c && <span className={`h-fit shrink-0 rounded-full px-2 py-0.5 text-xs whitespace-nowrap ${COVER[c.status]?.cls}`} title={c.note}>{COVER[c.status]?.label}</span>}
                </li>
              );
            })}
          </ul>
        </Card>
        {ex.responsibilities?.length > 0 && (
          <Card title="What you'd do">
            <ul className="list-disc space-y-1.5 pl-5 text-[15px]">{ex.responsibilities.map((r: any) => <li key={r.id}>{r.text}<Sourced item={r} /></li>)}</ul>
          </Card>
        )}
        <Card title="Application questions" sub={ex.application_questions?.length ? "Drafts for these appear in your plan." : "This posting doesn't show any application questions."}>
          {ex.application_questions?.length > 0 && <ul className="space-y-2 text-[15px]">{ex.application_questions.map((q: any) => <li key={q.id}><span className="text-mute">{q.id}</span> {q.question}{q.required && <span className="ml-2 text-xs text-alarm">required</span>}<Sourced item={q} /></li>)}</ul>}
        </Card>
      </div>
      <aside className="space-y-5 self-start">
        <Card title="Skills named">
          <div className="flex flex-wrap gap-1.5">{(ex.skills || []).map((s: any) => <span key={s.id} className="rounded-full bg-paper-2 px-2.5 py-1 text-sm" title={s.verified ? `“${s.quote}”` : "interpretation"}>{s.text}</span>)}</div>
        </Card>
        <Card title="Our reading" sub="Interpretation, not stated in the posting.">
          <p className="text-[15px]">{o.interpretation?.summary}</p>
          <p className="mt-2 text-sm text-graphite">Level: {o.interpretation?.seniority_guess} · {o.interpretation?.technical_role ? "includes coding" : "no coding round"}</p>
          {o.interpretation?.likely_interview_focus?.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm text-graphite">{o.interpretation.likely_interview_focus.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul>}
        </Card>
      </aside>
    </div>
  );
}

function YouStep({ d, reload, goPlan }: { d: any; reload: () => void; goPlan: () => void }) {
  const open = d.questions.find((q: any) => q.status === "open");
  const answered = d.questions.filter((q: any) => q.status !== "open");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const req = (rid: string) => (d.opportunity.extraction?.requirements || []).find((r: any) => r.id === rid);
  const supported = Object.values(d.coverage || {}).filter((c: any) => c.status === "supported").length;
  const total = Object.keys(d.coverage || {}).length;
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <div className="space-y-5">
        {open ? (
          <Card title="One question" sub={open.why}>
            <p className="font-display text-xl leading-snug">{open.question}</p>
            {req(open.requirement_id) && <p className="mt-1 text-sm text-mute">About {open.requirement_id}: {req(open.requirement_id).text}</p>}
            <form className="mt-4" onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              await api(`/api/prep/${d.id}/questions/${open.id}/answer`, { method: "POST", body: JSON.stringify({ answer }) });
              track("prep_answered");
              setAnswer("");
              setBusy(false);
              reload();
            }}>
              <textarea required rows={4} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Answer in your own words. Specifics help: what you did, with what, how much."
                className="w-full rounded-lg border border-line bg-surface px-3 py-2.5" />
              <div className="mt-3 flex flex-wrap gap-2">
                <button disabled={busy} className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary disabled:opacity-50">{busy ? "Saving…" : "Save answer"}</button>
                <button type="button" className="rounded-lg border border-line px-3 py-2 text-sm" onClick={async () => { await api(`/api/prep/${d.id}/questions/${open.id}/skip`, { method: "POST" }); reload(); }}>Skip this one</button>
              </div>
            </form>
          </Card>
        ) : (
          <Card title={d.pending_action.startsWith("Answer") ? "Thinking of the next question…" : "No more questions for now"}
            sub={`${supported} of ${total} requirements are backed by something you've told us.`} />
        )}
        <Card title="Ready when you are" sub="You can build the plan at any point; anything still unknown becomes something to practise.">
          <button className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2" onClick={async () => {
            await api(`/api/prep/${d.id}/plan`, { method: "POST", body: JSON.stringify({}) });
            track("prep_plan_requested");
            reload();
            goPlan();
          }}>Build my preparation plan</button>
        </Card>
        {answered.length > 0 && (
          <Card title="Earlier questions">
            <ul className="space-y-3">{answered.map((q: any) => (
              <li key={q.id} className="text-[15px]"><div className="text-graphite">{q.question}</div>
                <div className="mt-0.5">{q.status === "skipped" ? <span className="text-mute">Skipped</span> : q.answer}</div></li>
            ))}</ul>
          </Card>
        )}
      </div>
      <Facts d={d} reload={reload} />
    </div>
  );
}

function Facts({ d, reload }: { d: any; reload: () => void }) {
  const [editing, setEditing] = useState<number | null>(null);
  const [val, setVal] = useState("");
  const [adding, setAdding] = useState(false);
  const [nf, setNf] = useState({ topic: "", value: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const patch = async (id: number, body: any) => { await api(`/api/profile/facts/${id}`, { method: "PATCH", body: JSON.stringify(body) }); setEditing(null); reload(); };
  return (
    <Card title="What we know about you" sub="Shared across all your preparations. Only confirmed facts are used; correct anything that's off.">
      <ul className="space-y-3">
        {d.facts.map((f: any) => (
          <li key={f.id} className="rounded-xl border border-line p-3">
            <div className="flex items-center gap-2 text-sm">
              <span className="min-w-0 flex-1 truncate font-semibold">{f.topic}</span>
              <span className={"rounded-full px-2 py-0.5 text-xs " + (f.status === "confirmed" ? "bg-marine-soft text-marine-ink" : "bg-amber-soft")}>{f.status === "confirmed" ? "confirmed" : "suggested"}</span>
            </div>
            {editing === f.id ? (
              <form className="mt-2" onSubmit={(e) => { e.preventDefault(); patch(f.id, { value: val, status: "confirmed" }); }}>
                <textarea rows={3} value={val} onChange={(e) => setVal(e.target.value)} className="w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm" />
                <div className="mt-1.5 flex gap-2 text-sm"><button className="rounded-md bg-primary px-3 py-1 font-semibold text-on-primary">Save</button><button type="button" onClick={() => setEditing(null)} className="text-graphite">Cancel</button></div>
              </form>
            ) : (
              <p className="mt-1 text-sm text-graphite">{f.value}</p>
            )}
            {editing !== f.id && (
              <div className="mt-2 flex gap-3 text-xs">
                {f.status === "suggested" && <button className="inline-flex items-center gap-1 font-semibold text-marine-ink" onClick={() => patch(f.id, { status: "confirmed" })}><Check size={12} /> Confirm</button>}
                <button className="inline-flex items-center gap-1 text-graphite" onClick={() => { setEditing(f.id); setVal(f.value); }}><Pencil size={12} /> Correct</button>
                <button className="inline-flex items-center gap-1 text-graphite" onClick={() => patch(f.id, { status: "retracted" })}><X size={12} /> Remove</button>
              </div>
            )}
          </li>
        ))}
        {d.facts.length === 0 && <li className="text-sm text-graphite">Nothing yet. Answer the question, add a fact, or import a resume as suggestions.</li>}
      </ul>
      {adding ? (
        <form className="mt-3 space-y-2" onSubmit={async (e) => { e.preventDefault(); await api("/api/profile/facts", { method: "POST", body: JSON.stringify(nf) }); setNf({ topic: "", value: "" }); setAdding(false); reload(); }}>
          <input required placeholder="Topic, e.g. Kubernetes" value={nf.topic} onChange={(e) => setNf({ ...nf, topic: e.target.value })} className="w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm" />
          <textarea required rows={2} placeholder="What you did, in your words" value={nf.value} onChange={(e) => setNf({ ...nf, value: e.target.value })} className="w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm" />
          <div className="flex gap-2 text-sm"><button className="rounded-md bg-primary px-3 py-1 font-semibold text-on-primary">Add</button><button type="button" onClick={() => setAdding(false)} className="text-graphite">Cancel</button></div>
        </form>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2 text-sm">
          <button onClick={() => setAdding(true)} className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5"><Plus size={14} /> Add a fact</button>
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line px-3 py-1.5">
            <FileUp size={14} /> Import resume as suggestions
            <input type="file" accept=".pdf,.docx,.txt,.md" className="sr-only" onChange={async (e) => {
              const file = e.target.files?.[0]; if (!file) return;
              setMsg("Reading your resume…");
              const fd = new FormData(); fd.append("file", file);
              try { const r = await api<{ suggested: number }>("/api/profile/resume", { method: "POST", body: fd }); setMsg(`${r.suggested} suggestions added. Confirm the ones that are right.`); }
              catch (er: any) { setMsg(er.message); }
              reload();
            }} />
          </label>
        </div>
      )}
      {msg && <p className="mt-2 text-sm text-graphite" role="status">{msg}</p>}
    </Card>
  );
}

function PlanStep({ d, reload, goPractice }: { d: any; reload: () => void; goPractice: () => void }) {
  const [request, setRequest] = useState("");
  const p = d.plan;
  if (d.status === "planning" && !p) return <Card title="Building your plan…" sub="Matching the requirements with what you've told us. Under a minute." />;
  if (!p) return <Card title="No plan yet" sub="Build one from the About you step whenever you're ready." />;
  return (
    <div className="space-y-5">
      <Card title={`Preparation plan, version ${p.version}`} sub={d.plans.length > 1 ? `Earlier versions are kept: ${d.plans.map((x: any) => `v${x.version} (${x.note || "generated"})`).join(", ")}` : undefined}
        right={<button onClick={goPractice} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2"><Play size={15} /> Practise this plan</button>}>
        <p className="text-[15px]">{p.summary}</p>
        <p className="mt-2 text-sm text-graphite">Level: {p.seniority} · {p.include_coding ? "with a coding exercise" : "no coding round"} · question types: {p.question_types.join(", ")}</p>
        {d.status === "planning" && <p className="mt-2 text-sm text-amber">Revising… a new version will appear here.</p>}
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        {p.topics.map((t: any, i: number) => (
          <section key={i} className="rounded-2xl border border-line bg-surface p-5">
            <div className="flex items-start justify-between gap-2"><h3 className="font-semibold">{t.title}</h3><span className="rounded-full bg-paper-2 px-2 py-0.5 text-xs whitespace-nowrap">{t.question_type.replace(/_/g, " ")}</span></div>
            <p className="mt-1 text-sm text-graphite">{t.why} <span className="text-mute">({t.requirement_ids.join(", ")})</span></p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-[15px]">{t.example_questions.map((q: string, j: number) => <li key={j}>{q}</li>)}</ul>
            <p className="mt-2 text-sm"><span className="text-mute">Your angle:</span> {t.candidate_angle}</p>
          </section>
        ))}
      </div>
      {p.gaps_to_practice.length > 0 && <Card title="Gaps to practise" sub="Requirements where we don't have confirmed evidence from you yet."><ul className="list-disc space-y-1 pl-5 text-[15px]">{p.gaps_to_practice.map((g: string, i: number) => <li key={i}>{g}</li>)}</ul></Card>}
      {p.answer_drafts.length > 0 && <Drafts d={d} reload={reload} />}
      <Card title="Revise the plan" sub="Say what to change; you'll get a new version and keep the old one.">
        <form className="flex flex-wrap gap-2" onSubmit={async (e) => { e.preventDefault(); await api(`/api/prep/${d.id}/plan`, { method: "POST", body: JSON.stringify({ request }) }); setRequest(""); reload(); }}>
          <input value={request} onChange={(e) => setRequest(e.target.value)} placeholder="e.g. More system design, less behavioural" className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2" />
          <button disabled={!request.trim() || d.status === "planning"} className="rounded-lg border border-line px-4 py-2 text-sm font-medium disabled:opacity-50">Revise</button>
        </form>
      </Card>
    </div>
  );
}

function Drafts({ d, reload }: { d: any; reload: () => void }) {
  const p = d.plan;
  const qs = d.opportunity.extraction?.application_questions || [];
  const [edit, setEdit] = useState<Record<string, string>>({});
  return (
    <Card title="Application answers" sub="Suggested drafts built only from your confirmed facts. [Brackets] mark what's missing. Nothing is submitted anywhere.">
      <div className="space-y-4">
        {p.answer_drafts.map((a: any) => {
          const q = qs.find((x: any) => x.id === a.question_id);
          const val = edit[a.question_id] ?? a.draft;
          return (
            <div key={a.question_id} className="rounded-xl border border-line p-4">
              <div className="flex items-center justify-between gap-2"><p className="font-semibold">{q?.question ?? a.question_id}</p>
                <span className={"rounded-full px-2 py-0.5 text-xs " + (a.approved ? "bg-marine-soft text-marine-ink" : "bg-amber-soft")}>{a.approved ? "approved by you" : "suggested"}</span></div>
              <textarea rows={5} value={val} onChange={(e) => setEdit({ ...edit, [a.question_id]: e.target.value })} className="mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2 text-[15px]" />
              {a.missing?.length > 0 && <p className="mt-1 text-sm text-graphite">Missing: {a.missing.join("; ")}</p>}
              <div className="mt-2 flex gap-2 text-sm">
                <button className="rounded-md border border-line px-3 py-1" onClick={async () => { await api(`/api/prep/${d.id}/drafts/${a.question_id}`, { method: "PUT", body: JSON.stringify({ draft: val, approved: false }) }); reload(); }}>Save edit</button>
                <button className="rounded-md bg-marine px-3 py-1 font-semibold text-white" onClick={async () => { await api(`/api/prep/${d.id}/drafts/${a.question_id}`, { method: "PUT", body: JSON.stringify({ draft: val, approved: true }) }); reload(); }}>Approve</button>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function PracticeStep({ d, reload }: { d: any; reload: () => void }) {
  const nav = useNavigate();
  const [duration, setDuration] = useState(15);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="space-y-5">
      <Card title="Practice interview" sub={d.plan ? `A voice interview built from plan version ${d.plan.version}: the same room, interviewer and scoring as a real screen.` : "Build a plan first."}>
        {d.plan && (
          <div className="flex flex-wrap items-center gap-3">
            <select value={duration} onChange={(e) => setDuration(Number(e.target.value))} aria-label="Length" className="rounded-lg border border-line bg-surface px-3 py-2">
              {[15, 30, 45].map((m) => <option key={m} value={m}>{m} minutes</option>)}
            </select>
            <button disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 font-semibold text-on-primary hover:bg-primary-2 disabled:opacity-50" onClick={async () => {
              setBusy(true); setErr(null);
              try {
                const r = await api<{ interview_id: string }>(`/api/prep/${d.id}/practice`, { method: "POST", body: JSON.stringify({ duration_min: duration }) });
                track("practice_started", { duration });
                nav(`/interview/${r.interview_id}`);
              } catch (e: any) { setErr(e.message); setBusy(false); reload(); }
            }}><Play size={16} /> {d.practices.length ? "Start another practice" : "Start practice"}</button>
            {err && <span className="text-sm text-alarm">{err}</span>}
          </div>
        )}
      </Card>
      {d.practices.length > 0 && (
        <Card title="Your practice interviews" sub="Each keeps its own transcript, scorecard and feedback.">
          <ul className="divide-y divide-line">
            {d.practices.map((p: any, i: number) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 py-3">
                <span className="w-24 text-sm text-mute">#{d.practices.length - i} · v{p.plan_version}</span>
                <span className="min-w-0 flex-1 text-sm">{fmtDate(p.created_at)} · {p.duration_min} min</span>
                {p.recommendation ? <ScoreStrip scores={p.scores} size="sm" /> : null}
                <RecPill rec={p.recommendation} status={p.status} />
                <Link className="text-sm underline" to={["scored", "completed"].includes(p.status) ? `/dashboard/${p.id}` : `/interview/${p.id}`}>
                  {["scored", "completed"].includes(p.status) ? "Transcript and scorecard" : p.status === "ready" || p.status === "live" ? "Join" : "Open"}
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function FeedbackStep({ d, goPractice }: { d: any; goPractice: () => void }) {
  const done = d.practices.filter((p: any) => d.feedback[p.id]);
  const pending = d.practices.filter((p: any) => !d.feedback[p.id] && ["completed", "scored"].includes(p.status));
  if (!done.length) return <Card title={pending.length ? "Writing your feedback…" : "No feedback yet"} sub={pending.length ? "Scoring the interview and pulling out what to work on. A few minutes." : "Finish a practice interview to get feedback here."} />;
  return (
    <div className="space-y-6">
      {done.map((p: any) => {
        const f = d.feedback[p.id];
        return (
          <section key={p.id} className="rounded-2xl border border-line bg-surface p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h2 className="font-display text-lg font-semibold">Practice on {fmtDate(p.created_at)}</h2><p className="text-sm text-graphite">Plan v{p.plan_version} · {p.duration_min} min</p></div>
              <div className="flex items-center gap-3"><ScoreStrip scores={p.scores} /><RecPill rec={p.recommendation} status={p.status} /></div>
            </div>
            <div className="mt-5 grid gap-5 md:grid-cols-2">
              <List title="What went well" items={f.strengths} tone="text-marine-ink" />
              <List title="Gaps" items={f.gaps} tone="text-alarm" />
            </div>
            <div className="mt-5"><List title="How to answer better" items={f.improvements} tone="" /></div>
            <div className="mt-5">
              <h3 className="font-semibold">Practise next</h3>
              <ul className="mt-2 space-y-2">{f.next_topics.map((t: any, i: number) => <li key={i} className="text-[15px]"><span className="font-medium">{t.topic}</span> <span className="text-mute">({t.requirement_ids.join(", ")})</span><div className="text-sm text-graphite">{t.why}</div></li>)}</ul>
            </div>
            <div className="mt-5 flex flex-wrap gap-3 text-sm">
              <Link className="underline" to={`/dashboard/${p.id}`}>Full transcript and scorecard</Link>
              <button className="font-semibold underline" onClick={goPractice}>Practise again</button>
            </div>
          </section>
        );
      })}
    </div>
  );
}

function List({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  return <div><h3 className={`font-semibold ${tone}`}>{title}</h3><ul className="mt-2 list-disc space-y-1.5 pl-5 text-[15px]">{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>;
}

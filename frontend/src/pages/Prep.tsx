import { ArrowRight, Link2, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageHeader } from "../components/ui";
import { track } from "../lib/analytics";
import { api, fmtDate } from "../lib/api";

export const PREP_STATUS: Record<string, { label: string; tone: string }> = {
  intake: { label: "Reading the posting", tone: "bg-amber-soft" },
  needs_text: { label: "Needs the job text", tone: "bg-coral-soft text-alarm" },
  failed: { label: "Something failed", tone: "bg-coral-soft text-alarm" },
  context: { label: "Getting to know you", tone: "bg-marine-soft text-marine-ink" },
  planning: { label: "Building the plan", tone: "bg-amber-soft" },
  plan_ready: { label: "Plan ready", tone: "bg-marine-soft text-marine-ink" },
  practicing: { label: "Practising", tone: "bg-amber-soft" },
  reviewed: { label: "Feedback ready", tone: "bg-marine-soft text-marine-ink" },
};

export default function Prep() {
  const nav = useNavigate();
  const [mode, setMode] = useState<"url" | "text">("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [rows, setRows] = useState<any[] | null>(null);

  useEffect(() => {
    api("/api/prep").then(setRows).catch(() => setRows([]));
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 md:px-10">
      <PageHeader title="Prepare for a job"
        sub="Start from the job itself. We read the posting, ask what we need to know about you, build a plan, run a practice interview and tell you what to work on." />

      <form className="mt-8 rounded-2xl border border-line bg-surface p-6" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setErr(null);
        try {
          const r = await api<{ id: string }>("/api/prep", {
            method: "POST", body: JSON.stringify(mode === "url" ? { url } : { text }),
          });
          track("prep_started", { mode });
          nav(`/prep/${r.id}`);
        } catch (e: any) {
          setErr(e.message);
          setBusy(false);
        }
      }}>
        <div className="flex gap-1 rounded-lg bg-paper-2 p-1 text-sm" role="tablist" aria-label="Job source">
          {([["url", "Job link", Link2], ["text", "Paste the description", ScrollText]] as const).map(([m, label, Icon]) => (
            <button type="button" key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)}
              className={"flex flex-1 items-center justify-center gap-2 rounded-md py-2 " + (mode === m ? "bg-surface font-semibold shadow-[0_1px_2px_rgba(0,0,0,.12)]" : "text-graphite")}>
              <Icon size={16} /> {label}
            </button>
          ))}
        </div>
        {mode === "url" ? (
          <label className="mt-5 block">
            <span className="text-sm font-semibold">Public job or application link</span>
            <input type="url" required value={url} onChange={(e) => setUrl(e.target.value)}
              placeholder="https://job-boards.greenhouse.io/company/jobs/123456"
              className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5" />
            <span className="mt-1 block text-sm text-mute">Greenhouse, Lever, Ashby and most company career pages work. If a page needs a login we'll ask you to paste the text instead.</span>
          </label>
        ) : (
          <label className="mt-5 block">
            <span className="text-sm font-semibold">Job description</span>
            <textarea required minLength={200} rows={8} value={text} onChange={(e) => setText(e.target.value)}
              placeholder="Paste the whole posting: title, responsibilities, requirements, and any application questions."
              className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5" />
          </label>
        )}
        {err && <p className="mt-3 text-sm text-alarm" role="alert">{err}</p>}
        <button disabled={busy} className="mt-5 inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-3 font-semibold text-on-primary hover:bg-primary-2 disabled:opacity-50">
          {busy ? "Starting…" : <>Start preparing <ArrowRight size={18} /></>}
        </button>
      </form>

      <section className="mt-10">
        <h2 className="font-display text-xl font-semibold">Your preparations</h2>
        {rows && rows.length === 0 && <p className="mt-2 text-graphite">None yet. Paste a job link above to start.</p>}
        <ul className="mt-3 divide-y divide-line rounded-2xl border border-line bg-surface">
          {(rows ?? []).map((r) => {
            const s = PREP_STATUS[r.status] ?? { label: r.status, tone: "bg-paper-2" };
            return (
              <li key={r.id}>
                <Link to={`/prep/${r.id}`} className="flex flex-wrap items-center gap-4 px-5 py-4 hover:bg-paper/60">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-semibold">{r.title}{r.company && <span className="font-normal text-graphite"> · {r.company}</span>}</div>
                    <div className="truncate text-sm text-mute">Next: {r.pending_action} · {fmtDate(r.updated_at)}{r.practices ? ` · ${r.practices} practice${r.practices > 1 ? "s" : ""}` : ""}</div>
                  </div>
                  <span className={`rounded-full px-2.5 py-0.5 text-sm whitespace-nowrap ${s.tone}`}>{s.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

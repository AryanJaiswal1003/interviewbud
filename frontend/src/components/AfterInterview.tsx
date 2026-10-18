import { ArrowUpRight, Briefcase, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { track } from "../lib/analytics";

export type Review = {
  strengths: string[]; gaps: string[]; improvements: string[];
  next_topics: { topic: string; why: string; requirement_ids: string[] }[];
};

/** Feedback from one practice (or screening) call. Every point quotes the candidate or cites a turn. */
export function FeedbackBody({ f, compact }: { f: Review; compact?: boolean }) {
  return (
    <div className="space-y-5">
      <div className={"grid gap-5 " + (compact ? "" : "md:grid-cols-2")}>
        <Points title="What went well" items={f.strengths} tone="text-marine-ink" />
        <Points title="What to work on" items={f.gaps} tone="text-alarm" />
      </div>
      <Points title="How to answer better" items={f.improvements} tone="" />
      {f.next_topics?.length > 0 && (
        <div>
          <h3 className="font-semibold">Practise next</h3>
          <ul className="mt-2 space-y-2">
            {f.next_topics.map((t, i) => (
              <li key={i} className="text-[15px]">
                <span className="font-medium">{t.topic}</span>
                {t.requirement_ids?.length > 0 && <span className="text-mute"> ({t.requirement_ids.join(", ")})</span>}
                <div className="text-sm text-graphite">{t.why}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Points({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  if (!items?.length) return null;
  return (
    <div>
      <h3 className={`font-semibold ${tone}`}>{title}</h3>
      <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[15px]">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}

const FIT: Record<string, { label: string; cls: string }> = {
  strong: { label: "Strong fit", cls: "bg-marine-soft text-marine-ink" },
  good: { label: "Good fit", cls: "bg-amber-soft" },
  stretch: { label: "Stretch", cls: "bg-paper-2 text-graphite" },
};

/** Real openings that fit this candidate, from the company's board and other public boards. */
export function SimilarJobs({ prepId }: { prepId: string }) {
  const nav = useNavigate();
  const [d, setD] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    let t: number | undefined;
    const load = async () => {
      try {
        const x = await api(`/api/prep/${prepId}/jobs`);
        if (stop) return;
        setD(x);
        if (x.status === "working") t = window.setTimeout(load, 4000);
      } catch {
        if (!stop) setD({ status: "failed" });
      }
    };
    load();
    return () => { stop = true; window.clearTimeout(t); };
  }, [prepId]);

  const start = async (url: string) => {
    setBusy(url);
    try {
      const r = await api<{ id: string }>("/api/prep", { method: "POST", body: JSON.stringify({ url }) });
      track("similar_job_prepared");
      nav(`/prep/${r.id}`);
    } catch {
      setBusy(null);
    }
  };

  return (
    <section className="rounded-2xl border border-line bg-surface p-5 text-ink">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 font-display text-lg font-semibold"><Briefcase size={18} /> Jobs you could apply to</h2>
          <p className="mt-0.5 text-sm text-graphite">
            Open roles on real job boards that match what you've told us.
            {d?.searched && ` We searched ${d.pool ?? 0} openings across ${d.searched.length} companies.`}
          </p>
        </div>
        {d?.status === "ready" && (
          <button className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm"
            onClick={async () => { await api(`/api/prep/${prepId}/jobs/refresh`, { method: "POST" }); setD({ ...d, status: "working" }); }}>
            <RefreshCw size={14} /> Search again
          </button>
        )}
      </div>
      <div className="mt-4">
        {!d || d.status === "working" ? (
          <p className="text-sm text-graphite">Looking through job boards… about half a minute.</p>
        ) : d.status === "failed" ? (
          <p className="text-sm text-graphite">The job boards didn't answer just now. Try “Search again” in a minute.</p>
        ) : !d.items?.length ? (
          <p className="text-sm text-graphite">Nothing close enough right now. We'll look again whenever you ask.</p>
        ) : (
          <ul className="divide-y divide-line">
            {d.items.map((j: any) => (
              <li key={j.url} className="flex flex-wrap items-start gap-3 py-3.5">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{j.title}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs ${FIT[j.fit]?.cls}`}>{FIT[j.fit]?.label}</span>
                  </div>
                  <div className="text-sm text-graphite">{j.company}{j.location && <> · {j.location}</>}{j.same_company && " · same company"}</div>
                  <p className="mt-1 text-[15px]">{j.why}</p>
                  {j.gap && <p className="mt-0.5 text-sm text-mute">To show: {j.gap}</p>}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <button disabled={!!busy} onClick={() => start(j.url)}
                    className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-on-primary hover:bg-primary-2 disabled:opacity-50">
                    {busy === j.url ? "Starting…" : "Prepare and apply"}
                  </button>
                  <a href={j.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-sm text-graphite underline">
                    View posting <ArrowUpRight size={13} />
                  </a>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

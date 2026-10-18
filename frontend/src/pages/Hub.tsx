import { ArrowRight, Briefcase, FileText, History, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RecPill } from "../components/ui";
import { api, fmtDate } from "../lib/api";
import { useAuth } from "../lib/auth";
import { PREP_STATUS } from "./Prep";

/** Signed-in landing page: pick up where you left off, or start one of the two journeys. */
export default function Hub() {
  const { user } = useAuth();
  const [preps, setPreps] = useState<any[] | null>(null);
  const [interviews, setInterviews] = useState<any[] | null>(null);
  useEffect(() => {
    api("/api/prep").then(setPreps).catch(() => setPreps([]));
    api("/api/interviews").then(setInterviews).catch(() => setInterviews([]));
  }, []);
  const first = (user?.display_name || "").split(" ")[0];
  const active = (preps ?? []).find((p) => p.status !== "reviewed") ?? (preps ?? [])[0];
  const isNew = preps !== null && interviews !== null && !preps.length && !interviews.length;

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 md:px-10">
      <h1 className="font-display text-[34px] leading-tight font-semibold tracking-tight">{first ? `Hi ${first}` : "Welcome"}</h1>
      <p className="mt-1 text-graphite">{isNew ? "Let's get you ready for your next interview. Pick how you'd like to start." : "Here's where you left off."}</p>

      {active && (
        <Link to={`/prep/${active.id}`} className="group mt-8 flex flex-wrap items-center gap-4 rounded-2xl border border-amber/40 bg-amber-soft/40 p-5 hover:bg-amber-soft/60">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber text-onaccent"><Sparkles size={20} /></span>
          <div className="min-w-0 flex-1">
            <div className="text-sm text-graphite">Continue preparing</div>
            <div className="truncate font-display text-lg font-semibold">{active.title}{active.company && <span className="font-normal text-graphite"> · {active.company}</span>}</div>
            <div className="text-sm"><span className="font-semibold">Next:</span> {active.pending_action}</div>
          </div>
          <span className="inline-flex items-center gap-1.5 font-semibold">Continue <ArrowRight size={18} className="transition-transform group-hover:translate-x-0.5" /></span>
        </Link>
      )}

      <div className="mt-8 grid gap-4 md:grid-cols-2">
        <Choice to="/prep" icon={<Briefcase size={22} />} recommended title="Prepare for a specific job"
          body="Paste a job link. We read the requirements, ask a few questions about you, build a plan, then run a voice practice interview and tell you exactly what to improve."
          cta="Start with a job link" />
        <Choice to="/new" icon={<FileText size={22} />} title="Quick interview from a resume"
          body="Upload a resume and jump straight into a voice screening interview about its projects, with a coding round and a scorecard at the end."
          cta="Upload a resume" />
      </div>

      {(preps?.length || interviews?.length) ? (
        <div className="mt-10 grid gap-8 lg:grid-cols-2">
          <section>
            <div className="flex items-baseline justify-between"><h2 className="font-display text-lg font-semibold">Your preparations</h2><Link to="/prep" className="text-sm text-graphite underline">All</Link></div>
            <ul className="mt-2 divide-y divide-line rounded-2xl border border-line bg-surface">
              {(preps ?? []).slice(0, 4).map((p) => (
                <li key={p.id}><Link to={`/prep/${p.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-paper/60">
                  <span className="min-w-0 flex-1 truncate">{p.title}<span className="text-mute"> · {p.company}</span></span>
                  <span className={`rounded-full px-2 py-0.5 text-xs whitespace-nowrap ${PREP_STATUS[p.status]?.tone ?? "bg-paper-2"}`}>{PREP_STATUS[p.status]?.label ?? p.status}</span>
                </Link></li>
              ))}
              {preps?.length === 0 && <li className="px-4 py-3 text-sm text-graphite">None yet.</li>}
            </ul>
          </section>
          <section>
            <div className="flex items-baseline justify-between"><h2 className="font-display text-lg font-semibold">Recent interviews</h2><Link to="/dashboard" className="inline-flex items-center gap-1 text-sm text-graphite underline"><History size={14} /> History</Link></div>
            <ul className="mt-2 divide-y divide-line rounded-2xl border border-line bg-surface">
              {(interviews ?? []).slice(0, 4).map((r) => (
                <li key={r.id}><Link to={["completed", "scored"].includes(r.status) ? `/dashboard/${r.id}` : `/interview/${r.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-paper/60">
                  <span className="min-w-0 flex-1 truncate">{r.role}<span className="text-mute"> · {fmtDate(r.created_at)}</span></span>
                  <RecPill rec={r.recommendation} status={r.status} />
                </Link></li>
              ))}
              {interviews?.length === 0 && <li className="px-4 py-3 text-sm text-graphite">None yet.</li>}
            </ul>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function Choice({ to, icon, title, body, cta, recommended }: { to: string; icon: React.ReactNode; title: string; body: string; cta: string; recommended?: boolean }) {
  return (
    <Link to={to} className={"group flex flex-col rounded-2xl border bg-surface p-6 hover:border-ink/30 " + (recommended ? "border-amber/50" : "border-line")}>
      <div className="flex items-center gap-3">
        <span className={"flex h-11 w-11 items-center justify-center rounded-xl " + (recommended ? "bg-amber text-onaccent" : "bg-paper-2")}>{icon}</span>
        {recommended && <span className="rounded-full bg-amber-soft px-2.5 py-0.5 text-xs font-medium">Recommended</span>}
      </div>
      <h3 className="mt-4 font-display text-xl font-semibold">{title}</h3>
      <p className="mt-1.5 flex-1 text-[15px] text-graphite">{body}</p>
      <span className="mt-4 inline-flex items-center gap-1.5 font-semibold">{cta} <ArrowRight size={17} className="transition-transform group-hover:translate-x-0.5" /></span>
    </Link>
  );
}

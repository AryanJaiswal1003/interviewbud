import { ArrowDownRight, ArrowUpRight, Check, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/ui";
import { api, PHASES } from "../lib/api";
import { useAuth } from "../lib/auth";

type Lesson = { id: number; scope: string; lesson: string; evidence: string; status: string; helpful: number;
  harmful: number; source_interview_id: string | null };

const START_ELO: Record<string, number> = { Easy: 1200, Medium: 1500, Hard: 1800 };
const scopeLabel = (s: string) => s === "global" ? "Whole interview" : PHASES.find((p) => p.key === s)?.label ?? s;

export default function Learning() {
  const admin = useAuth().user?.is_admin ?? false;
  const [lessons, setLessons] = useState<Lesson[] | null>(null);
  const [cal, setCal] = useState<any[]>([]);
  const load = () => {
    api("/api/learning/lessons").then(setLessons).catch(() => setLessons([]));
    api("/api/learning/calibration").then(setCal).catch(() => {});
  };
  useEffect(load, []);
  const set = async (id: number, status: string) => {
    await api(`/api/learning/lessons/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    load();
  };
  const by = (s: string) => (lessons ?? []).filter((l) => l.status === s);
  const proposed = by("proposed"), active = by("active"), rejected = by("rejected");

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 md:px-10">
      {!admin && (
        <p className="mb-4 rounded-lg bg-paper-2 px-4 py-2.5 text-sm text-graphite">
          Lessons change how the interviewer behaves for everyone, so an admin approves them. You can read them here.
        </p>
      )}
      <PageHeader title="Learning"
        sub="After every interview, the interviewer reviews how it ran the conversation and proposes lessons. A lesson reaches future interviews only once an admin approves it; when later interviews find the same lesson, that counts as support for it." />

      <section className="mt-10">
        <h2 className="font-display text-xl font-semibold">Waiting for your review <span className="ml-1 text-base font-normal text-mute">{proposed.length}</span></h2>
        {lessons !== null && proposed.length === 0 && (
          <p className="mt-3 rounded-2xl border border-dashed border-line px-5 py-6 text-graphite">
            Nothing to review. New lessons show up here a few minutes after an interview is scored.
          </p>
        )}
        <div className="mt-3 grid gap-3">
          {proposed.map((l) => (
            <article key={l.id} className="rounded-2xl border border-line bg-surface p-5">
              <div className="flex flex-wrap items-start gap-4">
                <div className="min-w-0 flex-1">
                  <span className="rounded-full bg-amber-soft px-2.5 py-0.5 text-xs font-medium">{scopeLabel(l.scope)}</span>
                  <p className="mt-2 text-[17px] font-semibold leading-snug">{l.lesson}</p>
                  {l.evidence && <p className="mt-2 text-[15px] text-graphite">{l.evidence}</p>}
                  {l.source_interview_id && (
                    <Link className="mt-2 inline-block text-sm text-graphite underline" to={`/dashboard/${l.source_interview_id}`}>From this interview</Link>
                  )}
                </div>
                {admin && <div className="flex gap-2">
                  <button className="flex items-center gap-1.5 rounded-lg bg-marine px-3 py-2 text-sm font-semibold text-white hover:brightness-110" onClick={() => set(l.id, "active")}>
                    <Check size={16} /> Approve
                  </button>
                  <button className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm hover:bg-paper" onClick={() => set(l.id, "rejected")}>
                    <X size={16} /> Reject
                  </button>
                </div>}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="mt-12">
        <h2 className="font-display text-xl font-semibold">In use <span className="ml-1 text-base font-normal text-mute">{active.length}</span></h2>
        <p className="mt-1 text-sm text-graphite">Shown to the interviewer during the phase they apply to. A lesson that hurts more than it helps is pulled back for review.</p>
        {active.length === 0 ? <p className="mt-3 text-graphite">None yet.</p> : (
          <ul className="mt-3 divide-y divide-line rounded-2xl border border-line bg-surface">
            {active.map((l) => (
              <li key={l.id} className="flex flex-wrap items-center gap-4 px-5 py-3.5">
                <span className="w-28 shrink-0 text-sm text-mute">{scopeLabel(l.scope)}</span>
                <span className="min-w-0 flex-1">{l.lesson}</span>
                <span className="text-sm text-graphite tabular-nums" title="Times the interviewer's later self-reviews said this lesson helped / hurt">
                  <span className="text-marine">helped {l.helpful}</span> · <span className={l.harmful ? "text-alarm" : ""}>hurt {l.harmful}</span>
                </span>
                {admin && <button className="text-sm text-graphite underline" onClick={() => set(l.id, "proposed")}>Pause</button>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {rejected.length > 0 && (
        <details className="mt-8">
          <summary className="cursor-pointer text-graphite">Rejected ({rejected.length})</summary>
          <ul className="mt-2 space-y-2 text-[15px] text-graphite">
            {rejected.map((l) => (
              <li key={l.id} className="flex gap-3"><span className="flex-1">{l.lesson}</span>
                {admin && <button className="text-sm underline" onClick={() => set(l.id, "proposed")}>Reconsider</button>}</li>
            ))}
          </ul>
        </details>
      )}

      <section className="mt-12">
        <h2 className="font-display text-xl font-semibold">Problem difficulty, as candidates actually experience it</h2>
        <p className="mt-1 max-w-[72ch] text-sm text-graphite">
          Every problem starts at a rating from its LeetCode label and moves Elo-style after each attempt, adjusted for
          how many hints were needed. The planner picks problems rated near each candidate's level.
        </p>
        {cal.length === 0 ? <p className="mt-3 text-graphite">No problems attempted yet.</p> : (
          <div className="mt-4 overflow-hidden rounded-2xl border border-line bg-surface">
            <table className="w-full text-left text-[15px]">
              <thead className="border-b border-line text-sm text-graphite">
                <tr><th className="py-2.5 pl-5 font-medium">Problem</th><th className="font-medium">Label</th><th className="font-medium">Rating now</th><th className="pr-5 font-medium">Attempts</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {cal.map((p) => {
                  const delta = Math.round(p.elo - (START_ELO[p.difficulty] ?? p.elo));
                  return (
                    <tr key={p.id}>
                      <td className="py-2.5 pl-5">{p.title}</td>
                      <td className="text-graphite">{p.difficulty}</td>
                      <td className="tabular-nums">
                        {Math.round(p.elo)}
                        {delta !== 0 && (
                          <span className={"ml-2 inline-flex items-center text-sm " + (delta > 0 ? "text-alarm" : "text-marine")}
                            title={delta > 0 ? "Harder than its label suggests" : "Easier than its label suggests"}>
                            {delta > 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}{Math.abs(delta)}
                          </span>
                        )}
                      </td>
                      <td className="pr-5 tabular-nums">{p.attempts}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

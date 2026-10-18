import { CopilotKit, CopilotSidebar, useAgentContext, useFrontendTool } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { MessageSquareText, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Avatar, PageHeader, RecPill, ScoreStrip, Segmented } from "../components/ui";
import { api, DIMENSIONS, fmtDate, RECOMMENDATION } from "../lib/api";
import { useAgentToken } from "../lib/auth";

export default function Dashboard() {
  const [ask, setAsk] = useState(false);
  const agentToken = useAgentToken();
  return (
    <CopilotKit runtimeUrl="/api/copilotkit" agentId="analyst" properties={{ agentToken }} enableInspector={false}>
      <DashboardInner onAsk={() => setAsk(true)} />
      <CopilotSidebar
        agentId="analyst"
        open={ask}
        onOpenChange={setAsk}
        labels={{
          modalHeaderTitle: "Ask about your interviews",
          welcomeMessageText: "Ask me to compare candidates, summarise concerns, or open an interview.",
          chatInputPlaceholder: "e.g. Who showed the most technical depth?",
        }}
      />
    </CopilotKit>
  );
}

type Row = {
  id: string; candidate: string; headline: string; role: string; seniority: string; status: string;
  recommendation: string | null; scores: Record<string, number>; avg_score: number | null; created_at: string;
};

const STATUS_FILTERS = [
  { value: "", label: "All" }, { value: "scored", label: "Scored" }, { value: "live", label: "In progress" },
  { value: "ready", label: "Not started" }, { value: "failed", label: "Failed" },
];

function DashboardInner({ onAsk }: { onAsk: () => void }) {
  const nav = useNavigate();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [stats, setStats] = useState<any>(null);
  const [filter, setFilter] = useState({ text: "", status: "", recommendation: "" });

  useEffect(() => {
    const load = () => {
      api("/api/interviews").then(setRows).catch(() => setRows([]));
      api("/api/interviews/stats").then(setStats).catch(() => {});
    };
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  const shown = useMemo(() => (rows ?? []).filter((r) =>
    (!filter.text || `${r.candidate} ${r.role} ${r.headline}`.toLowerCase().includes(filter.text.toLowerCase())) &&
    (!filter.status || r.status === filter.status || (filter.status === "scored" && r.status === "completed")) &&
    (!filter.recommendation || r.recommendation === filter.recommendation)), [rows, filter]);

  useAgentContext({
    description: "Interviews currently visible in the dashboard table (after filters)",
    value: shown.map((r) => ({ id: r.id, candidate: r.candidate, role: r.role, status: r.status,
      recommendation: r.recommendation, avg: r.avg_score })),
  });
  useAgentContext({ description: "Active dashboard filters", value: filter });
  useFrontendTool({
    name: "open_interview",
    description: "Open an interview's full scorecard and transcript page",
    parameters: z.object({ id: z.string() }),
    handler: async ({ id }) => {
      nav(`/dashboard/${id}`);
      return { opened: id };
    },
  });
  useFrontendTool({
    name: "filter_interviews",
    description: "Filter the dashboard table. Empty strings clear a filter.",
    parameters: z.object({
      text: z.string().optional(),
      status: z.enum(["", "ready", "live", "completed", "scored", "failed"]).optional(),
      recommendation: z.enum(["", "strong_hire", "hire", "no_hire", "strong_no_hire"]).optional(),
    }),
    handler: async (f) => {
      setFilter((cur) => ({ ...cur, ...f }));
      return { ok: true };
    },
  });

  const lat = stats?.latency?.voice_to_voice_ms;
  const scored = stats ? Object.values(stats.recommendations as Record<string, number>).reduce((a, b) => a + b, 0) : 0;
  const advance = stats ? (stats.recommendations.hire ?? 0) + (stats.recommendations.strong_hire ?? 0) : 0;

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 md:px-10">
      <PageHeader
        title="Interview history"
        sub={stats ? `${stats.total} interviews, ${scored} scored` +
          (stats.candidate_rating?.avg ? `. Candidates rate the experience ${stats.candidate_rating.avg} of 5.` : ".") : "Loading…"}
        actions={<>
          <button onClick={onAsk} className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2 text-sm font-medium hover:bg-paper">
            <MessageSquareText size={16} /> Ask about interviews
          </button>
          <Link to="/prep" className="flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2">
            <Plus size={16} /> Prepare for a job
          </Link>
        </>}
      />

      {stats && (
        <dl className="mt-8 grid grid-cols-2 divide-line overflow-hidden rounded-2xl border border-line bg-surface md:grid-cols-4 md:divide-x">
          <Stat label="Reply speed, median" value={lat?.p50 != null ? `${(lat.p50 / 1000).toFixed(1)}s` : "—"}
            note={lat?.p90 != null ? `slowest 10% over ${(lat.p90 / 1000).toFixed(1)}s` : "from candidate stopping to first word"} />
          <Stat label="Recommended to advance" value={scored ? `${advance} of ${scored}` : "—"} note="hire or strong hire" />
          <Stat label="Weakest area" value={weakest(stats.dimension_avgs)} note="lowest average across scored interviews" />
          <Stat label="In progress" value={String(stats.by_status?.live ?? 0)} note={`${stats.by_status?.ready ?? 0} prepared, not started`} />
        </dl>
      )}

      <div className="mt-8 flex flex-wrap items-center gap-3">
        <label className="relative">
          <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-mute" />
          <input aria-label="Search" placeholder="Search candidate or role" value={filter.text}
            onChange={(e) => setFilter({ ...filter, text: e.target.value })}
            className="w-64 rounded-lg border border-line bg-surface py-2 pr-3 pl-9" />
        </label>
        <Segmented label="Status" value={filter.status} options={STATUS_FILTERS}
          onChange={(status) => setFilter({ ...filter, status })} />
        <select aria-label="Recommendation" value={filter.recommendation}
          onChange={(e) => setFilter({ ...filter, recommendation: e.target.value })}
          className="rounded-lg border border-line bg-surface px-3 py-2">
          <option value="">Any recommendation</option>
          {Object.entries(RECOMMENDATION).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
      </div>

      <div className="mt-4 overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full min-w-[820px] text-left">
          <thead className="border-b border-line text-sm text-graphite">
            <tr>
              <th className="py-3 pr-4 pl-5 font-medium">Candidate</th>
              <th className="py-3 pr-4 font-medium">Role</th>
              <th className="py-3 pr-4 font-medium" title={Object.values(DIMENSIONS).join(", ")}>Scores</th>
              <th className="py-3 pr-4 font-medium">Outcome</th>
              <th className="py-3 pr-5 font-medium">When</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {shown.map((r) => (
              <tr key={r.id} className="cursor-pointer hover:bg-paper/70" tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && nav(["completed", "scored"].includes(r.status) ? `/dashboard/${r.id}` : `/interview/${r.id}`)}
                onClick={() => nav(["completed", "scored"].includes(r.status) ? `/dashboard/${r.id}` : `/interview/${r.id}`)}>
                <td className="py-3.5 pr-4 pl-5">
                  <div className="flex items-center gap-3">
                    <Avatar name={r.candidate || "?"} />
                    <div className="min-w-0">
                      <div className="font-semibold">{r.candidate || "Unnamed"}</div>
                      <div className="max-w-[260px] truncate text-sm text-mute">{r.headline}</div>
                    </div>
                  </div>
                </td>
                <td className="max-w-[220px] py-3.5 pr-4">
                  <div className="truncate">{r.role}</div>
                  <div className="text-sm text-mute capitalize">{r.seniority}</div>
                </td>
                <td className="py-3.5 pr-4">{r.recommendation ? <ScoreStrip scores={r.scores} size="sm" /> : <span className="text-mute">—</span>}</td>
                <td className="py-3.5 pr-4"><RecPill rec={r.recommendation} status={r.status} /></td>
                <td className="py-3.5 pr-5 text-sm whitespace-nowrap text-mute">{fmtDate(r.created_at)}</td>
              </tr>
            ))}
            {rows !== null && shown.length === 0 && (
              <tr><td colSpan={5} className="py-14 text-center text-graphite">
                {rows.length ? "No interviews match these filters." : <>No interviews yet. <Link className="font-semibold underline" to="/prep">Prepare for a job</Link> or <Link className="font-semibold underline" to="/new">start from a resume</Link>.</>}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function weakest(avgs: Record<string, number>) {
  const e = Object.entries(avgs ?? {});
  if (!e.length) return "—";
  const [k] = e.sort((a, b) => a[1] - b[1])[0];
  return DIMENSIONS[k] ?? k;
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="px-5 py-4">
      <dt className="text-sm text-graphite">{label}</dt>
      <dd className="mt-0.5 font-display text-[26px] leading-tight font-semibold">{value}</dd>
      <dd className="text-xs text-mute">{note}</dd>
    </div>
  );
}

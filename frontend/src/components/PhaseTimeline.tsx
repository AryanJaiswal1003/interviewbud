import { PHASES } from "../lib/api";
import type { SessionState } from "../lib/useInterview";

/** Phases sized by their time budget; the current one is lit. It's a real sequence, so order is shown. */
export function PhaseTimeline({ state }: { state: SessionState | null }) {
  const budgets = Object.fromEntries((state?.phase_budgets ?? []).map((b) => [b.phase, b.minutes]));
  const currentIdx = PHASES.findIndex((p) => p.key === state?.phase);
  const shown = PHASES.filter((p) => (budgets[p.key] ?? 1) > 0);
  return (
    <ol className="flex w-full items-stretch gap-1" aria-label="Interview progress">
      {shown.map((p) => {
        const idx = PHASES.findIndex((x) => x.key === p.key);
        const status = idx < currentIdx ? "done" : idx === currentIdx ? "now" : "later";
        return (
          <li
            key={p.key}
            style={{ flexGrow: budgets[p.key] ?? 1 }}
            className="min-w-0 basis-0"
            aria-current={status === "now" ? "step" : undefined}
          >
            <div
              className={
                "h-1.5 rounded-full " +
                (status === "done" ? "bg-amber/70" : status === "now" ? "bg-amber" : "bg-white/15")
              }
            />
            <div
              className={
                "mt-1.5 truncate text-xs " + (status === "now" ? "font-bold text-white" : "text-white/55")
              }
            >
              {p.label}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

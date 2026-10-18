import { DIMENSIONS, RECOMMENDATION } from "../lib/api";

const SCORE_BG = ["bg-line/60", "bg-s1", "bg-s2", "bg-s3", "bg-s4"];
const SCORE_WORD = ["no signal", "weak", "mixed", "solid", "strong"];

/** Six rubric dimensions as one strip of tinted cells: the at-a-glance read of an interview. */
export function ScoreStrip({ scores, size = "md" }: { scores: Record<string, number | undefined>; size?: "sm" | "md" | "lg" }) {
  const h = size === "lg" ? "h-7 w-9" : size === "sm" ? "h-3.5 w-4" : "h-5 w-6";
  return (
    <span className="inline-flex gap-[3px]" role="img"
      aria-label={Object.keys(DIMENSIONS).map((k) => `${DIMENSIONS[k]} ${scores[k] ?? 0} of 4`).join(", ")}>
      {Object.keys(DIMENSIONS).map((k) => {
        const s = scores[k] ?? 0;
        return <span key={k} title={`${DIMENSIONS[k]}: ${s ? `${s}/4, ${SCORE_WORD[s]}` : "no signal"}`}
          className={`${h} rounded-[3px] ${SCORE_BG[s]}`} />;
      })}
    </span>
  );
}

/** One dimension's 1–4 score as a four-step meter. */
export function ScoreMeter({ score }: { score?: number | null }) {
  const s = score ?? 0;
  return (
    <span className="inline-flex items-center gap-2" aria-label={s ? `${s} of 4` : "no signal"}>
      <span className="inline-flex gap-[3px]">
        {[1, 2, 3, 4].map((i) => <span key={i} className={"h-2.5 w-7 rounded-full " + (i <= s ? SCORE_BG[s] : "bg-line/70")} />)}
      </span>
      <span className="text-sm text-graphite tabular-nums">{s ? `${s}/4 ${SCORE_WORD[s]}` : "no signal"}</span>
    </span>
  );
}

export function RecPill({ rec, status, big }: { rec?: string | null; status?: string; big?: boolean }) {
  const pad = big ? "px-4 py-1.5 text-base" : "px-2.5 py-0.5 text-sm";
  if (!rec) {
    const label = status === "live" ? "In progress" : status === "completed" ? "Scoring…" : status === "ready" ? "Not started"
      : status === "failed" ? "Failed" : status ?? "";
    return <span className={`inline-flex items-center gap-1.5 rounded-full bg-paper-2 whitespace-nowrap text-graphite ${pad}`}>
      {status === "live" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber" />}{label}</span>;
  }
  const good = rec === "hire" || rec === "strong_hire";
  return (
    <span className={`inline-flex items-center rounded-full font-semibold whitespace-nowrap ${pad} ` +
      (good ? "bg-marine-soft text-marine-ink" : "bg-coral-soft text-alarm")}>
      {RECOMMENDATION[rec]?.label ?? rec}
    </span>
  );
}

export function Avatar({ name, tone = "marine", size = 36 }: { name: string; tone?: "marine" | "amber"; size?: number }) {
  return (
    <span className={"inline-flex shrink-0 items-center justify-center rounded-full font-display font-semibold text-ink " +
      (tone === "marine" ? "bg-marine-soft" : "bg-amber-soft")}
      style={{ width: size, height: size, fontSize: size * 0.42 }} aria-hidden>
      {(name || "?").trim().charAt(0).toUpperCase()}
    </span>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex flex-wrap gap-1 rounded-lg bg-paper-2 p-1">
      {options.map((o) => (
        <button type="button" role="radio" aria-checked={o.value === value} key={String(o.value)} onClick={() => onChange(o.value)}
          className={"rounded-md px-3 py-1.5 text-sm transition-colors " +
            (o.value === value ? "bg-surface font-semibold text-ink shadow-[0_1px_2px_rgba(39,39,39,.12)]" : "text-graphite hover:text-ink")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function StatusDot({ ok, title }: { ok: boolean | null; title?: string }) {
  return <span title={title} className={"inline-block h-2 w-2 shrink-0 rounded-full " +
    (ok === null ? "bg-white/30" : ok ? "bg-[#5aa9e6]" : "bg-coral")} />;
}

/** Candidate stopped talking → interviewer's first audio. Under 1.5 s feels conversational; over 3 s feels slow. */
export function LatencyChip({ ms }: { ms?: number | null }) {
  if (!ms) return null; // 0 = the pre-written opening line, not a reply
  const tone = ms < 1500 ? "bg-marine-soft text-marine-ink" : ms < 3000 ? "bg-amber-soft text-ink" : "bg-coral-soft text-alarm";
  return <span title="Time from the candidate stopping to the interviewer's first word"
    className={`rounded-full px-2 py-0.5 text-xs tabular-nums ${tone}`}>{(ms / 1000).toFixed(1)}s</span>;
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-display text-[34px] leading-tight font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-graphite">{sub}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

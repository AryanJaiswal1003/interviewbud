export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body instanceof FormData ? init?.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("ib:unauthorized"));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail
        // FastAPI validation errors: [{loc, msg}] -> "Email: value is not a valid email address"
        : Array.isArray(j.detail) ? j.detail.map((d: any) => {
          const field = String(d.loc?.[d.loc.length - 1] ?? "");
          const msg = String(d.msg ?? "").replace(/^Value error, /, "");
          return field && field !== "body" ? `${field[0].toUpperCase()}${field.slice(1).replace(/_/g, " ")}: ${msg}` : msg;
        }).join(". ")
        : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw Object.assign(new Error(detail), { status: res.status });
  }
  return res.json();
}

export const PHASES = [
  { key: "intro", label: "Hello" },
  { key: "background", label: "Background" },
  { key: "projects", label: "Projects" },
  { key: "technical", label: "Going deeper" },
  { key: "coding", label: "Coding" },
  { key: "candidate_questions", label: "Your questions" },
  { key: "wrap_up", label: "Wrap-up" },
] as const;

export const DIMENSIONS: Record<string, string> = {
  communication: "Communication",
  technical_depth: "Technical depth",
  ownership_impact: "Ownership & impact",
  problem_solving: "Problem solving",
  coding: "Coding",
  verification: "Verification",
};

export const RECOMMENDATION: Record<string, { label: string; tone: string }> = {
  strong_hire: { label: "Strong hire", tone: "text-ok" },
  hire: { label: "Hire", tone: "text-ok" },
  no_hire: { label: "No hire", tone: "text-alarm" },
  strong_no_hire: { label: "Strong no hire", tone: "text-alarm" },
};

export function fmtDate(s?: string | null) {
  if (!s) return "";
  return new Date(s).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** First-party analytics. Nothing is recorded until the visitor accepts analytics cookies. */

export type Consent = "all" | "essential" | null;
const CONSENT = "ib_consent";

export function getConsent(): Consent {
  const m = document.cookie.match(/(?:^|;\s*)ib_consent=(all|essential)/);
  return (m?.[1] as Consent) ?? null;
}

export function setConsent(c: "all" | "essential") {
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${CONSENT}=${c}; Max-Age=${365 * 86400}; Path=/; SameSite=Lax${secure}`;
  window.dispatchEvent(new Event("ib:consent"));
  if (c === "all") flush();
}

let queue: { name: string; path: string; props: Record<string, unknown> }[] = [];
let timer: number | undefined;

function flush() {
  window.clearTimeout(timer);
  if (getConsent() !== "all" || !queue.length) {
    queue = [];
    return;
  }
  const events = queue.splice(0, 50);
  fetch("/api/analytics/events", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ events }), keepalive: true,
  }).catch(() => {});
}

export function track(name: string, props: Record<string, string | number | boolean> = {}) {
  if (getConsent() !== "all") return;
  queue.push({ name, path: location.pathname, props });
  window.clearTimeout(timer);
  timer = window.setTimeout(flush, 1500);
}

addEventListener("pagehide", flush);

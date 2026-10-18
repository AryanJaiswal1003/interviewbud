import { X } from "lucide-react";
import { useState } from "react";
import { track } from "../lib/analytics";
import { useAuth, type SavedAccount } from "../lib/auth";

/** The whole way in: a name for a new account, or one of the accounts saved in this browser. */
export function AccountStart({ onDone, tone = "page" }: { onDone?: () => void; tone?: "page" | "hero" }) {
  const { accounts, start, choose, forget } = useAuth();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [fresh, setFresh] = useState(false);
  const showPicker = accounts.length > 0 && !fresh;

  const pick = async (a: SavedAccount) => {
    setBusy(a.id); setErr(null);
    try { await choose(a); track("account_chosen"); onDone?.(); }
    catch (e: any) { setErr(e.status === 404 ? `${a.name}'s account isn't available any more, so we've removed it from this browser.` : e.message); setBusy(null); }
  };

  if (showPicker) {
    return (
      <div>
        <p className="text-[15px] font-medium text-graphite">Choose your account</p>
        <ul className="mt-3 space-y-2">
          {accounts.map((a) => (
            <li key={a.id} className="group flex items-center gap-2">
              <button onClick={() => pick(a)} disabled={!!busy}
                className="flex min-w-0 flex-1 items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-left hover:border-ink/40 disabled:opacity-60">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber font-display text-lg font-semibold text-onaccent">{a.name.trim().charAt(0).toUpperCase()}</span>
                <span className="min-w-0">
                  <span className="block truncate font-semibold">{busy === a.id ? "Opening…" : a.name}</span>
                  <span className="block font-mono text-xs text-mute">ID {a.account_id}</span>
                </span>
              </button>
              <button aria-label={`Remove ${a.name} from this browser`} title="Remove from this browser" onClick={() => forget(a.id)}
                className="rounded-lg p-2 text-mute opacity-60 hover:bg-paper-2 hover:text-ink group-hover:opacity-100">
                <X size={16} />
              </button>
            </li>
          ))}
        </ul>
        <button onClick={() => setFresh(true)} className="mt-4 text-[15px] font-semibold underline underline-offset-4">I'm someone else</button>
        {err && <p className="mt-3 text-sm text-alarm" role="alert">{err}</p>}
      </div>
    );
  }

  return (
    <form onSubmit={async (e) => {
      e.preventDefault();
      if (!name.trim()) return;
      setBusy("new"); setErr(null);
      try { await start(name.trim()); track("account_started"); onDone?.(); }
      catch (er: any) { setErr(er.message); setBusy(null); }
    }}>
      <label htmlFor="start-name" className={"block font-medium " + (tone === "hero" ? "text-[17px]" : "text-[15px] text-graphite")}>What should we call you?</label>
      <div className="mt-2.5 flex flex-col gap-2 sm:flex-row">
        <input id="start-name" autoFocus={tone === "page"} required maxLength={60} value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Your first name is fine" autoComplete="given-name"
          className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-4 py-3 text-[17px] placeholder:text-mute focus:border-ink/50 focus:outline-none" />
        <button disabled={!!busy || !name.trim()} className="rounded-xl bg-primary px-6 py-3 text-[17px] font-semibold text-on-primary hover:bg-primary-2 disabled:opacity-50">
          {busy ? "Setting up…" : "Start"}
        </button>
      </div>
      <p className="mt-2.5 text-sm text-mute">No email, no password. This browser remembers you; come back and pick your name.</p>
      {accounts.length > 0 && <button type="button" onClick={() => setFresh(false)} className="mt-2 text-sm font-semibold underline underline-offset-4">Back to saved accounts</button>}
      {err && <p className="mt-2 text-sm text-alarm" role="alert">{err}</p>}
    </form>
  );
}

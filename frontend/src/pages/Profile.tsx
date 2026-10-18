import { Camera, Laptop, LogOut, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader, Segmented } from "../components/ui";
import { getConsent, setConsent, track } from "../lib/analytics";
import { api, fmtDate } from "../lib/api";
import { useAuth } from "../lib/auth";
import { setTheme, useTheme } from "../lib/theme";

const ACCENTS = [
  { value: "amber", cls: "bg-amber" }, { value: "marine", cls: "bg-marine" },
  { value: "coral", cls: "bg-coral" }, { value: "grey", cls: "bg-ink-3" },
];
const LEVELS = [
  { value: "auto", label: "From resume" }, { value: "junior", label: "Junior" }, { value: "mid", label: "Mid" },
  { value: "senior", label: "Senior" }, { value: "staff", label: "Staff" },
];

/** Square-crop and shrink a picked photo to 256 px before upload. */
async function squareAvatar(file: File): Promise<Blob> {
  const img = await createImageBitmap(file);
  const side = Math.min(img.width, img.height);
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  c.getContext("2d")!.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, 256, 256);
  return new Promise((ok, bad) => c.toBlob((b) => (b ? ok(b) : bad(new Error("Couldn't read that image"))), "image/jpeg", 0.88));
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="mt-6 grid gap-6 rounded-2xl border border-line bg-surface p-6 md:grid-cols-[220px_1fr]">
      <div>
        <h2 className="font-display text-lg font-semibold">{title}</h2>
        {sub && <p className="mt-1 text-sm text-graphite">{sub}</p>}
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

export default function Profile() {
  const { user, update, setUser, logout } = useAuth();
  const nav = useNavigate();
  const theme = useTheme();
  const [form, setForm] = useState({ display_name: "", headline: "", timezone: "" });
  const [prefs, setPrefs] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  const [consent, setConsentState] = useState(getConsent());
  const zones = useMemo(() => (Intl as any).supportedValuesOf?.("timeZone") as string[] ?? [], []);

  useEffect(() => {
    if (!user) return;
    setForm({ display_name: user.display_name, headline: user.headline, timezone: user.timezone });
    setPrefs({ interviewer_name: "Alex", default_role: "", default_seniority: "auto", default_duration: 45, default_voice: "", ...user.preferences });
  }, [user]);
  useEffect(() => {
    Promise.all([api("/api/voices").catch(() => []), api("/api/voices/fish").catch(() => [])]).then(([s, l]) =>
      setVoices([...s.map((v: any) => ({ id: v.fish_model_id, name: v.name })),
        ...l.filter((m: any) => !s.some((v: any) => v.fish_model_id === m.fish_model_id)).map((m: any) => ({ id: m.fish_model_id, name: m.title }))]));
  }, []);
  if (!user) return null;

  const save = async (patch: any, what: string) => {
    setErr(null);
    try {
      await update(patch);
      setSaved(what);
      setTimeout(() => setSaved(null), 2500);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 md:px-10">
      <PageHeader title="Your profile" sub={`Signed in as ${user.email} · member since ${fmtDate(user.created_at)}`} />
      {(saved || err) && (
        <p role="status" className={"mt-4 rounded-lg px-4 py-2 text-sm " + (err ? "bg-coral-soft text-alarm" : "bg-marine-soft text-marine-ink")}>
          {err ?? `${saved} saved.`}
        </p>
      )}

      <Section title="About you" sub="How you appear in InterviewBud.">
        <div className="flex flex-wrap items-center gap-5">
          {user.avatar_url ? <img src={user.avatar_url} alt="" className="h-20 w-20 rounded-full object-cover" />
            : <AccentAvatar name={user.display_name} accent={user.accent} />}
          <div className="space-y-2">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-paper">
              <Camera size={16} /> {user.avatar_url ? "Change photo" : "Upload a photo"}
              <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                try {
                  const fd = new FormData();
                  fd.append("file", await squareAvatar(f), "avatar.jpg");
                  setUser(await api("/api/auth/me/avatar", { method: "PUT", body: fd }));
                  setSaved("Photo");
                } catch (e: any) { setErr(e.message); }
              }} />
            </label>
            {user.avatar_url ? (
              <button className="block text-sm text-graphite underline" onClick={async () => setUser(await api("/api/auth/me/avatar", { method: "DELETE" }))}>
                Remove photo
              </button>
            ) : (
              <div className="flex items-center gap-2" role="radiogroup" aria-label="Avatar colour">
                {ACCENTS.map((a) => (
                  <button key={a.value} role="radio" aria-checked={user.accent === a.value} aria-label={a.value}
                    onClick={() => save({ accent: a.value }, "Colour")}
                    className={`h-7 w-7 rounded-full ${a.cls} ` + (user.accent === a.value ? "ring-2 ring-ink ring-offset-2 ring-offset-surface" : "")} />
                ))}
              </div>
            )}
          </div>
        </div>
        <form className="mt-6 grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save(form, "Profile"); }}>
          <label className="block"><span className="text-sm font-semibold">Name</span>
            <input className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2" value={form.display_name} maxLength={120}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></label>
          <label className="block"><span className="text-sm font-semibold">Headline</span>
            <input className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2" value={form.headline} maxLength={200}
              placeholder="e.g. Engineering manager, Payments" onChange={(e) => setForm({ ...form, headline: e.target.value })} /></label>
          <label className="block md:col-span-2"><span className="text-sm font-semibold">Timezone</span>
            <select className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2" value={form.timezone}
              onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
              <option value="">Use this device's ({Intl.DateTimeFormat().resolvedOptions().timeZone})</option>
              {zones.map((z) => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
            </select></label>
          <div><button className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2">Save profile</button></div>
        </form>
      </Section>

      <Section title="Interview defaults" sub="Pre-filled every time you set up an interview.">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save({ preferences: prefs }, "Defaults"); }}>
          <label className="block"><span className="text-sm font-semibold">Interviewer's name</span>
            <input className="mt-1.5 w-full max-w-xs rounded-lg border border-line bg-surface px-3 py-2" value={prefs.interviewer_name ?? ""} maxLength={40}
              onChange={(e) => setPrefs({ ...prefs, interviewer_name: e.target.value })} />
            <span className="mt-1 block text-sm text-mute">What the AI interviewer calls itself in your interviews.</span></label>
          <label className="block"><span className="text-sm font-semibold">Role</span>
            <input className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2" value={prefs.default_role ?? ""} maxLength={200}
              placeholder="Software Engineer" onChange={(e) => setPrefs({ ...prefs, default_role: e.target.value })} /></label>
          <div><div className="text-sm font-semibold">Level</div>
            <div className="mt-1.5"><Segmented label="Default level" value={prefs.default_seniority ?? "auto"} options={LEVELS}
              onChange={(v) => setPrefs({ ...prefs, default_seniority: v })} /></div></div>
          <div><div className="text-sm font-semibold">Length</div>
            <div className="mt-1.5"><Segmented label="Default length" value={prefs.default_duration ?? 45}
              options={[30, 45, 60].map((m) => ({ value: m, label: `${m} min` }))} onChange={(v) => setPrefs({ ...prefs, default_duration: v })} /></div></div>
          <label className="block"><span className="text-sm font-semibold">Voice</span>
            <select className="mt-1.5 w-full max-w-xs rounded-lg border border-line bg-surface px-3 py-2" value={prefs.default_voice ?? ""}
              onChange={(e) => setPrefs({ ...prefs, default_voice: e.target.value })}>
              <option value="">Default voice</option>
              {voices.map((v) => <option key={v.id} value={v.id}>{v.name.replace(/_/g, " ")}</option>)}
            </select></label>
          <button className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2">Save defaults</button>
        </form>
      </Section>

      <Section title="Appearance and privacy">
        <div className="space-y-5">
          <div><div className="text-sm font-semibold">Theme</div>
            <div className="mt-1.5"><Segmented label="Theme" value={theme} options={[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }]}
              onChange={(t) => { setTheme(t); save({ preferences: { theme: t } }, "Theme"); }} /></div></div>
          <div><div className="text-sm font-semibold">Analytics cookies</div>
            <p className="mt-1 text-sm text-graphite">Lets us see which features get used. First-party only; sign-in cookies are always on.</p>
            <div className="mt-2"><Segmented label="Analytics cookies" value={consent ?? "essential"}
              options={[{ value: "all", label: "Allowed" }, { value: "essential", label: "Off" }]}
              onChange={(c) => { setConsent(c); setConsentState(c); save({ preferences: { analytics: c === "all" } }, "Cookie choice"); }} /></div></div>
        </div>
      </Section>

      <Security onSignedOut={() => nav("/login")} />

      <Section title="Delete account" sub="Removes your account, your interviews and their transcripts. This can't be undone.">
        <DeleteAccount onDeleted={async () => { await logout(); nav("/signup"); }} />
      </Section>

      <div className="mt-6">
        <button className="inline-flex items-center gap-2 text-sm text-graphite underline" onClick={async () => { await logout(); nav("/login"); }}>
          <LogOut size={15} /> Log out of this device
        </button>
      </div>
    </div>
  );
}

function AccentAvatar({ name, accent }: { name: string; accent: string }) {
  const bg = { amber: "bg-amber text-onaccent", marine: "bg-marine text-white", coral: "bg-coral text-white", grey: "bg-ink-3 text-white" }[accent] ?? "bg-amber text-onaccent";
  return <span className={`flex h-20 w-20 items-center justify-center rounded-full font-display text-3xl font-semibold ${bg}`}>{(name || "?").charAt(0).toUpperCase()}</span>;
}

function Security({ onSignedOut }: { onSignedOut: () => void }) {
  const [sessions, setSessions] = useState<any[]>([]);
  const [pw, setPw] = useState({ current: "", new: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api("/api/auth/sessions").then(setSessions).catch(() => {});
  useEffect(() => { load(); }, []);
  return (
    <Section title="Security" sub="Your password and the devices signed in to your account.">
      <form className="grid max-w-md gap-3" onSubmit={async (e) => {
        e.preventDefault();
        setMsg(null);
        try {
          await api("/api/auth/me/password", { method: "POST", body: JSON.stringify(pw) });
          setPw({ current: "", new: "" });
          setMsg("Password changed. Other devices were signed out.");
          track("password_changed");
          load();
        } catch (e: any) { setMsg(e.message); }
      }}>
        <input type="password" required placeholder="Current password" autoComplete="current-password" value={pw.current}
          onChange={(e) => setPw({ ...pw, current: e.target.value })} className="rounded-lg border border-line bg-surface px-3 py-2" />
        <input type="password" required minLength={8} placeholder="New password (8+ characters)" autoComplete="new-password" value={pw.new}
          onChange={(e) => setPw({ ...pw, new: e.target.value })} className="rounded-lg border border-line bg-surface px-3 py-2" />
        <div><button className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2">Change password</button></div>
        {msg && <p className="text-sm text-graphite" role="status">{msg}</p>}
      </form>

      <h3 className="mt-8 font-semibold">Signed-in devices</h3>
      <ul className="mt-2 divide-y divide-line rounded-xl border border-line">
        {sessions.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <Laptop size={18} className="shrink-0 text-mute" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-medium">{describeAgent(s.user_agent)}{s.current && <span className="ml-2 rounded-full bg-marine-soft px-2 py-0.5 text-xs text-marine-ink">this device</span>}</div>
              <div className="text-sm text-mute">Last active {fmtDate(s.last_seen_at)} · {s.remember ? "remembered for 30 days" : "until the browser closes"}</div>
            </div>
            <button className="text-sm text-graphite underline" onClick={async () => {
              await api(`/api/auth/sessions/${s.id}`, { method: "DELETE" });
              if (s.current) onSignedOut(); else load();
            }}>{s.current ? "Sign out" : "Sign out device"}</button>
          </li>
        ))}
      </ul>
      {sessions.length > 1 && (
        <button className="mt-3 text-sm font-medium underline" onClick={async () => { await api("/api/auth/sessions/revoke-others", { method: "POST" }); load(); }}>
          Sign out all other devices
        </button>
      )}
    </Section>
  );
}

function describeAgent(ua: string): string {
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

function DeleteAccount({ onDeleted }: { onDeleted: () => void }) {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  if (!open) return <button className="inline-flex items-center gap-2 rounded-lg border border-coral/60 px-3 py-2 text-sm font-medium text-alarm hover:bg-coral-soft" onClick={() => setOpen(true)}><Trash2 size={15} /> Delete my account</button>;
  return (
    <form className="grid max-w-md gap-3" onSubmit={async (e) => {
      e.preventDefault();
      try {
        await api("/api/auth/me/delete", { method: "POST", body: JSON.stringify({ password }) });
        onDeleted();
      } catch (e: any) { setErr(e.message); }
    }}>
      <input type="password" required placeholder="Your password, to confirm" autoComplete="current-password" value={password}
        onChange={(e) => setPassword(e.target.value)} className="rounded-lg border border-line bg-surface px-3 py-2" />
      <div className="flex gap-2">
        <button className="rounded-lg bg-coral px-4 py-2 text-sm font-semibold text-white">Delete everything</button>
        <button type="button" className="px-3 py-2 text-sm text-graphite" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {err && <p className="text-sm text-alarm" role="alert">{err}</p>}
    </form>
  );
}


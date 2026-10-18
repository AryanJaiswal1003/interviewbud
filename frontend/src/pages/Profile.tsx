import { Camera, Laptop, LogOut, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader, Segmented } from "../components/ui";
import { getConsent, setConsent } from "../lib/analytics";
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
  const { user, update, setUser, logout, forget } = useAuth();
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
      <PageHeader title="Your profile" sub={`Account ID ${user.account_id} · since ${fmtDate(user.created_at)}`} />
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
            <p className="mt-1 text-sm text-graphite">Lets us see which features get used. First-party only; the cookie that remembers your account is always on.</p>
            <div className="mt-2"><Segmented label="Analytics cookies" value={consent ?? "essential"}
              options={[{ value: "all", label: "Allowed" }, { value: "essential", label: "Off" }]}
              onChange={(c) => { setConsent(c); setConsentState(c); save({ preferences: { analytics: c === "all" } }, "Cookie choice"); }} /></div></div>
        </div>
      </Section>

      <ApplicationDetails />

      <Devices />

      <Section title="Delete account" sub="Removes your account, your interviews, preparations and applications. This can't be undone.">
        <DeleteAccount onDeleted={async () => { forget(user.id); await logout(); nav("/welcome"); }} />
      </Section>

      <div className="mt-6 flex flex-wrap gap-5">
        <button className="inline-flex items-center gap-2 text-sm text-graphite underline" onClick={async () => { await logout(); nav("/welcome"); }}>
          <LogOut size={15} /> Switch account
        </button>
        <button className="text-sm text-graphite underline" onClick={async () => { forget(user.id); await logout(); nav("/welcome"); }}>
          Remove this account from this browser
        </button>
      </div>
    </div>
  );
}

function AccentAvatar({ name, accent }: { name: string; accent: string }) {
  const bg = { amber: "bg-amber text-onaccent", marine: "bg-marine text-white", coral: "bg-coral text-white", grey: "bg-ink-3 text-white" }[accent] ?? "bg-amber text-onaccent";
  return <span className={`flex h-20 w-20 items-center justify-center rounded-full font-display text-3xl font-semibold ${bg}`}>{(name || "?").charAt(0).toUpperCase()}</span>;
}

function Devices() {
  const [sessions, setSessions] = useState<any[]>([]);
  const load = () => api("/api/auth/sessions").then(setSessions).catch(() => {});
  useEffect(() => { load(); }, []);
  if (sessions.length < 2) return null;
  return (
    <Section title="Where it's open" sub="Browsers using this account right now.">
      <ul className="divide-y divide-line rounded-xl border border-line">
        {sessions.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <Laptop size={18} className="shrink-0 text-mute" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-medium">{describeAgent(s.user_agent)}{s.current && <span className="ml-2 rounded-full bg-marine-soft px-2 py-0.5 text-xs text-marine-ink">this browser</span>}</div>
              <div className="text-sm text-mute">Last active {fmtDate(s.last_seen_at)}</div>
            </div>
          </li>
        ))}
      </ul>
      <button className="mt-3 text-sm font-medium underline" onClick={async () => { await api("/api/auth/sessions/revoke-others", { method: "POST" }); load(); }}>
        Close it everywhere else
      </button>
    </Section>
  );
}

const DETAIL_FIELDS: { key: string; label: string; placeholder?: string; type?: string }[] = [
  { key: "first_name", label: "First name" }, { key: "last_name", label: "Last name" },
  { key: "email", label: "Email", type: "email" }, { key: "phone", label: "Phone", type: "tel" },
  { key: "country", label: "Country", placeholder: "United States" }, { key: "location", label: "City", placeholder: "San Francisco" },
  { key: "linkedin", label: "LinkedIn", placeholder: "linkedin.com/in/…" }, { key: "github", label: "GitHub", placeholder: "github.com/…" },
  { key: "website", label: "Website or portfolio" },
  { key: "work_authorization", label: "Work authorization", placeholder: "e.g. US citizen; authorized to work in India" },
  { key: "needs_sponsorship", label: "Need visa sponsorship?", placeholder: "Yes or No" },
  { key: "relocation", label: "Open to relocating?", placeholder: "e.g. Yes, within the US" },
  { key: "notice_period", label: "Notice period or start date", placeholder: "e.g. 4 weeks" },
  { key: "how_heard", label: "How you usually hear about jobs", placeholder: "e.g. Company website" },
];

/** What application forms ask for every time; used only when you apply, and you see every value first. */
function ApplicationDetails() {
  const [d, setD] = useState<any>(null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { api("/api/profile/application").then((x) => { setD(x); setVals(x.details); }).catch(() => {}); }, []);
  if (!d) return null;
  return (
    <section id="application" className="mt-6 scroll-mt-6 rounded-2xl border border-line bg-surface p-6">
      <h2 className="font-display text-lg font-semibold">Application details</h2>
      <p className="mt-0.5 text-sm text-graphite">What job applications ask for every time. Only used when you apply, and you see every value before anything is sent.</p>
      <form className="mt-5 grid gap-4 md:grid-cols-2" onSubmit={async (e) => {
        e.preventDefault(); setMsg(null);
        try { const x = await api("/api/profile/application", { method: "PUT", body: JSON.stringify({ details: vals }) }); setD(x); setVals(x.details); setMsg("Saved."); }
        catch (er: any) { setMsg(er.message); }
      }}>
        {DETAIL_FIELDS.map((f) => (
          <label key={f.key} className="block"><span className="text-sm font-semibold">{f.label}</span>
            <input type={f.type ?? "text"} value={vals[f.key] ?? ""} placeholder={f.placeholder} maxLength={300}
              onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
        ))}
        <div className="flex items-center gap-3 md:col-span-2">
          <button className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary-2">Save details</button>
          {msg && <span className="text-sm text-graphite" role="status">{msg}</span>}
        </div>
      </form>
      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-line pt-5">
        <span className="text-sm font-semibold">Resume file</span>
        <span className="text-sm text-graphite">{d.resume ? `${d.resume.name} · uploaded ${fmtDate(d.resume.uploaded_at)}` : "None yet: forms that ask for a resume need one"}</span>
        <label className="ml-auto inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-paper">
          {d.resume ? "Replace" : "Upload"} resume
          <input type="file" accept=".pdf,.doc,.docx,.txt,.rtf" className="sr-only" onChange={async (e) => {
            const file = e.target.files?.[0]; if (!file) return;
            const fd = new FormData(); fd.append("file", file);
            try { const r = await api("/api/profile/application/resume", { method: "POST", body: fd }); setD({ ...d, resume: r }); }
            catch (er: any) { setMsg(er.message); }
          }} />
        </label>
      </div>
    </section>
  );
}

function describeAgent(ua: string): string {
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

function DeleteAccount({ onDeleted }: { onDeleted: () => void }) {
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  if (!open) return <button className="inline-flex items-center gap-2 rounded-lg border border-coral/60 px-3 py-2 text-sm font-medium text-alarm hover:bg-coral-soft" onClick={() => setOpen(true)}><Trash2 size={15} /> Delete my account</button>;
  return (
    <form className="grid max-w-md gap-3" onSubmit={async (e) => {
      e.preventDefault();
      try {
        await api("/api/auth/me/delete", { method: "POST", body: JSON.stringify({ confirm: typed.trim().toLowerCase() }) });
        onDeleted();
      } catch (e: any) { setErr(e.message); }
    }}>
      <label className="text-sm text-graphite">Type <span className="font-mono font-semibold text-ink">delete</span> to confirm
        <input required value={typed} onChange={(e) => setTyped(e.target.value)} className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2" /></label>
      <div className="flex gap-2">
        <button disabled={typed.trim().toLowerCase() !== "delete"} className="rounded-lg bg-coral px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">Delete everything</button>
        <button type="button" className="px-3 py-2 text-sm text-graphite" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {err && <p className="text-sm text-alarm" role="alert">{err}</p>}
    </form>
  );
}

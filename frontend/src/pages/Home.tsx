import { FileText, Play, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageHeader, RecPill, Segmented } from "../components/ui";
import { api, fmtDate, PHASES } from "../lib/api";
import { track } from "../lib/analytics";
import { playPreview } from "../lib/audio";
import { useAuth } from "../lib/auth";

const LEVELS = [
  { value: "auto", label: "From resume" }, { value: "intern", label: "Intern" }, { value: "junior", label: "Junior" },
  { value: "mid", label: "Mid" }, { value: "senior", label: "Senior" }, { value: "staff", label: "Staff" },
];

export default function Home() {
  const nav = useNavigate();
  const prefs = useAuth().user?.preferences ?? {};
  const [file, setFile] = useState<File | null>(null);
  const [role, setRole] = useState(prefs.default_role || "Software Engineer");
  const [seniority, setSeniority] = useState(prefs.default_seniority || "auto");
  const [duration, setDuration] = useState(prefs.default_duration || 45);
  const [focus, setFocus] = useState("");
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  const [voice, setVoice] = useState("");
  const [health, setHealth] = useState<any>(null);
  const [recent, setRecent] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [playing, setPlaying] = useState(false);
  const ctx = useRef<AudioContext | null>(null);

  useEffect(() => {
    api("/api/health").then(setHealth).catch(() => setHealth({ db: false }));
    Promise.all([
      api("/api/voices").catch(() => []),
      api("/api/voices/fish").catch(() => []),
    ]).then(([saved, lib]) => {
      const list = [...saved.map((v: any) => ({ id: v.fish_model_id, name: v.name })),
        ...lib.filter((m: any) => !saved.some((v: any) => v.fish_model_id === m.fish_model_id))
          .map((m: any) => ({ id: m.fish_model_id, name: m.title }))];
      setVoices(list);
      const d = saved.find((x: any) => x.is_default);
      if (prefs.default_voice) setVoice(prefs.default_voice);
      else if (d) setVoice(d.fish_model_id);
    });
    api("/api/interviews").then((r) => setRecent(r.slice(0, 4))).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setErr(null);
    const fd = new FormData();
    fd.append("resume_file", file);
    fd.append("role", role);
    fd.append("seniority", seniority);
    fd.append("duration_min", String(duration));
    fd.append("focus", focus);
    fd.append("voice_id", voice);
    try {
      const r = await api("/api/interviews", { method: "POST", body: fd });
      track("interview_created", { duration, seniority });
      nav(`/interview/${r.id}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };

  const missing = health && [!health.azure_openai && "the interviewer's language model",
    health.tts_ready !== true && "the voice server"].filter(Boolean);
  const budgets: Record<string, number> = health?.phase_budgets?.[duration] ?? {};
  const phases = PHASES.filter((p) => (budgets[p.key] ?? 0) > 0);
  const total = phases.reduce((a, p) => a + budgets[p.key], 0) || 1;

  return (
    <div className="mx-auto max-w-6xl px-6 py-10 md:px-10">
      <PageHeader title="New interview"
        sub={`Upload a resume. ${prefs.interviewer_name || "Alex"} reads it, plans a screening round around the candidate's own projects, and runs it live.`} />

      {missing && missing.length > 0 && (
        <p className="mt-5 rounded-lg bg-amber-soft px-4 py-2.5 text-sm">
          Can't reach {missing.join(" or ")}. <Link to="/settings" className="font-semibold underline">Check settings</Link>
        </p>
      )}

      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_360px]">
        <form onSubmit={submit} className="rounded-2xl border border-line bg-surface p-6 md:p-8">
          <label
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }}
            className={"flex cursor-pointer items-center gap-4 rounded-xl border-2 border-dashed px-5 py-6 transition-colors " +
              (drag ? "border-marine bg-marine-soft/40" : file ? "border-marine/60 bg-marine-soft/25" : "border-line hover:border-ink/30")}
          >
            <input type="file" className="sr-only" accept=".pdf,.docx,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <span className={"flex h-12 w-12 shrink-0 items-center justify-center rounded-xl " + (file ? "bg-marine text-white" : "bg-paper-2 text-graphite")}>
              {file ? <FileText size={22} /> : <UploadCloud size={22} />}
            </span>
            {file ? (
              <span>
                <span className="block font-semibold">{file.name}</span>
                <span className="text-sm text-mute">{(file.size / 1024).toFixed(0)} KB · click to choose a different file</span>
              </span>
            ) : (
              <span>
                <span className="block font-semibold">Drop the resume here, or browse</span>
                <span className="text-sm text-mute">PDF, Word or plain text</span>
              </span>
            )}
          </label>

          <div className="mt-6 space-y-5">
            <label className="block">
              <span className="text-sm font-semibold">Role</span>
              <input className="mt-1.5 w-full rounded-lg border border-line px-3 py-2.5" value={role}
                onChange={(e) => setRole(e.target.value)} required />
            </label>
            <div>
              <div className="text-sm font-semibold">Level</div>
              <div className="mt-1.5"><Segmented label="Level" value={seniority} options={LEVELS} onChange={setSeniority} /></div>
            </div>
            <div>
              <div className="text-sm font-semibold">Length</div>
              <div className="mt-1.5">
                <Segmented label="Length" value={duration} onChange={setDuration}
                  options={[30, 45, 60].map((m) => ({ value: m, label: `${m} min` }))} />
              </div>
            </div>
            <div>
              <div className="text-sm font-semibold">Interviewer voice</div>
              <div className="mt-1.5 flex gap-2">
                <select aria-label="Interviewer voice" className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2.5"
                  value={voice} onChange={(e) => setVoice(e.target.value)}>
                  <option value="">Default voice</option>
                  {voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                </select>
                <button type="button" aria-label="Play a sample of this voice" disabled={playing}
                  className="flex items-center gap-1.5 rounded-lg border border-line px-3 text-sm hover:bg-paper disabled:opacity-50"
                  onClick={async () => {
                    setPlaying(true);
                    try {
                      ctx.current ??= new AudioContext();
                      await playPreview(ctx.current, voice, "Hey, thanks for jumping on. Okay so, what are you working on these days?");
                    } catch (e: any) {
                      setErr(`Voice preview failed: ${e.message}`);
                    } finally {
                      setPlaying(false);
                    }
                  }}>
                  <Play size={15} /> {playing ? "Playing" : "Hear it"}
                </button>
              </div>
            </div>
            <label className="block">
              <span className="text-sm font-semibold">Job description or focus</span>
              <span className="ml-1.5 text-sm text-mute">optional</span>
              <textarea className="mt-1.5 w-full rounded-lg border border-line px-3 py-2.5" rows={4}
                placeholder="Paste the job description, or note what to probe: distributed systems, API design, on-call…"
                value={focus} onChange={(e) => setFocus(e.target.value)} />
            </label>
          </div>
          {err && <p className="mt-4 text-sm text-alarm" role="alert">{err}</p>}
          <button className="mt-6 w-full rounded-xl bg-primary px-4 py-3.5 font-display text-lg font-semibold text-on-primary hover:bg-primary-2 disabled:opacity-40"
            disabled={!file || busy}>
            {busy ? "Uploading…" : "Prepare interview"}
          </button>
          <p className="mt-2 text-center text-sm text-mute">Takes a minute or two: the resume is read and the round planned before you join.</p>
        </form>

        <aside className="space-y-8">
          <section>
            <h2 className="font-display text-lg font-semibold">How the {duration} minutes go</h2>
            <ol className="mt-3 space-y-2.5">
              {phases.map((p) => (
                <li key={p.key}>
                  <div className="flex justify-between text-sm">
                    <span className="font-medium">{p.label}</span>
                    <span className="text-mute tabular-nums">{budgets[p.key]} min</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-paper-2">
                    <div className={"h-full rounded-full " + (p.key === "coding" ? "bg-marine" : "bg-amber")}
                      style={{ width: `${(budgets[p.key] / total) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ol>
            <p className="mt-3 text-sm text-graphite">
              Questions come from the resume itself; the coding problem is picked to match the level and gets
              hidden and stress tests in a sandbox.
            </p>
          </section>

          {recent.length > 0 && (
            <section>
              <div className="flex items-baseline justify-between">
                <h2 className="font-display text-lg font-semibold">Recent</h2>
                <Link className="text-sm text-graphite underline" to="/dashboard">All interviews</Link>
              </div>
              <ul className="mt-2 divide-y divide-line rounded-xl border border-line bg-surface">
                {recent.map((r) => (
                  <li key={r.id}>
                    <Link to={["completed", "scored"].includes(r.status) ? `/dashboard/${r.id}` : `/interview/${r.id}`}
                      className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-paper/60">
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{r.candidate || "Unnamed"}</span>
                        <span className="block truncate text-sm text-mute">{r.role} · {fmtDate(r.created_at)}</span>
                      </span>
                      <RecPill rec={r.recommendation} status={r.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}

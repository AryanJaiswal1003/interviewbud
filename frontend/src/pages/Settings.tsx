import { Mic, Play, Star } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PageHeader, StatusDot } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { playPreview } from "../lib/audio";

const SAMPLE = "Hey, thanks for jumping on. Okay so, what are you working on these days?";
const PROVIDER: Record<string, string> = {
  pocket: "Pocket TTS, self-hosted on this machine",
  chatterbox: "Chatterbox, self-hosted",
  fish_local: "Fish Speech, self-hosted",
  fish: "Fish Audio cloud",
};

export default function Settings() {
  const admin = useAuth().user?.is_admin ?? false;
  const [health, setHealth] = useState<any>(null);
  const [voices, setVoices] = useState<any[]>([]);
  const [library, setLibrary] = useState<any[] | null>(null);
  const [libErr, setLibErr] = useState<string | null>(null);
  const [cloneName, setCloneName] = useState("");
  const [cloneText, setCloneText] = useState("");
  const [cloneFile, setCloneFile] = useState<File | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const ctx = useRef<AudioContext | null>(null);

  const load = () => {
    api("/api/health").then(setHealth).catch(() => {});
    api("/api/voices").then(setVoices).catch(() => {});
  };
  useEffect(() => {
    load();
    api("/api/voices/fish").then(setLibrary).catch((e) => setLibErr(e.message));
  }, []);

  const play = async (id: string) => {
    setPlaying(id);
    try {
      ctx.current ??= new AudioContext();
      await playPreview(ctx.current, id, SAMPLE);
    } catch (e: any) {
      setMsg(`Couldn't play that voice: ${e.message}`);
    } finally {
      setPlaying(null);
    }
  };
  const save = async (id: string, name: string, description = "") => {
    await api("/api/voices", { method: "POST", body: JSON.stringify({ name, fish_model_id: id, description }) });
    load();
  };

  const h = health;
  const rows: [string, boolean, React.ReactNode][] = h ? [
    ["Interviewer brain", !!h.azure_openai, h.azure_openai
      ? <>Azure · live voice <b>{h.chat_deployment}</b>, analysis <b>{h.reasoning_deployment}</b></>
      : "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env"],
    ["Voice", h.tts_ready === true, <>{PROVIDER[h.tts_provider] ?? h.tts_provider}{h.tts_ready !== true && `: ${String(h.tts_ready)}`}</>],
    ["Speech-to-text", h.stt_provider === "local" || (h.stt_provider === "fish" ? h.fish_audio : h.azure_openai),
      { local: "Whisper on this machine (Apple GPU when available)", azure: "Azure OpenAI", fish: "Fish Audio cloud" }[h.stt_provider as string] ?? h.stt_provider],
    ["Code sandbox", h.sandbox === true, h.sandbox === true ? "Docker: no network, read-only, non-root" : String(h.sandbox)],
    ["Problem bank", !!h.problems, `${(h.problems ?? 0).toLocaleString()} problems with reference solutions (LeetCodeDataset, Apache-2.0)`],
  ] : [];

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 md:px-10">
      <PageHeader title="Settings" sub="What the interviewer runs on, and how it sounds." />

      <section className="mt-8">
        <h2 className="font-display text-xl font-semibold">Services</h2>
        <ul className="mt-3 divide-y divide-line rounded-2xl border border-line bg-surface">
          {h ? rows.map(([name, ok, detail]) => (
            <li key={name} className="flex items-center gap-4 px-5 py-3.5">
              <StatusDot ok={ok} />
              <span className="w-40 shrink-0 font-medium">{name}</span>
              <span className={"min-w-0 text-[15px] " + (ok ? "text-graphite" : "text-alarm")}>{detail}</span>
            </li>
          )) : <li className="px-5 py-4 text-graphite">Checking…</li>}
        </ul>
      </section>

      <section className="mt-10">
        <h2 className="font-display text-xl font-semibold">Interviewer voice</h2>
        <p className="mt-1 text-sm text-graphite">Saved voices appear in the voice picker when you set up an interview. The default is used when none is picked.</p>
        <ul className="mt-3 divide-y divide-line rounded-2xl border border-line bg-surface">
          {voices.map((v) => (
            <li key={v.id} className="flex items-center gap-3 px-5 py-3">
              <button aria-label={`Play ${v.name}`} disabled={!!playing} onClick={() => play(v.fish_model_id)}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-soft hover:brightness-95 disabled:opacity-50">
                <Play size={15} className={playing === v.fish_model_id ? "animate-pulse" : ""} />
              </button>
              <div className="min-w-0 flex-1">
                <div className="font-medium">{v.name} {v.is_default && <span className="ml-1 rounded-full bg-marine-soft px-2 py-0.5 text-xs text-marine-ink">default</span>}</div>
                <div className="truncate text-sm text-mute">{v.description}</div>
              </div>
              {admin && !v.is_default && <button className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-paper"
                onClick={async () => { await api(`/api/voices/${v.id}/default`, { method: "POST" }); load(); }}>
                <Star size={14} /> Make default</button>}
            </li>
          ))}
          {voices.length === 0 && <li className="px-5 py-4 text-graphite">No saved voices yet. Pick one from the library below or clone one.</li>}
        </ul>

        <h3 className="mt-8 font-semibold">Library</h3>
        {libErr && <p className="mt-2 text-sm text-alarm">{libErr}</p>}
        {library && (
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {library.map((m) => {
              const saved = voices.some((v) => v.fish_model_id === m.fish_model_id);
              return (
                <div key={m.fish_model_id} className="flex items-center gap-2 rounded-xl border border-line bg-surface px-3 py-2">
                  <button aria-label={`Play ${m.title}`} disabled={!!playing} onClick={() => play(m.fish_model_id)}
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-paper-2 hover:bg-amber-soft disabled:opacity-50">
                    <Play size={14} className={playing === m.fish_model_id ? "animate-pulse" : ""} />
                  </button>
                  <span className="min-w-0 flex-1 truncate text-sm capitalize">{m.title.replace(/_/g, " ")}</span>
                  {saved ? <span className="text-xs text-mute">saved</span> : !admin ? null : (
                    <button className="text-sm font-medium text-marine hover:underline" onClick={() => save(m.fish_model_id, m.title.replace(/_/g, " "), m.description ?? "")}>Save</button>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {admin ? <form className="mt-8 rounded-2xl border border-line bg-surface p-5" onSubmit={async (e) => {
          e.preventDefault();
          if (!cloneFile) return;
          setBusy(true); setMsg(null);
          const fd = new FormData();
          fd.append("name", cloneName);
          fd.append("transcript", cloneText);
          fd.append("sample", cloneFile);
          try {
            const r = await api("/api/voices/clone", { method: "POST", body: fd });
            setMsg(`Voice saved (${r.state}).`);
            load();
          } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
        }}>
          <h3 className="flex items-center gap-2 font-semibold"><Mic size={16} /> Clone a voice</h3>
          <p className="mt-1 text-sm text-graphite">10 to 30 seconds of clean speech, from someone who has agreed to have their voice cloned.</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <input className="rounded-lg border border-line px-3 py-2" placeholder="Voice name" value={cloneName}
              onChange={(e) => setCloneName(e.target.value)} required />
            <input type="file" accept="audio/*" className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-paper-2 file:px-3 file:py-2"
              onChange={(e) => setCloneFile(e.target.files?.[0] ?? null)} required />
            <textarea className="rounded-lg border border-line px-3 py-2 md:col-span-2" rows={2}
              placeholder="Exact words spoken in the sample (optional; transcribed automatically otherwise)"
              value={cloneText} onChange={(e) => setCloneText(e.target.value)} />
          </div>
          <button className="mt-3 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary disabled:opacity-40" disabled={busy || !cloneFile}>
            {busy ? "Cloning…" : "Clone voice"}
          </button>
        </form> : <p className="mt-8 text-sm text-graphite">Only an admin can add or clone voices, since every user shares them.</p>}
        {msg && <p className="mt-4 text-sm" role="status">{msg}</p>}
      </section>
    </div>
  );
}

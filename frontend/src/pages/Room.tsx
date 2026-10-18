import { CopilotChat, CopilotKit, useAgentContext, useFrontendTool } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Captions, Check, CircleDashed, Keyboard, MessageSquareText, Mic, MicOff, PhoneOff, Quote, Send, Video, VideoOff, Volume2 } from "lucide-react";
import { FeedbackBody, SimilarJobs, type Review } from "../components/AfterInterview";
import { z } from "zod";
import { CodePanel } from "../components/CodePanel";
import { PhaseTimeline } from "../components/PhaseTimeline";
import { Whiteboard, type WhiteboardHandle } from "../components/Whiteboard";
import { api } from "../lib/api";
import { playPreview } from "../lib/audio";
import { track } from "../lib/analytics";
import { useAgentToken, useAuth } from "../lib/auth";
import { useInterview, type CaptureItem } from "../lib/useInterview";

type Panel = "code" | "whiteboard" | "notes";

export default function Room() {
  const { id = "" } = useParams();
  const agentToken = useAgentToken();
  const [info, setInfo] = useState<any>(null);

  useEffect(() => {
    let stop = false;
    const poll = async () => {
      try {
        const d = await api(`/api/interviews/${id}`);
        if (stop) return;
        setInfo(d);
        if (["created", "planning"].includes(d.status)) setTimeout(poll, 1500);
      } catch (e) {
        if (!stop) setInfo({ status: "missing", error: String(e) });
      }
    };
    poll();
    return () => {
      stop = true;
    };
  }, [id]);

  if (!info) return <Centered>Loading…</Centered>;
  if (info.status === "missing") return <Centered>That interview doesn't exist. <Link className="underline" to="/">Start a new one</Link>.</Centered>;
  if (["created", "planning"].includes(info.status)) return <Preparing info={info} />;
  if (info.status === "failed")
    return (
      <Centered>
        <h1 className="font-display text-2xl font-semibold">Preparation failed</h1>
        <p className="mt-2 max-w-lg text-white/70">{info.error}</p>
        <button className="mt-5 rounded-full bg-amber px-5 py-2.5 font-semibold text-onaccent" onClick={async () => {
          await api(`/api/interviews/${id}/retry`, { method: "POST" });
          location.reload();
        }}>Try again</button>
      </Centered>
    );
  if (["completed", "scored"].includes(info.status) && !info.live) return <Finished id={id} prepId={info.prep_id} kind={info.kind} />;

  return (
    <CopilotKit runtimeUrl="/api/copilotkit" agentId="interviewer" properties={{ interviewId: id, agentToken }} enableInspector={false}>
      <LiveRoom id={id} info={info} />
    </CopilotKit>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-full flex-col items-center justify-center bg-call p-6 text-center text-white">{children}</div>;
}

function Preparing({ info }: { info: any }) {
  // Real progress: the profile exists once the resume is read; the plan arrives with status "ready".
  const step = info.status === "created" ? 0 : info.profile?.name ? 2 : 1;
  const name = info.profile?.preferred_name || info.profile?.name;
  const steps = ["Queued", "Reading the resume", name ? `Planning ${name}'s interview` : "Planning the interview"];
  return (
    <Centered>
      <div className="w-full max-w-md text-left">
        <div className="relative mb-8 h-16 w-24" aria-hidden>
          <span className="absolute top-0 left-0 h-16 w-16 animate-pulse rounded-full bg-amber/80" />
          <span className="absolute top-0 left-8 h-16 w-16 rounded-full bg-marine/80 mix-blend-screen" />
        </div>
        <h1 className="font-display text-3xl font-semibold">Getting your interview ready</h1>
        <p className="mt-2 text-white/70">
          Alex is reading the resume, choosing which projects to dig into and picking a coding problem that fits
          {info.role ? ` ${info.role}` : " the role"}. This takes a minute or two.
        </p>
        <ol className="mt-8 space-y-3">
          {steps.map((s, i) => (
            <li key={s} className="flex items-center gap-3">
              <span className={"flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold " +
                (i < step ? "bg-marine text-white" : i === step ? "bg-amber text-onaccent" : "bg-white/10 text-white/50")}>
                {i < step ? "✓" : i + 1}
              </span>
              <span className={i === step ? "font-semibold" : i < step ? "text-white/70" : "text-white/40"}>{s}{i === step && "…"}</span>
            </li>
          ))}
        </ol>
      </div>
    </Centered>
  );
}

function Finished({ id, prepId, kind }: { id: string; prepId?: string | null; kind?: string }) {
  if (kind === "screening") return <ScreeningFinished id={id} prepId={prepId} />;
  return <InterviewFinished id={id} prepId={prepId} />;
}

/** Poll an endpoint until `done` says the background work is finished. */
function usePoll<T>(url: string | null, done: (x: T) => boolean, every = 4000) {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!url) return;
    let stop = false;
    let t: number | undefined;
    const load = async () => {
      try {
        const x = await api<T>(url);
        if (stop) return;
        setData(x);
        if (!done(x)) t = window.setTimeout(load, every);
      } catch {
        if (!stop) t = window.setTimeout(load, every * 2);
      }
    };
    load();
    return () => { stop = true; window.clearTimeout(t); };
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps
  return data;
}

function ScreeningFinished({ id, prepId }: { id: string; prepId?: string | null }) {
  const nav = useNavigate();
  const [attempt, setAttempt] = useState(0);
  const iv = usePoll<any>(`/api/interviews/${id}?a=${attempt}`, (x) => (x.status === "scored" && (x.scorecard?.next_interview_id || x.scorecard?.next_error || !x.prep_id)) || (x.status === "completed" && !!x.error), 3000);
  const failed = iv?.status === "completed" && iv?.error;
  const next = iv?.scorecard?.next_interview_id;
  const nextIv = usePoll<any>(next ? `/api/interviews/${next}` : null, (x) => !["created", "planning"].includes(x.status), 3000);
  const items: any[] = iv?.plan?.items ?? [];
  const captured = iv?.scorecard?.captured ?? {};
  const capturedDone = iv?.status === "scored";
  const ready = nextIv && !["created", "planning"].includes(nextIv.status);
  return (
    <div className="min-h-full overflow-y-auto bg-call px-6 py-12 text-white">
      <div className="mx-auto w-full max-w-3xl">
        <h1 className="font-display text-3xl font-semibold">Thanks, that's the screening call done</h1>
        {failed && (
          <p className="mt-3 rounded-lg bg-coral/20 px-4 py-2.5 text-white">
            Writing it up didn't work this time ({iv.error}).{" "}
            <button className="font-semibold underline" onClick={async () => { await api(`/api/interviews/${id}/rescore`, { method: "POST" }); setAttempt((a) => a + 1); }}>Try again</button>
          </p>
        )}
        <p className="mt-2 text-white/70">
          {capturedDone ? "Here's what we took from it. It's saved to your profile and your application; correct anything on your preparation page."
            : "Writing up what you said: matching your answers to the job and the application form…"}
        </p>
        <div className="mt-6 rounded-2xl bg-white/[.05] p-5">
          <ul className="space-y-3">
            {items.map((it) => {
              const c = captured[it.id];
              return (
                <li key={it.id} className="flex gap-3">
                  <span className={"mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full " + (c ? "bg-marine" : "bg-white/10")}>
                    {c ? <Check size={13} /> : <CircleDashed size={13} className="text-white/40" />}
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm text-white/60">{it.kind === "application" ? "Application question" : "About the role"} · {it.ask}</div>
                    {c ? <div className="text-[15px]">{c.answer} <span className="text-sm text-white/45 italic">“{c.quote}”</span></div>
                      : <div className="text-sm text-white/45">{capturedDone ? "Not covered: you can add it on your preparation page." : "…"}</div>}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          {iv?.scorecard?.next_error ? (
            <p className="text-coral">Setting up the technical round didn't work: {iv.scorecard.next_error}. Start it from your preparation page.</p>
          ) : ready ? (
            <button onClick={() => nav(`/interview/${next}`)} className="rounded-full bg-amber px-6 py-3 font-semibold text-onaccent hover:brightness-105">
              Start the technical round
            </button>
          ) : (
            <span className="rounded-full bg-white/10 px-5 py-2.5 text-white/80">Preparing your technical round… about a minute</span>
          )}
          {prepId && <Link to={`/prep/${prepId}`} className="text-white/75 underline">Review on your preparation page</Link>}
        </div>
      </div>
    </div>
  );
}

function InterviewFinished({ id, prepId }: { id: string; prepId?: string | null }) {
  const [rating, setRating] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);
  const prep = usePoll<any>(prepId ? `/api/prep/${prepId}` : null, (x) => !!x.feedback?.[id], 5000);
  const review: Review | undefined = prep?.feedback?.[id];
  return (
    <div className="min-h-full overflow-y-auto bg-call px-6 py-12 text-white">
      <div className="mx-auto grid w-full max-w-5xl gap-8 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0 space-y-6">
          <div>
            <h1 className="font-display text-3xl font-semibold">Thanks, that's the interview done</h1>
            <p className="mt-2 text-white/70">A person reviews every AI-led interview before any decision.</p>
          </div>
          {prepId && (
            <section className="rounded-2xl bg-surface p-6 text-ink">
              <h2 className="font-display text-xl font-semibold">Your feedback</h2>
              {review ? <div className="mt-4"><FeedbackBody f={review} /></div>
                : <p className="mt-2 text-graphite">Scoring the interview and pulling out what to work on, with quotes from what you said. A couple of minutes; this page updates by itself.</p>}
              <Link to={`/prep/${prepId}`} className="mt-5 inline-block text-sm font-semibold underline">Back to your preparation</Link>
            </section>
          )}
          {prepId && <SimilarJobs prepId={prepId} />}
          {!prepId && <Link to={`/dashboard/${id}`} className="inline-block rounded-full bg-amber px-5 py-2 font-semibold text-onaccent">See the transcript and scorecard</Link>}
        </div>
        <aside className="self-start rounded-2xl bg-white/[.05] p-5">
          <h2 className="font-semibold">How did this one feel?</h2>
          {sent ? (
            <p className="mt-3 font-medium text-marine-soft">Thanks. Your feedback goes into how the interviewer runs future interviews.</p>
          ) : (
            <form className="mt-3 space-y-4" onSubmit={async (e) => {
              e.preventDefault();
              await api(`/api/interviews/${id}/feedback`, { method: "POST", body: JSON.stringify({ rating, comment }) });
              setSent(true);
            }}>
              <fieldset>
                <legend className="text-sm text-white/75">Overall experience</legend>
                <div className="mt-2 flex gap-2">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button type="button" key={n} onClick={() => setRating(n)} aria-pressed={rating === n}
                      className={"h-10 w-10 rounded-full text-sm font-semibold " + (rating === n ? "bg-amber text-onaccent" : "bg-call-tile hover:bg-call-line")}>
                      {n}
                    </button>
                  ))}
                </div>
              </fieldset>
              <label className="block text-sm text-white/75">
                Anything the interviewer could do better?
                <textarea className="mt-1.5 w-full rounded-lg border border-call-line bg-call-2 p-3 text-white" rows={3}
                  value={comment} onChange={(e) => setComment(e.target.value)} />
              </label>
              <button className="rounded-full bg-amber px-5 py-2 font-semibold text-onaccent disabled:opacity-40" disabled={rating === null}>Send feedback</button>
            </form>
          )}
        </aside>
      </div>
    </div>
  );
}

function LiveRoom({ id, info }: { id: string; info: any }) {
  const interviewerName = useAuth().user?.preferences?.interviewer_name || "Alex";
  const [params, setParams] = useSearchParams();
  const wantsChat = params.get("mode") === "chat";
  const screening = info.kind === "screening";
  const [highlight, setHighlight] = useState<{ start_line: number; end_line: number; note: string; at: number } | null>(null);
  const [noteDot, setNoteDot] = useState(false);
  const [panel, setPanel] = useState<Panel>("code");
  const [side, setSide] = useState<"captions" | "chat" | null>("captions");
  const [notes, setNotes] = useState<string>(info.workspace?.notes ?? "");
  const [consent, setConsent] = useState(false);
  const [starting, setStarting] = useState(false);
  const board = useRef<WhiteboardHandle>(null);
  const codeRef = useRef<string>(info.workspace?.code ?? "");
  const boardSummary = useRef("");
  const cam = useCamera();

  // One set of UI handlers, reachable from both transports: WebSocket ui_actions (voice turns) and
  // CopilotKit frontend tools (AG-UI runs).
  const handleUI = useCallback((name: string, args: any) => {
    if (name === "show_panel" && args?.panel) setPanel(args.panel);
    if (name === "draw_on_whiteboard") {
      setPanel("whiteboard");
      setTimeout(() => board.current?.draw(args), 50);
    }
    if (name === "highlight_code" && args?.start_line) {
      setPanel("code");
      setHighlight({ ...args, at: Date.now() });
    }
    if (name === "write_note" && args?.text) {
      setNotes((n) => (n.trim() ? n.trimEnd() + "\n\n" : "") + args.text);
      setNoteDot(true);
    }
  }, []);

  const s = useInterview(id, handleUI);
  if (import.meta.env.DEV) (window as any).__ibUI = handleUI; // dev-only: exercise agent UI actions from the console

  useFrontendTool({
    name: "show_panel",
    description: "Bring a workspace panel to the front",
    parameters: z.object({ panel: z.enum(["code", "whiteboard", "notes"]) }),
    handler: async ({ panel }) => {
      handleUI("show_panel", { panel });
      return { ok: true };
    },
  });
  useFrontendTool({
    name: "draw_on_whiteboard",
    description: "Sketch labelled nodes and arrows on the whiteboard",
    parameters: z.object({
      title: z.string().optional(),
      nodes: z.array(z.object({ id: z.string(), label: z.string(), shape: z.enum(["rectangle", "ellipse", "diamond"]).optional() })),
      edges: z.array(z.object({ from: z.string(), to: z.string(), label: z.string().optional() })),
    }),
    handler: async (args) => {
      handleUI("draw_on_whiteboard", args);
      return { ok: true };
    },
  });
  useAgentContext({ description: "Workspace panel the candidate is looking at", value: panel });

  useEffect(() => {
    const t = setTimeout(() => s.syncWorkspace({ notes }), 800);
    return () => clearTimeout(t);
  }, [notes]); // eslint-disable-line react-hooks/exhaustive-deps

  const elapsed = s.state?.elapsed_min ?? 0;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const startedAt = useRef<number | null>(null);
  if (s.state && startedAt.current === null && s.started) startedAt.current = Date.now() - elapsed * 60000;
  const mins = startedAt.current ? (Date.now() - startedAt.current) / 60000 : elapsed;
  void tick;

  if (s.state?.ended) return <Finished id={id} prepId={info.prep_id} kind={info.kind} />;
  const chat = s.chatMode;

  const you = info.profile?.preferred_name || info.profile?.name || "You";
  const INTERVIEWER = interviewerName;
  const interviewerTalking = s.activity === "speaking";
  const youTalking = s.activity === "you" || s.activity === "holding";

  if (!s.started) {
    return (
      <div className="flex h-full flex-col bg-call text-white">
        <header className="flex items-center px-6 py-4">
          <Link to="/" className="font-display text-lg font-bold tracking-tight">InterviewBud</Link>
        </header>
        <div className="mx-auto grid w-full max-w-6xl flex-1 content-center items-center gap-10 px-6 pb-10 lg:grid-cols-[1.4fr_1fr]">
          <div className="relative">
            <Tile name={you} initial={you} tone="marine" stream={cam.stream} speaking={false} levels={s.levels} who="candidate" big />
            <div className="absolute inset-x-0 bottom-4 flex justify-center gap-3">
              <RoundButton label={cam.on ? "Turn off camera" : "Turn on camera"} off={!cam.on} onClick={cam.toggle}>
                {cam.on ? <Video size={20} /> : <VideoOff size={20} />}
              </RoundButton>
            </div>
            {cam.error && <p className="mt-2 text-sm text-white/60">{cam.error}</p>}
          </div>
          <div>
            <h1 className="font-display text-[34px] leading-tight font-semibold">{screening ? "Ready for a quick chat?" : "Ready to join?"}</h1>
            <p className="mt-2 text-white/75">
              {screening
                ? <>{INTERVIEWER} will ask about your background, your projects and a few questions from the {info.role} application. About {info.duration_min} minutes. What you say fills in your profile and application, and you check it all afterwards.</>
                : <>{INTERVIEWER}, your AI interviewer, is ready for your {info.role} interview. About {info.duration_min} minutes: your background, your projects, then some coding together.</>}
            </p>
            <div role="radiogroup" aria-label="How you'd like to talk" className="mt-5 grid grid-cols-2 gap-2">
              {([["voice", "Talk", <Mic size={16} key="m" />], ["chat", "Type instead", <Keyboard size={16} key="k" />]] as const).map(([m, label, icon]) => {
                const on = (m === "chat") === wantsChat;
                return (
                  <button key={m} role="radio" aria-checked={on} onClick={() => setParams(m === "chat" ? { mode: "chat" } : {}, { replace: true })}
                    className={"flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm " + (on ? "bg-white text-onaccent font-semibold" : "bg-white/[.06] text-white/75 hover:bg-white/10")}>
                    {icon} {label}
                  </button>
                );
              })}
            </div>
            <div className="mt-3 rounded-xl bg-white/[.05] p-4">
              <div className="text-sm font-semibold">How this works</div>
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-white/75">
                {wantsChat ? (<>
                  <li>Type your answers; {INTERVIEWER} replies in the chat. No microphone needed.</li>
                  <li>Take as long as you like over each answer.</li>
                </>) : (<>
                  <li>Just talk. When you pause, {INTERVIEWER} answers, like a normal call.</li>
                  <li>Interrupt any time, or say "give me a moment" to think; it waits.</li>
                  <li>Headphones keep the interviewer from hearing itself.</li>
                </>)}
                {screening && <li>You'll see each answer get captured as you go.</li>}
              </ol>
            </div>
            {!wantsChat && <MicCheck />}
            <p className="mt-3 text-xs text-white/45">Your camera stays on this screen; it's never recorded or sent.</p>
            <label className="mt-5 flex gap-3 text-sm text-white/85">
              <input type="checkbox" className="mt-1 accent-amber" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>
                {screening
                  ? "I understand this call is conducted and transcribed by AI, and that my answers are saved to my profile and used to fill in my application, which I review before anything is sent. Audio isn't stored; the transcript is."
                  : "I understand this interview is conducted and transcribed by AI, that it assesses communication, technical depth, ownership, problem solving, coding and testing, and that a person reviews the results. Audio isn't stored; the transcript and code are."}
              </span>
            </label>
            <button
              className="mt-6 rounded-full bg-amber px-8 py-3 font-semibold text-onaccent hover:brightness-105 disabled:opacity-40"
              disabled={!consent || !s.connected || starting}
              onClick={async () => {
                setStarting(true);
                if (wantsChat) {
                  s.startChat();
                  track("interview_joined", { duration: info.duration_min, mode: "chat", kind: info.kind });
                  return;
                }
                try {
                  await s.start();
                  track("interview_joined", { duration: info.duration_min, kind: info.kind });
                } catch (e) {
                  alert(`Microphone or audio couldn't start: ${e}`);
                  setStarting(false);
                }
              }}
            >
              {starting ? "Joining…" : "Join now"}
            </button>
            {!s.connected && <p className="mt-2 text-sm text-white/50">Connecting to the interview…</p>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-call text-white">
      <header className="flex items-center gap-6 px-5 pt-3 pb-2">
        <Link to="/" className="font-display text-base font-bold tracking-tight">InterviewBud</Link>
        <div className="mx-auto hidden w-full max-w-2xl md:block">
          <PhaseTimeline state={s.state} />
        </div>
        <span className={"h-2 w-2 shrink-0 rounded-full " + (s.connected ? "bg-marine" : "bg-coral")}
          title={s.connected ? "Connected" : "Reconnecting"} />
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 px-3 md:grid-cols-[1fr_320px]">
        {screening ? <CapturePanel items={s.captured.length ? s.captured : (info.plan?.items ?? [])} interviewer={INTERVIEWER} /> : (
        /* Stage: the shared workspace, presented like a shared screen */
        <section className="flex min-h-[60vh] flex-col overflow-hidden rounded-2xl bg-call-2 md:min-h-0">
          <nav className="flex items-center gap-1 px-3 py-2" role="tablist">
            {(["code", "whiteboard", "notes"] as const).map((p) => (
              <button key={p} role="tab" aria-selected={panel === p} onClick={() => { setPanel(p); if (p === "notes") setNoteDot(false); }}
                className={"relative rounded-full px-4 py-1.5 text-sm capitalize " + (panel === p ? "bg-white text-onaccent font-semibold" : "text-white/70 hover:bg-white/10")}>
                {p}
                {p === "notes" && noteDot && panel !== "notes" && <span className="absolute top-1 right-1.5 h-2 w-2 rounded-full bg-amber" aria-label="new note" />}
              </button>
            ))}
            <span className="ml-auto text-xs text-white/50">{INTERVIEWER} can see this workspace</span>
          </nav>
          <div className="mx-2 mb-2 min-h-0 flex-1 overflow-hidden rounded-xl bg-paper text-ink">
            <div className={panel === "code" ? "h-full" : "hidden"}>
              <CodePanel
                problem={s.problem}
                result={s.codeResult}
                running={s.codeRunning}
                initialCode={codeRef.current}
                onChange={(code) => {
                  codeRef.current = code;
                  s.syncWorkspace({ code, language: "python" });
                }}
                onRun={(code, mode) => s.runCode(code, mode)}
                highlight={highlight}
              />
            </div>
            <div className={panel === "whiteboard" ? "h-full" : "hidden"}>
              <Whiteboard
                ref={board}
                initial={info.workspace?.whiteboard}
                onChange={(summary, elements) => {
                  boardSummary.current = summary;
                  s.syncWorkspace({ whiteboard_summary: summary, whiteboard: elements });
                }}
              />
            </div>
            <div className={panel === "notes" ? "graph-paper h-full p-6" : "hidden"}>
              <label className="mb-2 block text-sm text-graphite" htmlFor="notes">
                Scratch notes: requirements, edge cases, complexity. Shared with your interviewer.
              </label>
              <textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)}
                className="h-[calc(100%-2rem)] w-full resize-none rounded-md border border-line bg-surface p-4 font-mono text-sm leading-relaxed" />
            </div>
          </div>
        </section>)}

        {/* People + captions/chat, like a call's side strip */}
        <aside className="flex min-h-0 flex-col gap-3">
          <Tile name={`${INTERVIEWER} · AI interviewer`} initial={INTERVIEWER} tone="amber" speaking={interviewerTalking}
            levels={s.levels} who="interviewer" status={s.activity === "thinking" ? "Thinking…" : undefined} />
          <Tile name={`${you} (you)`} initial={you} tone="marine" stream={cam.stream} speaking={youTalking}
            levels={s.levels} who="candidate" muted={!s.micOn}
            status={s.activity === "holding" ? "Take your time" : undefined} />
          {(side || chat) && (
            <div className="flex min-h-[220px] flex-1 flex-col overflow-hidden rounded-2xl bg-call-2">
              <div className={"flex items-center gap-1 px-3 pt-2" + (chat ? " hidden" : "")}>
                {(["captions", "chat"] as const).map((t) => (
                  <button key={t} onClick={() => setSide(t)} aria-pressed={side === t}
                    className={"rounded-full px-3 py-1 text-sm capitalize " + (side === t ? "bg-white/15 font-semibold" : "text-white/60 hover:text-white")}>
                    {t === "captions" ? "Transcript" : "Chat"}
                  </button>
                ))}
              </div>
              {chat ? (
                <ChatBox lines={s.lines} you={you} interviewer={INTERVIEWER} thinking={s.activity === "thinking"} onSend={s.sendText} />
              ) : side === "captions" ? <Transcript lines={s.lines} you={you} interviewer={INTERVIEWER} /> : (
                <div className="min-h-0 flex-1 bg-paper text-ink">
                  <CopilotChat className="ib-chat" agentId="interviewer" threadId={id}
                    labels={{ chatInputPlaceholder: "Type to your interviewer…" }} />
                </div>
              )}
            </div>
          )}
        </aside>
      </div>

      {s.error && (
        <div className="mx-auto mt-2 rounded-full bg-coral px-4 py-1.5 text-sm" role="alert">{s.error}</div>
      )}

      <footer className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-5 py-3">
        <div className="truncate text-sm text-white/80">
          <span className="tabular-nums">{fmtClock(mins)} / {s.state?.duration_min ?? info.duration_min}:00</span>
          <span className="mx-2 text-white/30">|</span>
          <span>{info.role}</span>
        </div>
        <div className="flex items-center gap-3">
          {!chat && (
            <RoundButton label={s.micOn ? "Mute microphone" : "Unmute microphone"} off={!s.micOn} onClick={s.toggleMic}>
              {s.micOn ? <Mic size={20} /> : <MicOff size={20} />}
            </RoundButton>
          )}
          <RoundButton label={cam.on ? "Turn off camera" : "Turn on camera"} off={!cam.on} onClick={cam.toggle}>
            {cam.on ? <Video size={20} /> : <VideoOff size={20} />}
          </RoundButton>
          {!chat && (<>
            <RoundButton label="Transcript" active={side === "captions"} onClick={() => setSide(side === "captions" ? null : "captions")}>
              <Captions size={20} />
            </RoundButton>
            <RoundButton label="Chat with the interviewer" active={side === "chat"} onClick={() => setSide(side === "chat" ? null : "chat")}>
              <MessageSquareText size={20} />
            </RoundButton>
          </>)}
          <button aria-label="End the interview" title="End the interview"
            className="flex h-11 w-16 items-center justify-center rounded-full bg-hangup hover:brightness-110"
            onClick={() => {
              if (confirm(screening ? "End the call now?" : "End the interview now?")) s.end();
            }}>
            <PhoneOff size={20} />
          </button>
        </div>
        <div className="text-right text-sm text-white/50">
          {s.lastLatency !== null && (
            <span title="Time from when you stopped talking to the interviewer's first word">
              replied in {(s.lastLatency / 1000).toFixed(1)}s
            </span>
          )}
        </div>
      </footer>
    </div>
  );
}


/** Pre-join check: real mic level and the interviewer's real voice, so problems show up before the interview. */
function MicCheck() {
  const [level, setLevel] = useState(0);
  const [state, setState] = useState<"idle" | "on" | "denied">("idle");
  const [heard, setHeard] = useState(false);
  const [playing, setPlaying] = useState(false);
  const stop = useRef<() => void>(() => {});
  useEffect(() => () => stop.current(), []);
  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const ctx = new AudioContext();
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Float32Array(an.fftSize);
      let raf = 0;
      const loop = () => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const rms = Math.min(1, Math.sqrt(sum / buf.length) * 8);
        setLevel(rms);
        if (rms > 0.25) setHeard(true);
        raf = requestAnimationFrame(loop);
      };
      loop();
      stop.current = () => { cancelAnimationFrame(raf); stream.getTracks().forEach((tr) => tr.stop()); void ctx.close(); };
      setState("on");
    } catch {
      setState("denied");
    }
  };
  return (
    <div className="mt-4 rounded-xl bg-white/[.05] p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold">Check your setup</span>
        {state !== "on" && (
          <button type="button" onClick={start} className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1.5 text-sm hover:bg-white/15">
            <Mic size={15} /> Test microphone
          </button>
        )}
        <button type="button" disabled={playing} className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1.5 text-sm hover:bg-white/15 disabled:opacity-50"
          onClick={async () => {
            setPlaying(true);
            try { await playPreview(new AudioContext(), "", "Hi! If you can hear me clearly, you're all set."); } catch { /* shown by the room if voice is down */ }
            setPlaying(false);
          }}>
          <Volume2 size={15} /> {playing ? "Playing…" : "Hear the interviewer"}
        </button>
      </div>
      {state === "on" && (
        <div className="mt-3">
          <div className="h-2 overflow-hidden rounded-full bg-white/10" aria-label="Microphone level">
            <div className="h-full rounded-full bg-marine transition-[width] duration-75" style={{ width: `${Math.round(level * 100)}%` }} />
          </div>
          <p className="mt-1.5 text-sm text-white/70">{heard ? "We can hear you. You're ready." : "Say something: the bar should move."}</p>
        </div>
      )}
      {state === "denied" && <p className="mt-2 text-sm text-coral">The browser blocked the microphone. Allow it in the address bar's site settings, or type during the interview instead.</p>}
    </div>
  );
}

function useCamera() {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toggle = useCallback(async () => {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      setStream(null);
      return;
    }
    try {
      setStream(await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false }));
      setError(null);
    } catch {
      setError("Camera unavailable. That's fine, the interview is audio only.");
    }
  }, [stream]);
  useEffect(() => () => stream?.getTracks().forEach((t) => t.stop()), [stream]);
  return { stream, on: !!stream, toggle, error };
}

/** A call participant: camera if there is one, otherwise their initial; a ring that moves with their voice. */
function Tile({ name, initial, tone, stream, speaking, levels, who, muted, status, big }: {
  name: string; initial: string; tone: "amber" | "marine"; stream?: MediaStream | null; speaking: boolean;
  levels: React.RefObject<{ interviewer: number; candidate: number }>; who: "interviewer" | "candidate";
  muted?: boolean; status?: string; big?: boolean;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const ring = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (video.current) video.current.srcObject = stream ?? null;
  }, [stream]);
  useEffect(() => {
    // Drive the voice ring straight from the audio level, without re-rendering React every frame.
    let raf = 0;
    let smooth = 0;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const loop = () => {
      const lv = levels.current ?? { interviewer: 0, candidate: 0 };
      const raw = who === "interviewer" ? Math.min(1, lv.interviewer * 6) : lv.candidate;
      smooth += ((speaking ? raw : 0) - smooth) * 0.2;
      if (ring.current) {
        const s = reduce ? (speaking ? 0.5 : 0) : smooth;
        ring.current.style.boxShadow = `0 0 0 ${3 + s * 10}px var(--ring)`;
        ring.current.style.opacity = speaking ? String(0.55 + s * 0.45) : "0";
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [levels, who, speaking]);
  const accent = tone === "amber" ? "var(--color-amber)" : "var(--color-marine)";
  return (
    <div className={"relative overflow-hidden rounded-2xl bg-call-tile " + (big ? "aspect-video" : "aspect-[16/10]")}
      style={{ outline: speaking ? `2px solid ${accent}` : "none", outlineOffset: -2 }}>
      {stream ? (
        <video ref={video} autoPlay playsInline muted className="h-full w-full -scale-x-100 object-cover" />
      ) : (
        <div className="flex h-full items-center justify-center">
          <div className="relative">
            <div ref={ring} className="absolute inset-0 rounded-full transition-opacity"
              style={{ ["--ring" as any]: tone === "amber" ? "rgba(236,164,0,.45)" : "rgba(1,111,185,.5)" }} />
            <div className={"relative flex items-center justify-center rounded-full font-display font-semibold " + (tone === "amber" ? "text-onaccent " : "text-white ") + (big ? "h-36 w-36 text-6xl" : "h-20 w-20 text-3xl")}
              style={{ background: accent }}>
              {initial.trim().charAt(0).toUpperCase()}
            </div>
          </div>
        </div>
      )}
      <div className="absolute bottom-2 left-3 flex items-center gap-1.5 rounded-md bg-black/40 px-2 py-0.5 text-sm">
        {muted && <MicOff size={14} className="text-alarm" />}
        <span>{name}</span>
      </div>
      {status && <div className="absolute top-2 right-3 rounded-full bg-black/40 px-2.5 py-0.5 text-xs text-white/85">{status}</div>}
    </div>
  );
}

function RoundButton({ label, off, active, onClick, children }: {
  label: string; off?: boolean; active?: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button aria-label={label} title={label} aria-pressed={active ?? (off === undefined ? undefined : !off)} onClick={onClick}
      className={"flex h-11 w-11 items-center justify-center rounded-full transition-colors " +
        (off ? "bg-coral hover:brightness-110" : active ? "bg-amber-soft text-ink" : "bg-call-tile hover:bg-call-line")}>
      {children}
    </button>
  );
}

function Transcript({ lines, you, interviewer }: { lines: ReturnType<typeof useInterview>["lines"]; you: string; interviewer: string }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" }); // returns a Promise in current Chrome: not a cleanup
  }, [lines]);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" aria-live="polite">
      <ol className="space-y-3 text-[15px] leading-snug">
        {lines.map((l) => (
          <li key={l.key} className={l.partial ? "opacity-70" : ""}>
            <div className={"text-xs font-bold " + (l.speaker === "interviewer" ? "text-amber" : "text-marine-soft")}>
              {l.speaker === "interviewer" ? interviewer : you}
            </div>
            <span className="text-white/90">{l.text}</span>
            {l.interrupted && <span className="ml-1 text-xs text-white/40">(cut off)</span>}
          </li>
        ))}
      </ol>
      <div ref={end} />
    </div>
  );
}

function fmtClock(min: number) {
  const s = Math.max(0, Math.floor(min * 60));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Screening call stage: the checklist of what the call needs to cover, filling in from the candidate's own words. */
function CapturePanel({ items, interviewer }: { items: CaptureItem[]; interviewer: string }) {
  const done = items.filter((i) => i.captured).length;
  return (
    <section className="flex min-h-[60vh] flex-col overflow-hidden rounded-2xl bg-call-2 md:min-h-0">
      <div className="flex items-baseline justify-between gap-3 px-5 pt-4">
        <h2 className="font-display text-lg font-semibold">What {interviewer} is filling in</h2>
        <span className="text-sm text-white/60 tabular-nums">{done} of {items.length}</span>
      </div>
      <div className="mx-5 mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
        <div className="h-full rounded-full bg-marine transition-[width] duration-500" style={{ width: `${items.length ? (done / items.length) * 100 : 0}%` }} />
      </div>
      <ul className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-5 py-4">
        {items.map((it) => (
          <li key={it.id} className={"rounded-xl p-3.5 transition-colors " + (it.captured ? "bg-marine/15" : "bg-white/[.04]")}>
            <div className="flex items-start gap-3">
              <span className={"mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full " + (it.captured ? "bg-marine" : "bg-white/10")}>
                {it.captured ? <Check size={13} /> : <CircleDashed size={13} className="text-white/40" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] text-white/50">{it.kind === "application" ? "From the application form" : "About the role"}{it.required ? " · important" : ""}</div>
                <div className="text-[15px] text-white/90">{it.ask}</div>
                {it.captured && (
                  <div className="mt-1.5 text-[15px]">
                    <span className="font-semibold text-white">{it.captured.answer}</span>
                    <div className="mt-0.5 flex gap-1.5 text-sm text-white/50 italic"><Quote size={12} className="mt-1 shrink-0" />{it.captured.quote}</div>
                  </div>
                )}
              </div>
            </div>
          </li>
        ))}
        {!items.length && <li className="text-white/60">Nothing specific to cover: just a chat about your background.</li>}
      </ul>
      <p className="px-5 pb-4 text-xs text-white/45">Only your own words count. You can correct anything after the call.</p>
    </section>
  );
}

/** Typed conversation over the same connection as voice. */
function ChatBox({ lines, you, interviewer, thinking, onSend }: {
  lines: ReturnType<typeof useInterview>["lines"]; you: string; interviewer: string; thinking: boolean; onSend: (t: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Transcript lines={lines} you={you} interviewer={interviewer} />
      {thinking && <div className="px-4 pb-1 text-xs text-white/50">{interviewer} is typing…</div>}
      <form className="flex items-end gap-2 border-t border-white/10 p-3" onSubmit={(e) => {
        e.preventDefault();
        if (!text.trim()) return;
        onSend(text.trim());
        setText("");
      }}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} aria-label="Your answer" placeholder="Type your answer…"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); (e.currentTarget.form as HTMLFormElement).requestSubmit(); } }}
          className="min-w-0 flex-1 resize-none rounded-xl border border-call-line bg-call p-2.5 text-[15px] text-white placeholder:text-white/40" />
        <button aria-label="Send" className="flex h-10 w-10 items-center justify-center rounded-full bg-amber text-onaccent disabled:opacity-40" disabled={!text.trim()}>
          <Send size={17} />
        </button>
      </form>
    </div>
  );
}

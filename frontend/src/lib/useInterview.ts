import { MicVAD } from "@ricky0123/vad-web";
import { useCallback, useEffect, useRef, useState } from "react";
import { encodeWav, PCMPlayer } from "./audio";

export type Speaker = "interviewer" | "candidate";
export type Line = { key: string; speaker: Speaker; text: string; partial?: boolean; interrupted?: boolean };
export type Activity = "idle" | "listening" | "you" | "holding" | "thinking" | "speaking";

export type Problem = {
  id: number;
  slug: string;
  title: string;
  difficulty: string;
  tags: string[];
  statement: string;
  examples: { input: string; output: string; explanation: string }[];
  constraints: string[];
  follow_up: string;
  starter_code: string;
  visible_tests: { id: number; kind: string; input: string; expected: string; why: string }[];
  hidden_count: number;
  hard_count: number;
};

export type CodeResult = {
  id: number;
  mode: "run" | "submit";
  summary: {
    compile_error: string | null;
    killed: string | null;
    passed: number;
    total: number;
    timeouts: number;
    errors: number;
    by_kind: Record<string, { passed: number; total: number }>;
    max_ms: number;
  };
  visible: { id: number; kind: string; status: string; ms: number; input: string; actual: string | null;
    expected: string | null; stdout: string; error: string; rationale: string }[];
  hidden: { id: number; kind: string; status: string; ms: number }[];
};

export type SessionState = {
  phase: string;
  elapsed_min: number;
  duration_min: number;
  phase_budgets: { phase: string; minutes: number; goals: string[] }[];
  phase_history: { phase: string; minutes: number }[];
  voice_enabled: boolean;
  ended: boolean;
  sample_rate: number;
};

type UIHandler = (name: string, args: any) => void;

// Silence (ms) before the browser treats a pause as end of utterance. Longer while coding: people pause to think.
const REDEMPTION = { default: 600, coding: 900 };

export function useInterview(interviewId: string, onUIAction: UIHandler) {
  const [connected, setConnected] = useState(false);
  const [started, setStarted] = useState(false);
  const [state, setState] = useState<SessionState | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [activity, setActivity] = useState<Activity>("idle");
  const [problem, setProblem] = useState<Problem | null>(null);
  const [codeResult, setCodeResult] = useState<CodeResult | null>(null);
  const [codeRunning, setCodeRunning] = useState<null | "run" | "submit">(null);
  const [memory, setMemory] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [micOn, setMicOn] = useState(true);
  const [lastLatency, setLastLatency] = useState<number | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const player = useRef<PCMPlayer | null>(null);
  const vad = useRef<MicVAD | null>(null);
  const currentTurn = useRef(0);
  const speakingTurn = useRef(0);
  const speechEndAt = useRef<number | null>(null);
  const awaitingFirstAudio = useRef(false);
  const playing = useRef(false);
  const bargeTimer = useRef<number | null>(null);
  const levels = useRef({ interviewer: 0, candidate: 0 });
  const uiRef = useRef(onUIAction);
  uiRef.current = onUIAction;
  const phaseRef = useRef("intro");

  const send = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  }, []);

  const upsertInterviewer = (turnId: number, fn: (prev: string) => string, extra?: Partial<Line>) => {
    const key = `i-${turnId}`;
    setLines((ls) => {
      const i = ls.findIndex((l) => l.key === key);
      if (i === -1) return [...ls, { key, speaker: "interviewer", text: fn(""), ...extra }];
      const copy = ls.slice();
      copy[i] = { ...copy[i], text: fn(copy[i].text), ...extra };
      return copy;
    });
  };

  // ---------- websocket ----------
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let closedByUs = false;
    let retry: number | undefined;
    const connect = () => {
      const sock = new WebSocket(`${proto}://${location.host}/ws/session/${interviewId}`);
      sock.binaryType = "arraybuffer";
      ws.current = sock;
      sock.onopen = () => {
        if (ws.current === sock) setConnected(true);
      };
      sock.onclose = (ev) => {
        if (ws.current !== sock) return; // a replaced socket closing late must not flip state
        setConnected(false);
        // Terminal closes: 4000 another tab took over, 4001 interview over, 44xx not allowed. Reconnecting
        // would fight the other tab (each replacing the other) or just fail again.
        if (ev.code === 4000) {
          closedByUs = true;
          setError("This interview is open in another tab or window, so this one stopped. Use that one, or reload here.");
        } else if (ev.code === 4001 || (ev.code >= 4400 && ev.code < 4500)) {
          closedByUs = true;
        }
        if (!closedByUs) retry = window.setTimeout(connect, 1500);
      };
      sock.onmessage = (ev) => {
        if (ws.current !== sock) return;
        if (ev.data instanceof ArrayBuffer) {
          const turnId = new DataView(ev.data).getUint32(0, true);
          if (turnId !== currentTurn.current) return; // stale audio from an interrupted turn
          player.current?.push(ev.data.slice(4));
          return;
        }
        const m = JSON.parse(ev.data);
        switch (m.type) {
          case "state":
            setState(m);
            phaseRef.current = m.phase;
            vad.current?.setOptions({
              redemptionMs: m.phase === "coding" ? REDEMPTION.coding : REDEMPTION.default,
            });
            break;
          case "history":
            setLines(
              m.turns.map((t: any) => ({ key: `h-${t.seq}`, speaker: t.speaker as Speaker, text: t.text })),
            );
            break;
          case "thinking":
            currentTurn.current = m.turn_id;
            setActivity("thinking");
            break;
          case "agent_turn_start":
            currentTurn.current = m.turn_id;
            break;
          case "audio_start":
            currentTurn.current = m.turn_id;
            speakingTurn.current = m.turn_id;
            awaitingFirstAudio.current = true;
            break;
          case "agent_text_delta":
            upsertInterviewer(m.turn_id, (p) => p + m.delta, { partial: true });
            break;
          case "agent_turn_end":
            if (m.listen) {
              setLines((ls) => ls.filter((l) => l.key !== `i-${m.turn_id}`));
              setActivity("listening");
            } else {
              upsertInterviewer(m.turn_id, () => m.text, { partial: false });
              if (!playing.current) setActivity("listening");
            }
            break;
          case "interrupt":
            if (m.turn_id === currentTurn.current) {
              player.current?.clear();
              player.current?.duck(1);
            }
            setLines((ls) =>
              ls.map((l) => (l.key === `i-${m.turn_id}` ? { ...l, partial: false, interrupted: true } : l)),
            );
            break;
          case "resume":
            player.current?.duck(1);
            break;
          case "listening_hold":
            setActivity("holding");
            break;
          case "transcript":
            if (m.speaker === "candidate") {
              setLines((ls) => {
                const withoutPartial = ls.filter((l) => !(l.speaker === "candidate" && l.partial));
                const prevPartial = ls.filter((l) => l.speaker === "candidate" && l.partial).map((l) => l.text);
                if (m.partial) {
                  return [...withoutPartial, { key: `cp-${Date.now()}`, speaker: "candidate", text:
                    [...prevPartial, m.text].join(" "), partial: true }];
                }
                return [...withoutPartial, { key: `c-${m.seq ?? Date.now()}`, speaker: "candidate", text: m.text }];
              });
            }
            break;
          case "problem":
            setProblem(m.problem);
            break;
          case "code_running":
            setCodeRunning(m.mode);
            break;
          case "code_result":
            setCodeRunning(null);
            setCodeResult(m.run);
            break;
          case "memory":
            setMemory(m.memory);
            break;
          case "ui_action":
            uiRef.current(m.name, m.args);
            break;
          case "ended": {
            closedByUs = true; // over: never reconnect
            const finish = () => {
              void vad.current?.destroy();
              vad.current = null;
              player.current?.close();
              player.current = null;
              setStarted(false);
              setState((s) => ({ ...(s ?? ({} as SessionState)), ended: true }));
              if (ws.current === sock) sock.close(1000, "interview ended");
            };
            // Let the interviewer's goodbye finish playing (up to 15 s) before switching to the end screen.
            const t0 = performance.now();
            const wait = () => (playing.current && performance.now() - t0 < 15000 ? window.setTimeout(wait, 200) : finish());
            wait();
            break;
          }
          case "error":
            setError(m.message);
            if (m.fatal) closedByUs = true;
            window.setTimeout(() => setError(null), 8000);
            break;
        }
      };
    };
    connect();
    return () => {
      closedByUs = true;
      window.clearTimeout(retry);
      ws.current?.close();
    };
  }, [interviewId]);

  // ---------- start: needs a user gesture for audio + mic ----------
  const start = useCallback(async () => {
    const sr = state?.sample_rate ?? 24000;
    const p = new PCMPlayer(sr);
    await p.init();
    p.onLevel = (rms) => (levels.current.interviewer = rms);
    p.onStarted = () => {
      playing.current = true;
      setActivity("speaking");
      if (awaitingFirstAudio.current) {
        awaitingFirstAudio.current = false;
        if (speechEndAt.current) {
          const v2v = Math.round(performance.now() - speechEndAt.current);
          setLastLatency(v2v);
          send({ type: "playback_started", turn_id: speakingTurn.current, voice_to_voice_ms: v2v });
          speechEndAt.current = null;
        }
      }
    };
    p.onDrained = () => {
      playing.current = false;
      setActivity((a) => (a === "speaking" ? "listening" : a));
    };
    player.current = p;

    const v = await MicVAD.new({
      model: "v5",
      baseAssetPath: "/vad/",
      onnxWASMBasePath: "/vad/",
      positiveSpeechThreshold: 0.55,
      negativeSpeechThreshold: 0.4,
      redemptionMs: REDEMPTION.default,
      minSpeechMs: 250,
      preSpeechPadMs: 300,
      onFrameProcessed: (probs) => {
        levels.current.candidate = probs.isSpeech;
      },
      onSpeechStart: () => {
        send({ type: "speech_start" });
      },
      onSpeechRealStart: () => {
        setActivity("you");
        if (playing.current) {
          // Duck first; only a sustained interruption stops the interviewer (so "mm-hmm" doesn't cut them off).
          p.duck(0.25);
          if (bargeTimer.current) window.clearTimeout(bargeTimer.current);
          bargeTimer.current = window.setTimeout(() => {
            p.clear();
            p.duck(1);
            currentTurn.current = -1; // drop whatever is still in flight for the interrupted turn
            send({ type: "barge_in" });
          }, 700);
        }
      },
      onSpeechEnd: (audio) => {
        if (bargeTimer.current) window.clearTimeout(bargeTimer.current);
        speechEndAt.current = performance.now();
        setActivity("thinking");
        const wav = encodeWav(audio);
        if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(wav);
      },
      onVADMisfire: () => {
        if (bargeTimer.current) window.clearTimeout(bargeTimer.current);
        p.duck(1);
        send({ type: "speech_misfire" });
        setActivity(playing.current ? "speaking" : "listening");
      },
    });
    vad.current = v;
    await v.start();
    setStarted(true);
    setActivity("listening");
    send({ type: "start" });
  }, [send, state?.sample_rate]);

  useEffect(
    () => () => {
      void vad.current?.destroy();
      player.current?.close();
    },
    [],
  );

  const toggleMic = useCallback(async () => {
    if (!vad.current) return;
    if (micOn) await vad.current.pause();
    else await vad.current.start();
    setMicOn(!micOn);
  }, [micOn]);

  const sendText = useCallback(
    (text: string) => {
      speechEndAt.current = performance.now();
      send({ type: "text", text });
    },
    [send],
  );

  const runCode = useCallback((code: string, mode: "run" | "submit") => send({ type: "run_code", code, mode }), [send]);
  const syncWorkspace = useCallback((patch: object) => send({ type: "workspace", ...patch }), [send]);
  const end = useCallback(() => send({ type: "end" }), [send]);

  return {
    connected, started, state, lines, activity, problem, codeResult, codeRunning, memory, error, micOn,
    lastLatency, levels, start, toggleMic, sendText, runCode, syncWorkspace, end,
  };
}

import { useEffect, useRef } from "react";
import type { Activity } from "../lib/useInterview";

const AMBER = [236, 164, 0];
const MARINE = [1, 111, 185];

/** The interviewer's presence: a ring that breathes with whoever is talking (amber = interviewer, marine = you). */
export function VoicePresence({
  levels,
  activity,
  size = 220,
}: {
  levels: React.RefObject<{ interviewer: number; candidate: number }>;
  activity: Activity;
  size?: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const act = useRef(activity);
  act.current = activity;

  useEffect(() => {
    const c = canvas.current!;
    const dpr = window.devicePixelRatio || 1;
    c.width = size * dpr;
    c.height = size * dpr;
    const ctx = c.getContext("2d")!;
    ctx.scale(dpr, dpr);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let smooth = 0;
    let t = 0;
    const draw = () => {
      t += 1 / 60;
      const lv = levels.current ?? { interviewer: 0, candidate: 0 };
      const a = act.current;
      const isYou = a === "you" || a === "holding";
      const raw = a === "speaking" ? Math.min(1, lv.interviewer * 6) : isYou ? lv.candidate : 0;
      smooth += (raw - smooth) * 0.18;
      const col = isYou ? MARINE : AMBER;
      const cx = size / 2;
      const base = size * 0.3;
      ctx.clearRect(0, 0, size, size);

      // Outer halo: amplitude
      const halo = base + 10 + smooth * size * 0.12;
      const g = ctx.createRadialGradient(cx, cx, base * 0.6, cx, cx, halo + 18);
      g.addColorStop(0, `rgba(${col.join(",")},0.35)`);
      g.addColorStop(1, `rgba(${col.join(",")},0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cx, halo + 18, 0, Math.PI * 2);
      ctx.fill();

      // Ring: wobbles with voice; slow orbit while thinking
      ctx.lineWidth = 3;
      ctx.strokeStyle = `rgba(${col.join(",")},${a === "idle" ? 0.35 : 0.95})`;
      ctx.beginPath();
      const pts = 96;
      for (let i = 0; i <= pts; i++) {
        const ang = (i / pts) * Math.PI * 2;
        const wob = reduce ? 0 : Math.sin(ang * 5 + t * 4) * smooth * 9 + Math.sin(ang * 3 - t * 2.5) * smooth * 6;
        const r = base + wob;
        const x = cx + Math.cos(ang) * r;
        const y = cx + Math.sin(ang) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      if (a === "thinking" && !reduce) {
        ctx.strokeStyle = `rgba(${AMBER.join(",")},0.9)`;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(cx, cx, base + 14, t * 3, t * 3 + Math.PI * 0.5);
        ctx.stroke();
      }

      // Core
      ctx.fillStyle = "#272727";
      ctx.beginPath();
      ctx.arc(cx, cx, base - 8, 0, Math.PI * 2);
      ctx.fill();
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [levels, size]);

  return <canvas ref={canvas} style={{ width: size, height: size }} aria-hidden="true" />;
}

export const ACTIVITY_LABEL: Record<Activity, string> = {
  idle: "Not started",
  listening: "Listening",
  you: "You're speaking",
  holding: "Take your time",
  thinking: "Thinking",
  speaking: "Speaking",
};

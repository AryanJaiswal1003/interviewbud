import { convertToExcalidrawElements, Excalidraw } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { forwardRef, useImperativeHandle, useRef } from "react";
import { useTheme } from "../lib/theme";

type Node = { id: string; label: string; shape?: "rectangle" | "ellipse" | "diamond" };
type Edge = { from: string; to: string; label?: string };
export type WhiteboardHandle = { draw: (spec: { title?: string; nodes: Node[]; edges: Edge[] }) => void };

/** Text description of the board so the interviewer can "see" it: labelled shapes and who points at whom. */
function describe(elements: readonly any[]): string {
  const live = elements.filter((e) => !e.isDeleted);
  const byId = new Map(live.map((e) => [e.id, e]));
  const labelOf = (el: any): string => {
    if (!el) return "?";
    if (el.type === "text") return el.text;
    const bound = (el.boundElements || []).find((b: any) => b.type === "text");
    const t = bound && byId.get(bound.id);
    return t?.text || `${el.type}`;
  };
  const shapes = live.filter((e) => ["rectangle", "ellipse", "diamond"].includes(e.type));
  const arrows = live.filter((e) => e.type === "arrow");
  const freeText = live.filter((e) => e.type === "text" && !e.containerId);
  const lines: string[] = [];
  if (shapes.length) lines.push("Shapes: " + shapes.map((s) => `${s.type} "${labelOf(s)}"`).join(", "));
  for (const a of arrows) {
    const from = labelOf(byId.get(a.startBinding?.elementId));
    const to = labelOf(byId.get(a.endBinding?.elementId));
    lines.push(`Arrow: ${from} -> ${to}`);
  }
  if (freeText.length) lines.push("Text: " + freeText.map((t) => `"${t.text}"`).join(", "));
  const free = live.filter((e) => e.type === "freedraw").length;
  if (free) lines.push(`${free} freehand strokes`);
  return lines.join("\n");
}

export const Whiteboard = forwardRef<WhiteboardHandle, { onChange: (summary: string, elements: any[]) => void;
  initial?: any[] }>(function Whiteboard({ onChange, initial }, ref) {
  const api = useRef<any>(null);
  const theme = useTheme();
  const last = useRef("");
  const timer = useRef<number | undefined>(undefined);
  const drawCount = useRef(0);

  useImperativeHandle(ref, () => ({
    draw(spec) {
      if (!api.current) return;
      const existing = api.current.getSceneElements();
      const offsetY = existing.length
        ? Math.max(...existing.map((e: any) => e.y + (e.height || 0))) + 80
        : 80;
      const cols = Math.min(4, Math.max(1, Math.ceil(Math.sqrt(spec.nodes.length))));
      const W = 190;
      const H = 80;
      const skeleton: any[] = [];
      if (spec.title) {
        skeleton.push({ type: "text", x: 60, y: offsetY - 50, text: spec.title, fontSize: 22, fontFamily: 2,
          strokeColor: "#272727" });
      }
      const ids = new Map<string, string>();
      const boxes = new Map<string, { x: number; y: number }>();
      spec.nodes.forEach((n, i) => {
        const id = `ai-${drawCount.current}-${n.id}`;
        const x = 60 + (i % cols) * (W + 110);
        const y = offsetY + Math.floor(i / cols) * (H + 90);
        ids.set(n.id, id);
        boxes.set(n.id, { x, y });
        skeleton.push({
          id,
          type: n.shape ?? "rectangle",
          x,
          y,
          width: W,
          height: H,
          strokeColor: "#a87500",
          backgroundColor: "#fbe6ad",
          fillStyle: "solid",
          roundness: { type: 3 },
          label: { text: n.label, fontSize: 18, fontFamily: 2 },
        });
      });
      // Route each arrow centre-to-centre, clipped to the shapes' edges, so layout never depends on auto-routing.
      const edgePoint = (b: { x: number; y: number }, dx: number, dy: number) => {
        const cx = b.x + W / 2;
        const cy = b.y + H / 2;
        const t = Math.min(dx ? (W / 2 + 6) / Math.abs(dx) : Infinity, dy ? (H / 2 + 6) / Math.abs(dy) : Infinity);
        return { x: cx + dx * t, y: cy + dy * t };
      };
      for (const e of spec.edges) {
        const a = boxes.get(e.from);
        const b = boxes.get(e.to);
        if (!a || !b || a === b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const p0 = edgePoint(a, dx, dy);
        const p1 = edgePoint(b, -dx, -dy);
        skeleton.push({
          type: "arrow",
          x: p0.x,
          y: p0.y,
          width: p1.x - p0.x,
          height: p1.y - p0.y,
          points: [[0, 0], [p1.x - p0.x, p1.y - p0.y]],
          strokeColor: "#272727",
          start: { id: ids.get(e.from) },
          end: { id: ids.get(e.to) },
          ...(e.label ? { label: { text: e.label, fontSize: 14, fontFamily: 2 } } : {}),
        });
      }
      drawCount.current += 1;
      const els = convertToExcalidrawElements(skeleton, { regenerateIds: false });
      api.current.updateScene({ elements: [...existing, ...els] });
      // The panel may have just been un-hidden: let it lay out, re-measure, then frame the sketch.
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          api.current?.refresh();
          api.current?.scrollToContent(els, { fitToContent: true, animate: true });
        }),
      );
    },
  }));

  return (
    <div className="h-full w-full">
      <Excalidraw
        theme={theme}
        excalidrawAPI={(a) => (api.current = a)}
        initialData={{ elements: initial ?? [], appState: { viewBackgroundColor: "#ffffff" } }}
        onChange={(elements) => {
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => {
            const summary = describe(elements);
            if (summary !== last.current) {
              last.current = summary;
              onChange(summary, elements.filter((e) => !e.isDeleted) as any[]);
            }
          }, 900);
        }}
        UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, export: false } }}
      />
    </div>
  );
});

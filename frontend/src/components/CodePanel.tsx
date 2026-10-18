import Editor from "@monaco-editor/react";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "../lib/theme";
import type { CodeResult, Problem } from "../lib/useInterview";

/** The dataset's text lost superscripts: "10^4" arrives as "104", "2^31" as "231". Restore them in constraints. */
function restorePowers(c: string) {
  return c
    .replace(/(^|[^\d.])10([1-9])(?!\d)/g, "$110^$2")
    .replace(/(^|[^\d.])2(31|32|63|64)(?!\d)/g, "$12^$2");
}

function Powered({ text }: { text: string }) {
  const parts = restorePowers(text).split(/(\d+\^\d+)/);
  return (
    <>
      {parts.map((p, i) => {
        const m = p.match(/^(\d+)\^(\d+)$/);
        return m ? <span key={i}>{m[1]}<sup>{m[2]}</sup></span> : <span key={i}>{p}</span>;
      })}
    </>
  );
}

const KIND_ORDER = ["sample", "injected", "hidden", "hard"];

const STATUS_STYLE: Record<string, string> = {
  pass: "text-ok",
  fail: "text-alarm",
  error: "text-alarm",
  timeout: "text-amber",
  skipped: "text-mute",
};

export function CodePanel({
  problem,
  result,
  running,
  initialCode,
  onChange,
  onRun,
  highlight,
}: {
  highlight?: { start_line: number; end_line: number; note: string; at: number } | null;
  problem: Problem | null;
  result: CodeResult | null;
  running: null | "run" | "submit";
  initialCode?: string;
  onChange: (code: string) => void;
  onRun: (code: string, mode: "run" | "submit") => void;
}) {
  const theme = useTheme();
  const [code, setCode] = useState(initialCode || "");
  const debounce = useRef<number | undefined>(undefined);
  const loadedFor = useRef<number | null>(null);
  const editor = useRef<any>(null);
  const decorations = useRef<any>(null);
  // The interviewer points at lines ("line twelve, the loop bound"): highlight them for a while and scroll there.
  useEffect(() => {
    const ed = editor.current;
    if (!highlight || !ed) return;
    decorations.current?.clear();
    decorations.current = ed.createDecorationsCollection([{
      range: { startLineNumber: highlight.start_line, startColumn: 1, endLineNumber: highlight.end_line, endColumn: 1 },
      options: { isWholeLine: true, className: "ib-pointed-line", linesDecorationsClassName: "ib-pointed-gutter",
        hoverMessage: { value: highlight.note } },
    }]);
    ed.revealLinesInCenterIfOutsideViewport(highlight.start_line, highlight.end_line);
    const t = window.setTimeout(() => decorations.current?.clear(), 20000);
    return () => window.clearTimeout(t);
  }, [highlight]);
  // Read straight from Monaco on run: state can lag a keystroke behind the click.
  const current = () => editor.current?.getValue() ?? code;

  useEffect(() => {
    if (problem && loadedFor.current !== problem.id) {
      loadedFor.current = problem.id;
      if (!code.trim()) setCode(problem.starter_code);
    }
  }, [problem]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (v: string) => {
    setCode(v);
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => onChange(v), 700);
  };

  if (!problem) {
    return (
      <div className="graph-paper flex h-full items-center justify-center p-8">
        <div className="max-w-sm text-center">
          <h3 className="font-display text-xl font-semibold">The coding exercise opens here</h3>
          <p className="mt-2 text-graphite">
            Your interviewer will bring it up after you've talked through your background and projects. Python,
            with tests you can run as you go.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(320px,38%)_1fr]">
      <article className="min-h-0 overflow-y-auto border-r border-line bg-surface px-6 py-5">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-display text-2xl leading-tight font-semibold">{problem.title}</h2>
          <span
            className={
              "shrink-0 rounded px-2 py-0.5 text-sm font-medium " +
              (problem.difficulty === "Easy"
                ? "bg-marine-soft text-marine"
                : problem.difficulty === "Hard"
                  ? "bg-coral-soft text-alarm"
                  : "bg-amber-soft text-ink")
            }
          >
            {problem.difficulty}
          </span>
        </div>
        <p className="mt-1 text-sm text-mute">{problem.tags.join(", ")}</p>
        <div className="mt-4 text-[15px] leading-relaxed whitespace-pre-wrap">{problem.statement}</div>
        {problem.examples.map((ex, i) => (
          <div key={i} className="mt-4 rounded-md border border-line bg-paper p-3 text-sm">
            <div className="font-semibold">Example {i + 1}</div>
            <div className="mt-1 font-mono text-[13px] break-words">
              <div>
                <span className="text-mute">Input </span>
                {ex.input}
              </div>
              <div>
                <span className="text-mute">Output </span>
                {ex.output}
              </div>
            </div>
            {ex.explanation && <p className="mt-1 text-graphite">{ex.explanation}</p>}
          </div>
        ))}
        {problem.constraints.length > 0 && (
          <>
            <h3 className="mt-5 font-semibold">Constraints</h3>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 font-mono text-[13px]">
              {problem.constraints.map((c, i) => (
                <li key={i}><Powered text={c} /></li>
              ))}
            </ul>
          </>
        )}
        {problem.follow_up && <p className="mt-4 text-sm text-graphite">{problem.follow_up}</p>}
        <p className="mt-5 text-sm text-mute">
          Submitting also runs {problem.hidden_count} hidden and {problem.hard_count} stress tests.
        </p>
      </article>

      <section className="flex min-h-0 flex-col">
        <div className="flex items-center justify-between gap-2 border-b border-line bg-paper-2 px-3 py-2">
          <span className="text-sm text-graphite">Python 3.12</span>
          <div className="flex gap-2">
            <button
              className="rounded-md border border-ink/20 bg-surface px-3 py-1.5 text-sm font-medium hover:bg-paper disabled:opacity-50"
              onClick={() => onRun(current(), "run")}
              disabled={!!running}
            >
              {running === "run" ? "Running…" : "Run tests"}
            </button>
            <button
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-on-primary hover:bg-primary-2 disabled:opacity-50"
              onClick={() => onRun(current(), "submit")}
              disabled={!!running}
            >
              {running === "submit" ? "Submitting…" : "Submit"}
            </button>
          </div>
        </div>
        <div className="relative min-h-0 flex-1">
          {highlight && highlight.note && (
            <div className="pointer-events-none absolute top-2 right-4 z-10 max-w-xs rounded-lg bg-amber px-3 py-1.5 text-sm font-medium text-onaccent shadow">
              Line {highlight.start_line}{highlight.end_line > highlight.start_line ? `–${highlight.end_line}` : ""}: {highlight.note}
            </div>
          )}
          <Editor
            theme={theme === "dark" ? "vs-dark" : "light"}
            language="python"
            value={code}
            onChange={(v) => update(v ?? "")}
            onMount={(ed) => (editor.current = ed)}
            options={{
              fontFamily: "JetBrains Mono",
              fontSize: 14,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              tabSize: 4,
              padding: { top: 12 },
              renderLineHighlight: "line",
            }}
          />
        </div>
        <TestResults problem={problem} result={result} />
      </section>
    </div>
  );
}

function TestResults({ problem, result }: { problem: Problem; result: CodeResult | null }) {
  const [open, setOpen] = useState<number | null>(null);
  const injected = problem.visible_tests.filter((t) => t.kind === "injected");
  return (
    <div className="max-h-[40%] min-h-[120px] overflow-y-auto border-t border-line bg-surface px-4 py-3 text-sm">
      {!result && (
        <div className="text-graphite">
          {problem.visible_tests.length} visible tests
          {injected.length > 0 && `, including ${injected.length} added by your interviewer`}. Run them whenever
          you like.
        </div>
      )}
      {result?.summary.compile_error && (
        <pre className="font-mono text-[13px] whitespace-pre-wrap text-alarm">{result.summary.compile_error}</pre>
      )}
      {result?.summary.killed && <div className="text-alarm">{result.summary.killed}</div>}
      {result && !result.summary.compile_error && (
        <>
          <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1">
            {Object.entries(result.summary.by_kind)
              .sort(([a], [b]) => KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b))
              .map(([k, v]) => (
              <span key={k}>
                <span className="text-mute">{k === "injected" ? "added" : k} </span>
                <span className={v.passed === v.total ? "font-semibold text-ok" : "font-semibold text-alarm"}>
                  {v.passed}/{v.total}
                </span>
              </span>
            ))}
            <span className="text-mute">slowest {result.summary.max_ms.toFixed(1)} ms</span>
          </div>
          <ul className="space-y-1">
            {result.visible.map((r) => (
              <li key={r.id}>
                <button
                  className="flex w-full items-center gap-3 text-left"
                  onClick={() => setOpen(open === r.id ? null : r.id)}
                  aria-expanded={open === r.id}
                >
                  <span className={"w-16 shrink-0 font-semibold " + STATUS_STYLE[r.status]}>{r.status}</span>
                  <span className="truncate font-mono text-[13px]">{r.input}</span>
                </button>
                {open === r.id && (
                  <div className="mt-1 ml-[76px] space-y-0.5 font-mono text-[13px]">
                    {r.rationale && <div className="font-sans text-graphite">Why: {r.rationale}</div>}
                    <div>
                      <span className="text-mute">expected </span>
                      {r.expected}
                    </div>
                    {r.actual !== null && (
                      <div>
                        <span className="text-mute">got </span>
                        {r.actual}
                      </div>
                    )}
                    {r.error && <div className="text-alarm">{r.error}</div>}
                    {r.stdout && <pre className="whitespace-pre-wrap text-graphite">{r.stdout}</pre>}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {result.hidden.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1" aria-label="Hidden test outcomes">
              {result.hidden.map((h) => (
                <span
                  key={h.id}
                  title={`${h.kind}: ${h.status}`}
                  className={
                    "h-2.5 w-2.5 rounded-sm " +
                    (h.status === "pass" ? "bg-ok" : h.status === "timeout" ? "bg-amber" : h.status === "skipped"
                      ? "bg-line" : "bg-coral")
                  }
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

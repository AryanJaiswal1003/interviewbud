import { AlertTriangle, Check, ExternalLink, FileText, Lock, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { track } from "../lib/analytics";
import { api, fmtDate } from "../lib/api";

const BUSY = new Set(["reading", "filling", "submitting"]);
const GROUPS: { kind: string; title: string; sub: string }[] = [
  { kind: "contact", title: "Your details", sub: "From your application details." },
  { kind: "file", title: "Documents", sub: "Your uploaded resume." },
  { kind: "question", title: "Questions", sub: "Answered from what you said on your screening call and what you've confirmed." },
  { kind: "consent", title: "Agreements", sub: "Only you can agree to these." },
  { kind: "voluntary", title: "Voluntary self-identification", sub: "We never answer these for you. Leave them blank or choose an answer yourself." },
  { kind: "sensitive", title: "Not handled by InterviewBud", sub: "Fill these in on the site yourself if the form needs them." },
];
const SOURCE: Record<string, string> = {
  details: "Your details", resume: "Your resume", screening: "Screening call", fact: "Your profile", draft: "Your draft",
};

/** Apply on the candidate's behalf: read the form -> review every value and its source -> approve -> fill and
 *  see a screenshot (nothing sent) -> send only on an explicit click. */
export function ApplyStep({ d }: { d: any }) {
  const company = d.opportunity.extraction?.company || "the company";
  const [app, setApp] = useState<any>(undefined);
  const [details, setDetails] = useState<any>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [sure, setSure] = useState(false);
  const [shotAt, setShotAt] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    const a = await api<any>(`/api/prep/${d.id}/application`);
    setApp(a);
    if (a && BUSY.has(a.status)) timer.current = window.setTimeout(load, 2500);
    else setShotAt(Date.now());
    return a;
  }, [d.id]);
  useEffect(() => {
    load();
    api("/api/profile/application").then(setDetails).catch(() => {});
    return () => window.clearTimeout(timer.current);
  }, [load]);

  const act = async (path: string, body?: object, event?: string) => {
    setErr(null);
    try {
      const a = await api<any>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
      setApp(a);
      if (event) track(event);
      if (BUSY.has(a.status)) timer.current = window.setTimeout(load, 2500);
      return a;
    } catch (e: any) {
      setErr(e.message);
    }
  };

  if (app === undefined) return <div className="text-graphite">Loading…</div>;
  const missingDetails = details && (!details.details.first_name || !details.details.email || !details.resume);

  if (!app) {
    return (
      <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
        <section className="rounded-2xl border border-line bg-surface p-6">
          <h2 className="font-display text-xl font-semibold">Apply to {company}</h2>
          <p className="mt-1 text-[15px] text-graphite">We open the real application form, fill it from what you've told us, and show you exactly what goes in before anything is sent.</p>
          <ol className="mt-5 space-y-3">
            {[["Read the form", "Every field, as the employer will see it."],
              ["Review", "Each answer shows where it came from. Change anything."],
              ["Preview", "We fill the real form and show you a screenshot. Nothing is sent."],
              ["Send", "Only when you press Send, after you've seen the preview."]].map(([t, s], i) => (
              <li key={t} className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-paper-2 text-xs font-bold">{i + 1}</span>
                <div><div className="font-semibold">{t}</div><div className="text-sm text-graphite">{s}</div></div>
              </li>
            ))}
          </ol>
          <button className="mt-6 rounded-lg bg-primary px-5 py-2.5 font-semibold text-on-primary hover:bg-primary-2"
            onClick={() => act(`/api/prep/${d.id}/application`, {}, "application_read")}>Read the application form</button>
          {err && <p className="mt-2 text-sm text-alarm">{err}</p>}
        </section>
        <DetailsNotice details={details} missing={!!missingDetails} />
      </div>
    );
  }

  if (app.status === "reading") return <Status title="Opening the application form…" sub="Reading every field and its options in a real browser. Under a minute." />;

  const fields: any[] = app.fields;
  const values = app.values || {};
  const val = (k: string) => edits[k] ?? values[k]?.value ?? "";
  const dirty = Object.entries(edits).some(([k, v]) => v !== (values[k]?.value ?? ""));
  const approved = !!app.approved && !dirty;
  const filled = app.status === "filled" && approved && app.filled_at && app.approved_at && app.filled_at >= app.approved_at;
  const fillFailures = Object.entries(app.fill_fields || {}).filter(([, v]) => v !== "filled");
  const label = (k: string) => fields.find((f) => f.key === k)?.label ?? k;
  const done = app.status === "submitted";

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-line bg-surface p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-display text-xl font-semibold">Your application to {company}</h2>
            <p className="mt-0.5 text-sm text-graphite">
              {fields.length} fields on the <a className="underline" href={app.apply_url} target="_blank" rel="noreferrer noopener">real form <ExternalLink size={12} className="inline" /></a>
              {" "}· {Object.values(values).filter((v: any) => v.value).length} filled in for you
            </p>
          </div>
          <Progress app={app} approved={approved} filled={!!filled} />
        </div>
        {app.error && <p className="mt-3 flex items-center gap-2 text-sm text-alarm"><AlertTriangle size={15} /> {app.error}</p>}
      </section>

      {done ? (
        <section className="rounded-2xl border border-marine/40 bg-marine-soft/40 p-6">
          <h2 className="flex items-center gap-2 font-display text-xl font-semibold"><Check size={20} /> Application sent</h2>
          <p className="mt-1">{company} confirmed it: “{app.confirmation}”. Sent {app.submitted_at && fmtDate(app.submitted_at)}.</p>
          <Shot app={app} name="submitted" at={shotAt} />
        </section>
      ) : (
        <>
          {missingDetails && <DetailsNotice details={details} missing />}
          {GROUPS.map((g) => {
            const fs = fields.filter((f) => f.kind === g.kind);
            if (!fs.length) return null;
            return (
              <section key={g.kind} className="rounded-2xl border border-line bg-surface p-5">
                <h3 className="font-semibold">{g.title}</h3>
                <p className="text-sm text-graphite">{g.sub}</p>
                <ul className="mt-4 divide-y divide-line">
                  {fs.map((f) => (
                    <FieldRow key={f.key} f={f} v={values[f.key] || {}} value={val(f.key)} edited={edits[f.key] !== undefined && edits[f.key] !== (values[f.key]?.value ?? "")}
                      onChange={(x) => setEdits({ ...edits, [f.key]: x })} />
                  ))}
                </ul>
              </section>
            );
          })}

          <section className="sticky bottom-3 z-10 rounded-2xl border border-line bg-surface p-4 shadow-lg">
            {app.missing_required?.length > 0 && !dirty && (
              <p className="mb-3 text-sm text-alarm">Still required: {app.missing_required.slice(0, 6).join("; ")}</p>
            )}
            <div className="flex flex-wrap items-center gap-3">
              {!approved ? (
                <button className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 font-semibold text-on-primary hover:bg-primary-2"
                  onClick={async () => { const a = await act(`/api/applications/${app.id}/approve`, { values: Object.fromEntries(fields.map((f) => [f.key, val(f.key)])) }, "application_approved"); if (a) setEdits({}); }}>
                  <ShieldCheck size={17} /> Approve these answers
                </button>
              ) : !filled ? (
                <button disabled={BUSY.has(app.status)} className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 font-semibold text-on-primary hover:bg-primary-2 disabled:opacity-50"
                  onClick={() => act(`/api/applications/${app.id}/fill`, {}, "application_previewed")}>
                  <FileText size={17} /> {app.status === "filling" ? "Filling the form…" : "Preview the filled form"}
                </button>
              ) : null}
              <span className="text-sm text-graphite">
                {!approved ? "Approve to continue. Nothing is filled or sent yet." : !filled ? "We fill the real form and show you a screenshot. Nothing is sent." : "Check the preview below before sending."}
              </span>
              <button className="ml-auto inline-flex items-center gap-1.5 text-sm text-graphite underline" disabled={BUSY.has(app.status)}
                onClick={async () => { setEdits({}); await act(`/api/applications/${app.id}/remap`); }}>
                <RefreshCw size={13} /> Fill answers again from my latest info
              </button>
            </div>
            {err && <p className="mt-2 text-sm text-alarm">{err}</p>}
          </section>

          {app.screenshot && !dirty && (
            <section className="rounded-2xl border border-line bg-surface p-5">
              <h3 className="font-semibold">The form, filled in</h3>
              <p className="text-sm text-graphite">A screenshot of the real form with your approved answers. It hasn't been sent.</p>
              {fillFailures.length > 0 && (
                <p className="mt-2 text-sm text-alarm">Couldn't fill: {fillFailures.map(([k, v]) => `${label(k)} (${v})`).join("; ")}. Change the answer, or finish on the site yourself.</p>
              )}
              <Shot app={app} name={app.screenshot} at={shotAt} />
            </section>
          )}

          {filled && fillFailures.length === 0 && (
            <section className="rounded-2xl border border-coral/50 bg-surface p-5">
              <h3 className="font-display text-lg font-semibold">Send your application</h3>
              {app.missing_required?.length ? (
                <p className="mt-1 text-sm text-alarm">Answer the required questions first.</p>
              ) : (
                <>
                  <label className="mt-3 flex gap-3 text-[15px]">
                    <input type="checkbox" className="mt-1 accent-amber" checked={sure} onChange={(e) => setSure(e.target.checked)} />
                    <span>I've checked the filled form above. Send this application to {company} for {d.opportunity.extraction?.title} as me.</span>
                  </label>
                  <button disabled={!sure || app.status === "submitting"}
                    className="mt-4 inline-flex items-center gap-2 rounded-lg bg-coral px-5 py-2.5 font-semibold text-white hover:brightness-105 disabled:opacity-40"
                    onClick={() => act(`/api/applications/${app.id}/submit`, { confirm: true }, "application_sent")}>
                    <Send size={16} /> {app.status === "submitting" ? "Sending…" : `Send to ${company}`}
                  </button>
                  <p className="mt-2 text-sm text-mute">If the site asks for a human check, we stop and hand it to you; we never try to get past one.</p>
                </>
              )}
            </section>
          )}
          {app.status === "needs_you" && (
            <section className="rounded-2xl border border-amber/60 bg-amber-soft/40 p-5">
              <h3 className="font-semibold">Over to you for this bit</h3>
              <p className="mt-1">{app.confirmation || app.error}. <a className="font-semibold underline" href={app.apply_url} target="_blank" rel="noreferrer noopener">Open the form</a> and use your approved answers above.</p>
            </section>
          )}
        </>
      )}
      <details className="rounded-2xl border border-line bg-surface p-5 text-sm">
        <summary className="cursor-pointer font-semibold">What happened, step by step</summary>
        <ol className="mt-3 space-y-1">
          {app.history.map((h: any, i: number) => <li key={i} className="text-graphite"><span className="tabular-nums text-mute">{new Date(h.at).toLocaleString()}</span> · {h.event.replace(/_/g, " ")}{h.detail && `: ${h.detail}`}</li>)}
        </ol>
      </details>
    </div>
  );
}

function Progress({ app, approved, filled }: { app: any; approved: boolean; filled: boolean }) {
  const steps = ["Review", "Approved", "Previewed", "Sent"];
  const at = app.status === "submitted" ? 4 : filled ? 3 : approved ? 2 : 1;
  return (
    <ol className="flex items-center gap-1.5 text-xs" aria-label="Application progress">
      {steps.map((s, i) => (
        <li key={s} className={"rounded-full px-2.5 py-1 " + (i < at - 1 || at === 4 ? "bg-marine-soft text-marine-ink" : i === at - 1 ? "bg-amber-soft font-semibold" : "bg-paper-2 text-mute")}>{s}</li>
      ))}
    </ol>
  );
}

function FieldRow({ f, v, value, edited, onChange }: { f: any; v: any; value: string; edited: boolean; onChange: (x: string) => void }) {
  const id = `fld-${f.key}`;
  const locked = f.kind === "sensitive" || f.type === "file";
  const input = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-[15px]";
  let control;
  if (f.kind === "sensitive") control = <div className="flex items-center gap-2 text-sm text-mute"><Lock size={14} /> Left empty</div>;
  else if (f.type === "file") control = <div className="flex items-center gap-2 text-[15px]"><FileText size={15} /> {value || <span className="text-mute">No file</span>}</div>;
  else if (f.kind === "consent" || f.type === "checkbox") control = (
    <label className="flex items-center gap-2 text-[15px]"><input id={id} type="checkbox" className="accent-amber" checked={value === "Yes"} onChange={(e) => onChange(e.target.checked ? "Yes" : "")} /> {value === "Yes" ? "Ticked" : "Not ticked"}</label>);
  else if (f.options?.length) control = (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={input}>
      <option value="">{f.kind === "voluntary" ? "Leave blank" : "Choose…"}</option>
      {f.options.map((o: string) => <option key={o} value={o}>{o}</option>)}
    </select>);
  else if (f.type === "textarea") control = <textarea id={id} rows={4} value={value} maxLength={f.maxlength || undefined} onChange={(e) => onChange(e.target.value)} className={input} />;
  else control = <input id={id} value={value} onChange={(e) => onChange(e.target.value)} className={input} placeholder={f.search ? "Type it as you'd search for it" : ""} />;
  const src = edited ? "You changed this" : v.state === "you" ? "You entered this" : v.source ? SOURCE[v.source] ?? v.source : "";
  return (
    <li className="grid gap-2 py-3.5 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div>
        <label htmlFor={locked ? undefined : id} className="text-[15px] font-medium">{f.label}</label>
        {f.required && <span className="ml-1.5 text-xs text-alarm">required</span>}
      </div>
      <div>
        {control}
        <div className="mt-1 text-sm">
          {src && <span className="mr-1.5 rounded-full bg-paper-2 px-2 py-0.5 text-xs">{src}</span>}
          {!edited && v.detail && <span className={v.state === "missing" ? "text-alarm" : "text-graphite"}>{v.detail}</span>}
        </div>
      </div>
    </li>
  );
}

function Shot({ app, name, at }: { app: any; name: string; at: number }) {
  return (
    <div className="mt-4 max-h-[720px] overflow-y-auto rounded-xl border border-line bg-white">
      <img src={`/api/applications/${app.id}/screenshot/${name}?t=${at}`} alt="Screenshot of the application form filled in" className="w-full" />
    </div>
  );
}

function Status({ title, sub }: { title: string; sub: string }) {
  return <section className="rounded-2xl border border-line bg-surface p-6"><h2 className="font-display text-lg font-semibold">{title}</h2><p className="mt-1 text-graphite">{sub}</p></section>;
}

function DetailsNotice({ details, missing }: { details: any; missing: boolean }) {
  return (
    <aside className={"self-start rounded-2xl border p-5 " + (missing ? "border-amber/60 bg-amber-soft/30" : "border-line bg-surface")}>
      <h3 className="font-semibold">Your application details</h3>
      <p className="mt-1 text-sm text-graphite">Name, email, phone, links and your resume file: the basics every form asks for.</p>
      {details && (
        <ul className="mt-3 space-y-1 text-sm">
          <li>{details.details.first_name ? `${details.details.first_name} ${details.details.last_name}` : <span className="text-alarm">No name yet</span>}</li>
          <li>{details.details.email || <span className="text-alarm">No email yet</span>}</li>
          <li>{details.resume ? `Resume: ${details.resume.name}` : <span className="text-alarm">No resume file yet</span>}</li>
        </ul>
      )}
      <Link to="/profile#application" className="mt-3 inline-block rounded-lg border border-line px-3 py-1.5 text-sm font-medium">Edit application details</Link>
    </aside>
  );
}

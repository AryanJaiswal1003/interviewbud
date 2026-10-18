import { Check, Lock } from "lucide-react";
import { AccountStart } from "../components/AccountStart";

/** The public front page. Its centrepiece is the product's own moment: a few lines of a screening call, and the
 *  application form filling itself in from what the candidate just said. */
export default function Landing() {
  return (
    <div className="min-h-full bg-paper text-ink">
      <header className="mx-auto flex max-w-6xl items-center px-5 py-5 md:px-8">
        <span className="flex items-center gap-2.5 font-display text-lg font-bold tracking-tight">
          <Mark /> InterviewBud
        </span>
        <a href="#start" className="ml-auto rounded-full border border-line px-4 py-1.5 text-[15px] font-medium hover:border-ink/40">Start</a>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-5 pt-8 pb-20 md:px-8 lg:grid-cols-[1.05fr_1fr] lg:pt-14 lg:pb-28">
          <div>
            <h1 className="font-display text-[clamp(2.75rem,6.4vw,4.9rem)] leading-[0.98] font-semibold tracking-[-0.035em]">
              Practise for the job you actually want.
            </h1>
            <p className="mt-6 max-w-[34rem] text-[19px] leading-relaxed text-graphite">
              Paste a job link and talk for ten minutes about yourself. You get a practice interview built for that
              role, feedback that quotes what you said, and the application filled in from your own words. It's
              sent only when you say so.
            </p>
            <div id="start" className="mt-9 max-w-[30rem] scroll-mt-24">
              <AccountStart tone="hero" />
            </div>
          </div>
          <CallToForm />
        </section>

        <section className="border-y border-line bg-surface">
          <div className="mx-auto max-w-6xl px-5 py-20 md:px-8">
            <h2 className="max-w-xl font-display text-[clamp(1.9rem,3.4vw,2.6rem)] leading-tight font-semibold tracking-tight">
              From a job link to a sent application, in five steps
            </h2>
            <ol className="relative mt-12 grid gap-10 md:grid-cols-5 md:gap-6">
              <span aria-hidden className="absolute top-[18px] right-[10%] left-[2%] hidden h-px bg-line md:block" />
              {STEPS.map(([title, body], i) => (
                <li key={title} className="relative">
                  <span className={"relative flex h-9 w-9 items-center justify-center rounded-full font-display text-[15px] font-bold " +
                    (i === 4 ? "bg-coral text-white" : i === 1 ? "bg-amber text-onaccent" : "bg-ink text-paper")}>{i + 1}</span>
                  <h3 className="mt-4 font-display text-lg font-semibold">{title}</h3>
                  <p className="mt-1.5 text-[15px] leading-relaxed text-graphite">{body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="mx-auto grid max-w-6xl gap-14 px-5 py-20 md:px-8 lg:grid-cols-[1.2fr_1fr]">
          <div>
            <h2 className="font-display text-[clamp(1.9rem,3.4vw,2.6rem)] leading-tight font-semibold tracking-tight">Feedback that quotes you</h2>
            <p className="mt-3 max-w-lg text-[17px] text-graphite">Not “work on your communication”. The exact moment, what you said, and a better way to say it.</p>
            <figure className="mt-8 rounded-2xl border border-line bg-surface p-6">
              <p className="text-sm text-mute">Turn 14 · project deep-dive</p>
              <blockquote className="mt-2 text-[17px] leading-relaxed">
                Asked how the sync handled conflicts, you said{" "}
                <mark className="rounded bg-marine-soft px-1 text-ink">“we just retried until it worked”</mark>.
              </blockquote>
              <figcaption className="mt-4 border-l-[3px] border-amber pl-4 text-[15px] leading-relaxed text-graphite">
                A stronger answer names the strategy and the cost: last-write-wins per field, why that was safe for
                drafts, and the one case where you'd have needed a merge.
              </figcaption>
            </figure>
          </div>
          <div>
            <h2 className="font-display text-[clamp(1.9rem,3.4vw,2.6rem)] leading-tight font-semibold tracking-tight">What it won't do</h2>
            <ul className="mt-8 space-y-5">
              {WONT.map(([title, body]) => (
                <li key={title} className="flex gap-4">
                  <span className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 border-coral text-coral" aria-hidden>
                    <span className="block h-[2px] w-2.5 rounded bg-coral" />
                  </span>
                  <div>
                    <h3 className="font-semibold">{title}</h3>
                    <p className="mt-0.5 text-[15px] leading-relaxed text-graphite">{body}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="bg-call text-white">
          <div className="mx-auto flex max-w-6xl flex-col items-start gap-6 px-5 py-16 md:flex-row md:items-center md:px-8">
            <p className="max-w-xl font-display text-[clamp(1.6rem,3vw,2.2rem)] leading-tight font-semibold">
              Your next interview is a conversation. Have it here first.
            </p>
            <a href="#start" className="rounded-xl bg-amber px-6 py-3 text-[17px] font-semibold text-onaccent hover:brightness-105 md:ml-auto">Start with your name</a>
          </div>
        </section>
      </main>
      <footer className="mx-auto flex max-w-6xl flex-wrap gap-x-6 gap-y-2 px-5 py-8 text-sm text-mute md:px-8">
        <span>InterviewBud</span>
        <span>An AI interviewer. A person reviews every result before any decision.</span>
      </footer>
    </div>
  );
}

const STEPS: [string, string][] = [
  ["Paste the job", "We read the posting and pull out what it really asks for, quoting the page so nothing is made up."],
  ["Talk for ten minutes", "A friendly screening call about your background. Voice or typing, whichever you prefer."],
  ["Do the technical round", "A practice interview for that role, with real coding, a whiteboard and an interviewer who helps."],
  ["Read your feedback", "What went well and what to fix, each point tied to something you said. Plus similar open roles."],
  ["Apply, with your OK", "We fill the real form from your answers, show you a screenshot, and send it when you press Send."],
];

const WONT: [string, string][] = [
  ["Invent experience", "Every answer comes from something you said or confirmed. If we don't know, the field stays empty."],
  ["Answer for you on gender, race or disability", "Voluntary self-identification questions are always yours to answer, or skip."],
  ["Press send without you", "You see the filled form first. Nothing goes to an employer until you click Send."],
  ["Get past a CAPTCHA", "If a site asks for a human check, it stops and hands the form back to you."],
];

function Mark() {
  return (
    <span className="relative h-5 w-7" aria-hidden>
      <span className="absolute top-0 left-0 h-5 w-5 rounded-full bg-amber" />
      <span className="absolute top-0 left-2.5 h-5 w-5 rounded-full bg-marine/90" />
    </span>
  );
}

/** Hero: three lines of a call, then the form field they fill. One orchestrated sequence on load. */
function CallToForm() {
  return (
    <div className="relative mx-auto w-full max-w-[34rem] lg:mx-0" aria-label="Example: a screening call filling in an application">
      <div className="rounded-[28px] bg-call-2 p-5 text-white shadow-[0_30px_60px_-30px_rgba(0,0,0,.45)] ring-1 ring-white/10 sm:p-6">
        <div className="flex items-center gap-2 text-sm text-white/55">
          <span className="ib-live h-2 w-2 rounded-full bg-coral" /> Screening call · Backend Engineer
        </div>
        <ol className="mt-5 space-y-4 text-[16px] leading-snug">
          <Line who="Alex" tone="amber" delay={0.2}>Okay so, how long have you been doing Python, roughly?</Line>
          <Line who="You" tone="marine" delay={1.1}>About six years. Mostly backend, the last three on payments at Ledgerline.</Line>
          <Line who="Alex" tone="amber" delay={2.0}>Oh nice. And would you be up for relocating to the Bay Area?</Line>
          <Line who="You" tone="marine" delay={2.9}>Yeah, I'd move for the right team.</Line>
        </ol>
      </div>
      <div className="ib-rise relative -mt-6 ml-6 rounded-2xl border border-line bg-surface p-5 text-ink shadow-[0_24px_50px_-28px_rgba(0,0,0,.35)] sm:ml-14" style={{ animationDelay: "3.6s" }}>
        <div className="text-sm text-mute">Application · job-boards.greenhouse.io</div>
        <Field label="Years of experience with Python" value="About six years" quote="About six years" delay={4.0} />
        <Field label="Willing to relocate to the Bay Area?" value="Yes" quote="I'd move for the right team" delay={4.5} />
        <div className="mt-3 flex items-center justify-between gap-3 border-t border-line pt-3 text-[15px]">
          <span className="text-graphite">Gender</span>
          <span className="flex items-center gap-1.5 text-sm text-mute"><Lock size={13} /> Left for you</span>
        </div>
      </div>
    </div>
  );
}

function Line({ who, tone, delay, children }: { who: string; tone: "amber" | "marine"; delay: number; children: React.ReactNode }) {
  return (
    <li className="ib-rise flex gap-3" style={{ animationDelay: `${delay}s` }}>
      <span className={"mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-display text-[13px] font-bold " + (tone === "amber" ? "bg-amber text-onaccent" : "bg-marine text-white")}>{who[0]}</span>
      <span><span className={"block text-xs font-bold " + (tone === "amber" ? "text-amber" : "text-[#7cc0ee]")}>{who}</span>{children}</span>
    </li>
  );
}

function Field({ label, value, quote, delay }: { label: string; value: string; quote: string; delay: number }) {
  return (
    <div className="mt-3">
      <div className="text-[15px] text-graphite">{label}</div>
      <div className="ib-fill mt-1 flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2" style={{ animationDelay: `${delay}s` }}>
        <span className="ib-rise font-semibold" style={{ animationDelay: `${delay}s` }}>{value}</span>
        <Check size={16} className="ib-rise shrink-0 text-marine" style={{ animationDelay: `${delay + 0.3}s` }} />
      </div>
      <div className="ib-rise mt-1 text-sm text-mute italic" style={{ animationDelay: `${delay + 0.3}s` }}>From the call: “{quote}”</div>
    </div>
  );
}

import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { track } from "../lib/analytics";
import { safeNext, useAuth } from "../lib/auth";

export default function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const { user, login, signup } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [remember, setRemember] = useState(true);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (user) return <Navigate to={next} replace />;
  const isSignup = mode === "signup";

  return (
    <div className="grid min-h-full bg-paper lg:grid-cols-[1fr_1.1fr]">
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-call-2 p-10 text-white lg:flex">
        <div className="flex items-center gap-2.5 font-display text-lg font-bold tracking-tight">
          <span className="relative h-5 w-7" aria-hidden>
            <span className="absolute top-0 left-0 h-5 w-5 rounded-full bg-amber" />
            <span className="absolute top-0 left-2.5 h-5 w-5 rounded-full bg-marine mix-blend-screen" />
          </span>
          InterviewBud
        </div>
        <div className="max-w-md">
          <p className="font-display text-[34px] leading-tight font-semibold">
            Screening interviews that have actually read the resume.
          </p>
          <p className="mt-4 text-white/65">
            A live voice interviewer that digs into the candidate's own projects, runs a real coding round, and hands
            you a scorecard with the quotes behind every score.
          </p>
        </div>
        <div className="flex gap-3" aria-hidden>
          <span className="h-24 w-24 rounded-full bg-amber/90" />
          <span className="-ml-10 h-24 w-24 rounded-full bg-marine/90 mix-blend-screen" />
          <span className="-ml-10 h-24 w-24 rounded-full bg-coral/80 mix-blend-screen" />
        </div>
      </aside>

      <main className="flex items-center justify-center px-6 py-12">
        <form className="w-full max-w-sm" onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            if (isSignup) {
              await signup(email, password, name, remember);
              track("signup", { remember });
            } else {
              await login(email, password, remember);
              track("login", { remember });
            }
            nav(next, { replace: true });
          } catch (e: any) {
            setErr(e.message);
            setBusy(false);
          }
        }}>
          <h1 className="font-display text-[32px] leading-tight font-semibold">{isSignup ? "Create your account" : "Welcome back"}</h1>
          <p className="mt-1 text-graphite">
            {isSignup ? <>Already have one? <Link className="font-semibold text-ink underline" to={`/login?next=${encodeURIComponent(next)}`}>Log in</Link></>
              : <>New here? <Link className="font-semibold text-ink underline" to={`/signup?next=${encodeURIComponent(next)}`}>Create an account</Link></>}
          </p>

          <div className="mt-8 space-y-4">
            {isSignup && (
              <label className="block">
                <span className="text-sm font-semibold">Your name</span>
                <input className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5" value={name}
                  onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={120} />
              </label>
            )}
            <label className="block">
              <span className="text-sm font-semibold">Email</span>
              <input type="email" required className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5" value={email}
                onChange={(e) => setEmail(e.target.value)} autoComplete="email" autoFocus />
            </label>
            <label className="block">
              <span className="text-sm font-semibold">Password</span>
              <span className="relative mt-1.5 block">
                <input type={show ? "text" : "password"} required minLength={isSignup ? 8 : undefined} maxLength={128}
                  className="w-full rounded-lg border border-line bg-surface py-2.5 pr-11 pl-3" value={password}
                  onChange={(e) => setPassword(e.target.value)} autoComplete={isSignup ? "new-password" : "current-password"} />
                <button type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}
                  className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1.5 text-mute hover:text-ink">
                  {show ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </span>
              {isSignup && <span className="mt-1 block text-sm text-mute">At least 8 characters.</span>}
            </label>
            <label className="flex items-start gap-2.5 text-[15px]">
              <input type="checkbox" className="mt-1 accent-amber" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              <span>
                Remember this device
                <span className="block text-sm text-mute">Stay signed in for 30 days. Leave off on a shared computer.</span>
              </span>
            </label>
          </div>
          {err && <p className="mt-4 text-sm text-alarm" role="alert">{err}</p>}
          <button className="mt-6 w-full rounded-xl bg-primary px-4 py-3 font-display text-lg font-semibold text-on-primary hover:bg-primary-2 disabled:opacity-50"
            disabled={busy}>
            {busy ? (isSignup ? "Creating account…" : "Logging in…") : isSignup ? "Create account" : "Log in"}
          </button>
        </form>
      </main>
    </div>
  );
}

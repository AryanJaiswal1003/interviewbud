import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { AccountStart } from "../components/AccountStart";
import { safeNext, useAuth } from "../lib/auth";

/** The way in: pick an account saved in this browser, or give a name to start one. No email, no password. */
export default function Welcome() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  if (user) return <Navigate to={next} replace />;
  return (
    <div className="grid min-h-full bg-paper lg:grid-cols-[1fr_1.1fr]">
      <aside className="hidden flex-col justify-between bg-call-2 p-10 text-white lg:flex">
        <a href="/" className="flex items-center gap-2.5 font-display text-lg font-bold tracking-tight">
          <span className="relative h-5 w-7" aria-hidden>
            <span className="absolute top-0 left-0 h-5 w-5 rounded-full bg-amber" />
            <span className="absolute top-0 left-2.5 h-5 w-5 rounded-full bg-marine mix-blend-screen" />
          </span>
          InterviewBud
        </a>
        <p className="max-w-md font-display text-[34px] leading-tight font-semibold">Practise for the job you actually want.</p>
        <p className="text-sm text-white/50">Your account lives in this browser. Anyone using this browser can open it, so on a shared computer remove it when you're done.</p>
      </aside>
      <main className="flex items-center justify-center px-6 py-14">
        <div className="w-full max-w-md">
          <h1 className="font-display text-[32px] leading-tight font-semibold tracking-tight">Welcome</h1>
          <p className="mt-1.5 mb-8 text-graphite">Pick up where you left off, or start fresh.</p>
          <AccountStart onDone={() => nav(next, { replace: true })} />
        </div>
      </main>
    </div>
  );
}

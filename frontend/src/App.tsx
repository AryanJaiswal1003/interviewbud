import { ArrowLeftRight, Briefcase, FileText, GraduationCap, Home, LayoutList, Moon, Plus, Settings as SettingsIcon, Sun } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ConsentBanner } from "./components/ConsentBanner";
import { track } from "./lib/analytics";
import { RequireAuth, useAuth } from "./lib/auth";
import { StatusDot } from "./components/ui";
import { api } from "./lib/api";
import { setTheme, useTheme } from "./lib/theme";

const NewInterview = lazy(() => import("./pages/Home"));
const Hub = lazy(() => import("./pages/Hub"));
const Room = lazy(() => import("./pages/Room"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const InterviewDetail = lazy(() => import("./pages/InterviewDetail"));
const Learning = lazy(() => import("./pages/Learning"));
const Settings = lazy(() => import("./pages/Settings"));
const Profile = lazy(() => import("./pages/Profile"));
const Prep = lazy(() => import("./pages/Prep"));
const PrepJourney = lazy(() => import("./pages/PrepJourney"));
const Welcome = lazy(() => import("./pages/AuthPage"));
const Landing = lazy(() => import("./pages/Landing"));

/** Whether an interview could run right now, polled for the rail. */
function useHealth() {
  const [h, setH] = useState<any>(null);
  useEffect(() => {
    const load = () => api("/api/health").then(setH).catch(() => setH({ db: false }));
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);
  return h;
}

function Shell() {
  const h = useHealth();
  const { user } = useAuth();
  const link = ({ isActive }: { isActive: boolean }) =>
    "flex items-center gap-3 rounded-lg px-3 py-2 text-[15px] transition-colors " +
    (isActive ? "bg-white/10 font-semibold text-white" : "text-white/65 hover:bg-white/5 hover:text-white");
  const services: [string, boolean | null, string][] = h ? [
    ["Interviewer brain", !!h.azure_openai, h.chat_deployment ?? ""],
    ["Voice", h.tts_ready === true, h.tts_model ?? ""],
    ["Speech-to-text", h.stt_provider === "local" || h.fish_audio || h.azure_openai, h.stt_provider === "local" ? "Whisper, on device" : h.stt_provider],
    ["Code sandbox", h.sandbox === true, "Docker, no network"],
  ] : [];
  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 flex h-screen w-[232px] shrink-0 flex-col border-r border-white/5 bg-call-2 px-3 py-5 text-white max-md:hidden">
        <NavLink to="/" className="flex items-center gap-2.5 px-3 font-display text-lg font-bold tracking-tight">
          <span className="relative h-5 w-7" aria-hidden>
            <span className="absolute top-0 left-0 h-5 w-5 rounded-full bg-amber" />
            <span className="absolute top-0 left-2.5 h-5 w-5 rounded-full bg-marine mix-blend-screen" />
          </span>
          InterviewBud
        </NavLink>
        <NavLink to="/prep" end
          className="mx-1 mt-7 flex items-center justify-center gap-2 rounded-lg bg-amber px-3 py-2.5 font-semibold text-onaccent hover:brightness-105">
          <Plus size={18} /> Prepare for a job
        </NavLink>
        <nav className="mt-5 flex flex-col gap-0.5" aria-label="Main">
          <NavLink to="/" end className={link}><Home size={18} /> Home</NavLink>
          <NavLink to="/prep" className={link}><Briefcase size={18} /> My preparations</NavLink>
          <NavLink to="/new" className={link}><FileText size={18} /> Interview from a resume</NavLink>
          <NavLink to="/dashboard" className={link}><LayoutList size={18} /> Interview history</NavLink>
        </nav>
        {user?.is_admin && (
          <nav className="mt-5 flex flex-col gap-0.5" aria-label="Admin">
            <div className="px-3 pb-1 text-xs text-white/40">Admin</div>
            <NavLink to="/learning" className={link}><GraduationCap size={18} /> Interviewer learning</NavLink>
            <NavLink to="/settings" className={link}><SettingsIcon size={18} /> System and voices</NavLink>
          </nav>
        )}
        <div className="mt-auto rounded-xl bg-white/[.04] px-3 py-3">
          {user?.is_admin ? (
            <>
              <div className="text-sm font-semibold text-white/80">Services</div>
              <ul className="mt-2 space-y-1.5">
                {(services.length ? services : [["Checking…", null, ""] as [string, null, string]]).map(([name, ok, detail]) => (
                  <li key={name} className="flex items-start gap-2 text-[13px]" title={detail}>
                    <span className="pt-[5px]"><StatusDot ok={ok} /></span>
                    <span className="min-w-0">
                      <span className="block text-white/80">{name}</span>
                      {detail && <span className="block truncate text-[12px] text-white/40">{detail}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            // Members only need to know whether an interview will work right now.
            <div className="flex items-center gap-2 text-[13px] text-white/75">
              <StatusDot ok={services.length ? services.every(([, ok]) => ok) : null} />
              {!services.length ? "Checking…" : services.every(([, ok]) => ok) ? "All systems ready" : "Some features are unavailable right now"}
            </div>
          )}
        </div>
        <ThemeSwitch />
        <UserChip />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-4 bg-call px-4 py-3 text-white md:hidden">
          <NavLink to="/" className="font-display font-bold">InterviewBud</NavLink>
          <nav className="ml-auto flex gap-3 text-sm text-white/75">
            <NavLink to="/prep">Prepare</NavLink>
            <NavLink to="/dashboard">History</NavLink>
            <NavLink to="/profile">Profile</NavLink>
          </nav>
        </header>
        <main className="flex-1">
          <Suspense fallback={<div className="p-10 text-graphite">Loading…</div>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}

function UserChip() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  if (!user) return null;
  const bg = { amber: "bg-amber text-onaccent", marine: "bg-marine text-white", coral: "bg-coral text-white", grey: "bg-ink-3 text-white" }[user.accent] ?? "bg-amber text-onaccent";
  return (
    <div className="mt-3 flex items-center gap-2.5 rounded-xl px-2 py-2 hover:bg-white/5">
      <Link to="/profile" className="flex min-w-0 flex-1 items-center gap-2.5" title="Your profile">
        {user.avatar_url ? <img src={user.avatar_url} alt="" className="h-8 w-8 shrink-0 rounded-full object-cover" />
          : <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${bg}`}>{(user.display_name || "?").charAt(0).toUpperCase()}</span>}
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{user.display_name}</span>
          <span className="block truncate font-mono text-xs text-white/45">ID {user.account_id}</span>
        </span>
      </Link>
      <button aria-label="Switch account" title="Switch account" className="rounded-md p-1.5 text-white/50 hover:bg-white/10 hover:text-white"
        onClick={async () => { await logout(); nav("/welcome"); }}>
        <ArrowLeftRight size={16} />
      </button>
    </div>
  );
}

let lastView = { route: "", at: 0 };

function PageViews() {
  const loc = useLocation();
  useEffect(() => {
    // Interview ids are dropped from the path so analytics never ties a page view to a candidate.
    const route = loc.pathname.replace(/[0-9a-f-]{36}/g, ":id");
    if (lastView.route === route && Date.now() - lastView.at < 1000) return; // StrictMode runs effects twice
    lastView = { route, at: Date.now() };
    track("page_view", { route });
  }, [loc.pathname]);
  return null;
}

function ThemeSwitch() {
  const theme = useTheme();
  const { user, update } = useAuth();
  return (
    <div role="radiogroup" aria-label="Theme" className="mt-3 grid grid-cols-2 gap-1 rounded-lg bg-white/[.06] p-1 text-[13px]">
      {(["dark", "light"] as const).map((t) => (
        <button key={t} role="radio" aria-checked={theme === t} onClick={() => { setTheme(t); if (user) update({ preferences: { theme: t } }).catch(() => {}); }}
          className={"flex items-center justify-center gap-1.5 rounded-md py-1.5 capitalize " +
            (theme === t ? "bg-white/15 font-semibold text-white" : "text-white/55 hover:text-white")}>
          {t === "dark" ? <Moon size={14} /> : <Sun size={14} />} {t}
        </button>
      ))}
    </div>
  );
}

/** Signed-out visitors see the landing page at "/", and are sent to the welcome page from anywhere else. */
function Gate() {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="flex min-h-full items-center justify-center bg-paper text-graphite">Loading…</div>;
  if (!user && loc.pathname === "/") return <Suspense fallback={null}><Landing /></Suspense>;
  return <RequireAuth><Shell /></RequireAuth>;
}

export default function App() {
  return (
    <>
    <PageViews />
    <ConsentBanner />
    <Routes>
      <Route path="/welcome" element={<Suspense fallback={null}><Welcome /></Suspense>} />
      {/* Old links */}
      <Route path="/login" element={<Navigate to="/welcome" replace />} />
      <Route path="/signup" element={<Navigate to="/welcome" replace />} />
      <Route path="/interview/:id" element={<RequireAuth><Suspense fallback={null}><Room /></Suspense></RequireAuth>} />
      <Route element={<Gate />}>
        <Route path="/" element={<Hub />} />
        <Route path="/new" element={<NewInterview />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/dashboard/:id" element={<InterviewDetail />} />
        <Route path="/learning" element={<Learning />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/prep" element={<Prep />} />
        <Route path="/prep/:id" element={<PrepJourney />} />
      </Route>
    </Routes>
    </>
  );
}

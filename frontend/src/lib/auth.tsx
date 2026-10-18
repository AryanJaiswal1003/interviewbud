import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api } from "./api";
import { setTheme } from "./theme";

export type Me = {
  id: string; email: string; display_name: string; headline: string; accent: string; timezone: string;
  avatar_url: string | null; created_at: string; is_admin: boolean;
  preferences: {
    interviewer_name?: string; default_role?: string; default_seniority?: string; default_duration?: number;
    default_voice?: string; theme?: "dark" | "light"; analytics?: boolean;
  };
};

type Auth = {
  user: Me | null;
  loading: boolean;
  login: (email: string, password: string, remember: boolean) => Promise<void>;
  signup: (email: string, password: string, displayName: string, remember: boolean) => Promise<void>;
  logout: () => Promise<void>;
  update: (patch: Partial<Me> & { preferences?: Partial<Me["preferences"]> }) => Promise<Me>;
  setUser: (u: Me | null) => void;
};

const Ctx = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const setUser = useCallback((u: Me | null) => {
    setUserState(u);
    if (u?.preferences?.theme) setTheme(u.preferences.theme);
  }, []);

  useEffect(() => {
    api<Me>("/api/auth/me").then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
    // Any API call that comes back 401 (session expired or revoked elsewhere) signs this tab out.
    const out = () => setUserState(null);
    window.addEventListener("ib:unauthorized", out);
    return () => window.removeEventListener("ib:unauthorized", out);
  }, [setUser]);

  const value: Auth = {
    user,
    loading,
    setUser,
    login: async (email, password, remember) =>
      setUser(await api<Me>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password, remember }) })),
    signup: async (email, password, display_name, remember) =>
      setUser(await api<Me>("/api/auth/signup", {
        method: "POST", body: JSON.stringify({ email, password, display_name, remember }),
      })),
    logout: async () => {
      await api("/api/auth/logout", { method: "POST" }).catch(() => {});
      setUserState(null);
    },
    update: async (patch) => {
      const u = await api<Me>("/api/auth/me", { method: "PATCH", body: JSON.stringify(patch) });
      setUser(u);
      return u;
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): Auth {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

/** Sends signed-out visitors to the login page, then back to where they were going. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="flex min-h-full items-center justify-center bg-paper text-graphite">Loading…</div>;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <>{children}</>;
}

/** Short-lived token for CopilotKit agent runs (the runtime calls our agents without the browser's cookie). */
export function useAgentToken(): string | null {
  const [token, setToken] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () => api<{ token: string }>("/api/auth/agent-token").then((r) => !stop && setToken(r.token)).catch(() => {});
    load();
    const t = setInterval(load, 6 * 3600 * 1000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);
  return token;
}

/** Only same-site relative paths are allowed as post-login destinations (no open redirects). */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api } from "./api";
import { setTheme } from "./theme";

export type Me = {
  id: string; account_id: string; display_name: string; headline: string; accent: string; timezone: string;
  avatar_url: string | null; created_at: string; is_admin: boolean;
  preferences: {
    interviewer_name?: string; default_role?: string; default_seniority?: string; default_duration?: number;
    default_voice?: string; theme?: "dark" | "light"; analytics?: boolean;
  };
};

/** An account remembered in this browser. The key is what lets the browser use it again; it never leaves this
 *  device except to open the account. */
export type SavedAccount = { id: string; account_id: string; name: string; key: string; last_used: number };

const STORE = "ib-accounts";

export function savedAccounts(): SavedAccount[] {
  try {
    const xs = JSON.parse(localStorage.getItem(STORE) || "[]");
    return Array.isArray(xs) ? xs.filter((x) => x && x.id && x.key).sort((a, b) => b.last_used - a.last_used) : [];
  } catch {
    return [];
  }
}

function saveAccount(a: Omit<SavedAccount, "last_used">) {
  try {
    const rest = savedAccounts().filter((x) => x.id !== a.id);
    localStorage.setItem(STORE, JSON.stringify([{ ...a, last_used: Date.now() }, ...rest]));
  } catch {
    /* storage blocked: this visit still works, the account just won't be offered next time */
  }
}

export function forgetAccount(id: string) {
  try {
    localStorage.setItem(STORE, JSON.stringify(savedAccounts().filter((x) => x.id !== id)));
  } catch {
    /* nothing saved */
  }
}

type Auth = {
  user: Me | null;
  loading: boolean;
  accounts: SavedAccount[];
  start: (name: string) => Promise<void>;
  choose: (a: SavedAccount) => Promise<void>;
  forget: (id: string) => void;
  logout: () => Promise<void>;
  update: (patch: Partial<Me> & { preferences?: Partial<Me["preferences"]> }) => Promise<Me>;
  setUser: (u: Me | null) => void;
};

const Ctx = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [accounts, setAccounts] = useState<SavedAccount[]>(savedAccounts);
  const refresh = () => setAccounts(savedAccounts());

  const setUser = useCallback((u: Me | null) => {
    setUserState(u);
    if (u?.preferences?.theme) setTheme(u.preferences.theme);
  }, []);

  useEffect(() => {
    api<Me>("/api/auth/me").then(async (u) => {
      setUser(u);
      // Signed in from before accounts lived in the browser: keep a key for this account here too.
      if (!savedAccounts().some((a) => a.id === u.id)) {
        const k = await api<{ account_key: string }>("/api/auth/account-key", { method: "POST" }).catch(() => null);
        if (k) { saveAccount({ id: u.id, account_id: u.account_id, name: u.display_name, key: k.account_key }); refresh(); }
      }
    }).catch(() => setUser(null)).finally(() => setLoading(false));
    // Any API call that comes back 401 (session expired or revoked elsewhere) signs this tab out.
    const out = () => setUserState(null);
    window.addEventListener("ib:unauthorized", out);
    return () => window.removeEventListener("ib:unauthorized", out);
  }, [setUser]);

  const value: Auth = {
    user,
    loading,
    accounts,
    setUser,
    start: async (name) => {
      const r = await api<Me & { account_key: string }>("/api/auth/start", { method: "POST", body: JSON.stringify({ name }) });
      saveAccount({ id: r.id, account_id: r.account_id, name: r.display_name, key: r.account_key });
      refresh();
      const { account_key: _key, ...me } = r;
      void _key;
      setUser(me);
    },
    choose: async (a) => {
      try {
        const me = await api<Me>("/api/auth/switch", { method: "POST", body: JSON.stringify({ account_key: a.key }) });
        saveAccount({ ...a, name: me.display_name, account_id: me.account_id });
        refresh();
        setUser(me);
      } catch (e: any) {
        if (e.status === 404) { forgetAccount(a.id); refresh(); }
        throw e;
      }
    },
    forget: (id) => { forgetAccount(id); refresh(); },
    logout: async () => {
      await api("/api/auth/logout", { method: "POST" }).catch(() => {});
      setUserState(null);
    },
    update: async (patch) => {
      const u = await api<Me>("/api/auth/me", { method: "PATCH", body: JSON.stringify(patch) });
      setUser(u);
      if (patch.display_name) { const a = savedAccounts().find((x) => x.id === u.id); if (a) { saveAccount({ ...a, name: u.display_name }); refresh(); } }
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

/** Sends visitors without an account in use to the welcome page (name or account picker), then back. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="flex min-h-full items-center justify-center bg-paper text-graphite">Loading…</div>;
  if (!user) return <Navigate to={`/welcome?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
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

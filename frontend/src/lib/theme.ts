import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light";
const KEY = "ib-theme";

function read(): Theme {
  try {
    return localStorage.getItem(KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

/** Applies the theme to <html>: data-theme drives our tokens, the .dark class drives CopilotKit's styles. */
export function applyTheme(t: Theme) {
  const el = document.documentElement;
  el.dataset.theme = t;
  el.classList.toggle("dark", t === "dark");
}

let current: Theme = read();
const listeners = new Set<() => void>();

export function setTheme(t: Theme) {
  current = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* private mode: still switch for this visit */
  }
  applyTheme(t);
  listeners.forEach((l) => l());
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
  );
}

/** Resolved value of a colour token, for libraries that need literal colours (charts). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--color-${name}`).trim();
}

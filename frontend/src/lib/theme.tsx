/* Theme provider: light/dark/system mode + accent presets, persisted to
 * localStorage. The inline script in index.html applies the stored theme
 * before first paint; this provider takes over after hydration. */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

export type ThemeMode = "light" | "dark" | "system";
export type Accent = "violet" | "cyan" | "emerald" | "rose";

const THEME_KEY = "tagforge.theme";
const ACCENT_KEY = "tagforge.accent";

export const ACCENTS: { id: Accent; label: string; swatch: string }[] = [
  { id: "violet", label: "Violet", swatch: "hsl(265 75% 58%)" },
  { id: "cyan", label: "Cyan", swatch: "hsl(192 88% 44%)" },
  { id: "emerald", label: "Emerald", swatch: "hsl(157 75% 38%)" },
  { id: "rose", label: "Rose", swatch: "hsl(350 78% 50%)" },
];

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Mutates <html> synchronously; returns the resolved theme.
 *  Must run before React re-renders so getComputedStyle reads (charts)
 *  see the updated variables in the same render pass. */
function applyDom(mode: ThemeMode, accent: Accent): "light" | "dark" {
  const dark = mode === "dark" || (mode === "system" && systemPrefersDark());
  const el = document.documentElement;
  el.classList.toggle("dark", dark);
  if (accent === "violet") el.removeAttribute("data-accent");
  else el.setAttribute("data-accent", accent);
  return dark ? "dark" : "light";
}

interface ThemeCtx {
  mode: ThemeMode;
  accent: Accent;
  resolvedTheme: "light" | "dark";
  setMode: (m: ThemeMode) => void;
  setAccent: (a: Accent) => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

function readStored<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  try {
    const v = localStorage.getItem(key);
    return allowed.includes(v as T) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() =>
    readStored(THEME_KEY, ["light", "dark", "system"], "system"),
  );
  const [accent, setAccentState] = useState<Accent>(() =>
    readStored(ACCENT_KEY, ["violet", "cyan", "emerald", "rose"], "violet"),
  );
  const [resolvedTheme, setResolved] = useState<"light" | "dark">(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );

  const setMode = useCallback(
    (m: ThemeMode) => {
      try {
        localStorage.setItem(THEME_KEY, m);
      } catch {
        /* private mode */
      }
      setResolved(applyDom(m, accent));
      setModeState(m);
    },
    [accent],
  );

  const setAccent = useCallback(
    (a: Accent) => {
      try {
        localStorage.setItem(ACCENT_KEY, a);
      } catch {
        /* private mode */
      }
      applyDom(mode, a);
      setAccentState(a);
    },
    [mode],
  );

  // Follow OS preference while in "system" mode.
  useEffect(() => {
    if (mode !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolved(applyDom("system", accent));
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode, accent]);

  // Reconcile once on mount (inline script and React state should agree).
  useEffect(() => {
    setResolved(applyDom(mode, accent));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Ctx.Provider value={{ mode, accent, resolvedTheme, setMode, setAccent }}>
      {children}
    </Ctx.Provider>
  );
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}

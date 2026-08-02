import { useMemo } from "react";

import { useTheme } from "@/lib/theme";

/**
 * Resolves theme CSS variables into concrete color strings for Recharts.
 * SVG presentation attributes don't reliably support var() in all browsers,
 * so we read computed values. ThemeProvider mutates <html> synchronously in
 * its setters, so this memo (keyed on resolvedTheme + accent) always reads
 * fresh values.
 */
export function useChartColors() {
  const { resolvedTheme, accent } = useTheme();
  return useMemo(() => {
    const css = getComputedStyle(document.documentElement);
    const c = (name: string) => `hsl(${css.getPropertyValue(name).trim()})`;
    return {
      grid: c("--line"),
      tick: c("--text-muted"),
      tooltipBg: c("--bg-panel"),
      tooltipBorder: c("--line"),
      bar: c("--brand"),
      // Hover band behind bars — Recharts defaults to hardcoded #ccc.
      cursor: `hsl(${css.getPropertyValue("--line").trim()} / 0.4)`,
    };
  }, [resolvedTheme, accent]);
}

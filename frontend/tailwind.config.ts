import type { Config } from "tailwindcss";

/**
 * All color tokens resolve through CSS variables defined in src/styles.css.
 * Variables hold space-separated HSL channels (e.g. "265 80% 65%") so the
 * `hsl(var(--x) / <alpha-value>)` form keeps every opacity modifier in the
 * codebase (bg-brand/15, ring-brand/40, bg-bg-panel/80, ...) working.
 */
const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        bg: {
          DEFAULT: "hsl(var(--bg) / <alpha-value>)",
          panel: "hsl(var(--bg-panel) / <alpha-value>)",
          subtle: "hsl(var(--bg-subtle) / <alpha-value>)",
          hover: "hsl(var(--bg-hover) / <alpha-value>)",
        },
        line: {
          DEFAULT: "hsl(var(--line) / <alpha-value>)",
          strong: "hsl(var(--line-strong) / <alpha-value>)",
        },
        text: {
          DEFAULT: "hsl(var(--text) / <alpha-value>)",
          muted: "hsl(var(--text-muted) / <alpha-value>)",
          subtle: "hsl(var(--text-subtle) / <alpha-value>)",
        },
        brand: {
          DEFAULT: "hsl(var(--brand) / <alpha-value>)",
          fg: "hsl(var(--brand-fg) / <alpha-value>)",
          subtle: "hsl(var(--brand-subtle) / <alpha-value>)",
        },
        accent: {
          green: "hsl(var(--accent-green) / <alpha-value>)",
          amber: "hsl(var(--accent-amber) / <alpha-value>)",
          rose: "hsl(var(--accent-rose) / <alpha-value>)",
          cyan: "hsl(var(--accent-cyan) / <alpha-value>)",
        },
      },
      fontFamily: {
        // Inter / JetBrains Mono are used when the OS has them; neither is
        // bundled, so the system stacks after them do the real work.
        sans: ["Inter", "ui-sans-serif", "system-ui"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
        // Bundled (see main.tsx) — the wordmark must not silently fall back.
        display: ['"Archivo Variable"', "Archivo", "ui-sans-serif", "system-ui"],
      },
      boxShadow: {
        // Full shadow value lives in the variable — box-shadow has no
        // <alpha-value> support, so light/dark swap the whole thing.
        panel: "var(--shadow-panel)",
      },
    },
  },
  plugins: [],
};

export default config;

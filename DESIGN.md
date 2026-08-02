# Design

Tag Forge visual system. Product register: design serves the tool — dense
tables, quiet chrome, one accent. See PRODUCT.md for strategy.

## Theme architecture

All colors flow through CSS variables holding **space-separated HSL channels**
(`--brand: 265 80% 65%`), consumed by Tailwind as
`hsl(var(--x) / <alpha-value>)` so opacity modifiers (`bg-brand/15`,
`ring-brand/40`) work. Defined in `frontend/src/styles.css` `@layer base`;
mapped in `frontend/tailwind.config.ts`.

Cascade order is load-bearing (specificity ties):
`:root` (light) → `[data-accent=…]` (light accents) → `.dark` → `.dark[data-accent=…]`.

Mode + accent are applied to `<html>` (`.dark` class, `data-accent` attribute)
by `frontend/src/lib/theme.tsx` (`ThemeProvider`/`useTheme`), persisted in
localStorage (`tagforge.theme`, `tagforge.accent`), pre-applied before first
paint by an inline script in `frontend/index.html`. Modes: light / dark /
system. Accents: violet (default), cyan, emerald, rose.

## Color tokens

| Token | Utility | Dark (identity — do not change) | Light |
|---|---|---|---|
| `--bg` | `bg-bg` | 220 18% 7% | 220 20% 97% |
| `--bg-panel` | `bg-bg-panel` | 220 16% 10% | 0 0% 100% |
| `--bg-subtle` | `bg-bg-subtle` | 220 14% 13% | 220 16% 94% |
| `--bg-hover` | `bg-bg-hover` | 220 13% 17% | 220 14% 90% |
| `--line` / `--line-strong` | `border-line[-strong]` | 220 13% 18% / 26% | 220 13% 87% / 76% |
| `--text` | `text-text` | 220 14% 92% | 224 30% 12% |
| `--text-muted` | `text-text-muted` | 220 8% 60% | 220 9% 38% |
| `--text-subtle` | `text-text-subtle` | 220 6% 45% | 220 7% 48% |
| `--brand` (violet) | `bg/text-brand` | 265 80% 65% | 265 70% 48% |
| `--brand-fg` | `text-brand-fg` | white | white |
| `--accent-green/amber/rose/cyan` | `text-accent-*` | saturated | darkened for ≥4.6:1 on white |

Rules:
- **Never** raw Tailwind palette text colors (`text-green-400`) in pages —
  they break light mode. Use `text-accent-*` semantic tokens. Where a
  bucket-specific hue is needed, pair `text-*-700 dark:text-*-400`.
- On brand backgrounds use `text-brand-fg`, never `text-white` (cyan/emerald
  dark accents use a dark foreground).
- Charts read resolved colors via `frontend/src/lib/useChartColors.ts`
  (getComputedStyle bridge — SVG attrs don't reliably support var()).

## Typography

Inter (UI) + JetBrains Mono (data: tag names, counts, paths). Fixed rem
scale; page title `text-xl font-semibold`, panel titles `text-sm
font-semibold`, table/data text `text-xs`, hints `text-[10-11px]`. Numeric
table cells: right-aligned + `tabular-nums`.

## Components (`pf-*` classes + shared modules)

- `styles.css`: `pf-panel`, `pf-input`, `pf-btn[-primary|-ghost]`, `pf-pill`,
  `pf-section-title`, `pf-stat`, `pf-label`, `pf-divider`, `pf-bucket` (shape only).
- `components/Panel.tsx`: `Panel` (card w/ header+actions), `Stat`.
- `components/forms.tsx`: `Field`, `Checkbox`, `SegmentedControl`, `RATINGS`,
  `ratingPillClass`, `RatingFilter` (g/s/q/e pills), `ConfirmButton`
  (3s arm-then-confirm for destructive actions), `CopyButton`.
- `lib/buckets.ts`: **single source of truth** for bucket colors
  (`BUCKET_STYLES` — literal class strings only, Tailwind scanner can't see
  composed names) and bucket lists (`ASSIGN_BUCKETS`, `EXPORT_BUCKETS`).
- `components/GlobalJobTray.tsx` + `lib/jobStore.ts`: background jobs render
  once, bottom-left, surviving navigation (`tagforge.activeJobs`).
- `components/BucketBadge.tsx`: bucket chip via `bucketBadgeClass`.

## Interaction conventions

- Free-text filters debounce 250ms (`lib/useDebouncedValue.ts`) +
  `placeholderData: keepPreviousData` — no table blanking per keystroke.
- Query failures toast globally (QueryCache onError in `main.tsx`) — never
  render confidently-wrong zeros/empties.
- Destructive/mass ops go through `ConfirmButton`, quoting real counts.
- Rapid-triage actions (Tag Review assign) get an Undo toast action.
- Motion: 150-250ms transitions, state-conveying only; `animate-spin` for
  pending, `animate-pulse` for skeletons. No page-load choreography.

## Backdrop system (dark-mode depth)

Layer stack bottom-up: aurora (`body::after`, 3 accent-derived blurred
radials drifting on a 55s transform loop) → grain (`body::before`) →
dot-grid (`.pf-dots`, top 45vh, dark only) → content. All accent-aware via
`--aurora-*`/`--dots-opacity` vars; static under reduced motion.
**No `backdrop-filter` on panels** — the animating backdrop would force
per-frame re-blurs (continuous GPU cost); panels use 80%-alpha fills.
Extras: `.pf-panel` cursor spotlight (`--spot-x/y`, dark only),
`CountUp.tsx` (stat entrances), `ClickSpark.tsx` (Roll button feedback),
custom select chevrons + hidden number spinners (`--select-chevron`).

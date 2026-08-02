# Product

## Register

product

## Users

One power user (the developer/owner) running the app locally on a Windows desktop, usually in the evening, iterating fast: ingesting hundreds of thousands of AI-image prompt records, triaging tag classifications, watching trends, and exporting wildcard files for Kohaku-NAI image generation. Sessions are long, data-dense, and keyboard/mouse mixed. No mobile use, no second user.

## Product Purpose

Tag Forge mines prompt metadata from local NovelAI files and Danbooru, classifies tags into semantic buckets (outfit, pose, expression, background, character, …) via a three-stage pipeline (rules → embeddings → LLM), and forges the results into wildcard .txt files and trend reports. Success = the user can go from raw metadata to high-quality, deduplicated wildcard files with minimal manual triage.

## Brand Personality

Technical, calm, precise. A dimly-lit workshop tool: violet-branded dark UI, monospace data, quiet chrome that stays out of the way of dense tables. Light mode should feel like the same instrument in daylight — crisp paper-white panels, never washed out.

## Anti-references

- Generic SaaS dashboard gloss (hero metrics, gradient cards, marketing chrome).
- Consumer-app playfulness; no mascots, no illustration-heavy empty states.
- Do NOT alter the established dark palette — it is the identity. Light mode adapts to it, not the reverse.
- No component-library restyle (no shadcn/ui import, no MUI); the hand-rolled `pf-*` system is the design system.

## Design Principles

1. **Density is a feature** — tables and numbers are the product; chrome earns its pixels or goes.
2. **Trust the screen** — never render confidently-wrong data (fake zeros while loading, silent query failures).
3. **One source of truth per visual concept** — bucket colors, field wrappers, rating pills defined once, used everywhere.
4. **Long jobs are first-class** — multi-minute ingest/classify jobs must survive navigation and stay visible.
5. **Destructive is deliberate** — anything irreversible (deny-list delete, mass relabels) confirms with real numbers.

## Accessibility & Inclusion

Desktop-only, single known user. Keep pragmatic AA: ≥4.5:1 body text contrast in both themes, visible focus rings (already present), `prefers-reduced-motion` respected for any added motion, keyboard affordances (Enter submit / Esc close) where they speed up the power-user loop. No screen-reader deep pass.

# Tag Forge

A standalone local app that mines AI-image prompt metadata — your NovelAI
`metadata.txt` dump and live Danbooru / AIBooru scrapes — classifies every tag
into semantic buckets with a three-stage pipeline, and forges the results into
**per-image-grouped wildcard files** that drop straight into
`Kohaku-NAI/client_extensions/kohaku-nai-wildcards/wildcards/`.

```
metadata.txt (NAI/SD prompt dump)                     \
Danbooru order:rank / order:score / popular / search   ->  parser -> 3-stage classifier -> SQLite -> React UI -> wildcard .txt
AIBooru (same Danbooru codebase)                      /
```

Everything runs on your machine: SQLite database, local dev servers, no
telemetry. The only outbound traffic is the booru APIs you ask it to fetch
and the LLM provider you optionally enable for Stage 3.

## Stack

| layer      | choice                                                                  |
| ---------- | ----------------------------------------------------------------------- |
| backend    | FastAPI + SQLModel (SQLite, WAL) + httpx + sse-starlette                |
| frontend   | Vite + React 18 + TypeScript + Tailwind (CSS-variable theme) + TanStack Query/Table/Virtual + Recharts |
| scraping   | rate-limited `httpx` against `posts.json` / `explore/posts/popular.json` |
| classifier | Stage 1: `KohakuBlueleaf/danbooru-tag-tree` + `tags.jsonl` rules · Stage 2: sentence-transformers embeddings (GPU) · Stage 3: LLM (OpenAI / Anthropic), live-concurrent or OpenAI Batch API |

> **Tab-by-tab walkthrough + export workflow:** see [`USAGE.md`](./USAGE.md).

## Quick start

**Requires Python 3.10+ and Node 18+.** Windows is the developed-on
platform (the launchers are `.bat`/`.ps1`); on Linux/macOS run the same
steps manually — create a venv, `pip install -r backend/requirements.txt`,
`python -m backend.cli init-db`, `python -m backend.cli seed-tag-tree`,
then `python -m backend.cli serve` plus `npm --prefix frontend run dev`.
The Decompose tab's "open in explorer" action is Windows-only.

```bat
:: from the repo root, double-click or run in cmd:
scripts\dev.bat
```

PowerShell users: `scripts\dev.ps1`. The launcher creates `.venv`, installs
Python + node deps, runs `init-db`, downloads `tag_tree.json` if missing, and
opens one window per server:

- FastAPI on `http://127.0.0.1:9301`
- Vite dev on `http://localhost:9300` (proxies `/api` to the backend)

**Daily use:** `scripts\run.bat` — single window. Rebuilds the frontend only
when sources changed, then serves the SPA + API from uvicorn alone at
`http://127.0.0.1:9301` (no Vite dev server).

For Stage 3 (LLM classification), copy `.env.example` → `.env` and set
`OPENAI_API_KEY` (and/or `ANTHROPIC_API_KEY`).

### Ports

Defaults are 9300/9301. To change: `frontend/vite.config.ts` (`server.port`,
`proxy.target`), `backend/settings.py` (`API_PORT`), and the uvicorn line in
`scripts/dev.bat`. On Windows, `WinError 10013` usually means the port sits in
a Hyper-V/WSL2 excluded range — check with
`netsh int ipv4 show excludedportrange protocol=tcp`.

## Core model: buckets and scene lines

Every canonical tag gets a **bucket**: `outfit`, `pose`, `expression`,
`background`, `composition`, `accessory`, `extras`, `character`, `artist`,
`quality_meta`, or `other` (= unclassified). For every image the app keeps
**one row per `(image, bucket)`** in `scene_line`, with that image's tags for
the bucket rejoined into a single comma-separated line:

| bucket        | example line                                           |
| ------------- | ------------------------------------------------------ |
| `outfit`      | `white shirt, open shirt, microskirt, thighhighs`      |
| `pose`        | `lying on bed, on back, arms behind back`              |
| `expression`  | `light smile, half-closed eyes`                        |
| `background`  | `bedroom, indoors, dakimakura`                         |
| `composition` | `full body, low angle, depth of field`                 |
| `accessory`   | `glasses, hairpin, hair ornament`                      |
| `extras`      | `holding cup of tea, teacup, plate, cake`              |
| `character`   | `hatsune miku, rem (re:zero)`                          |
| `scene`       | outfit + pose + expression + background concatenated   |

Export writes those rows as `outfit.txt`, `pose.txt`, … — each line is a
complete, coherent slice of a real curated image, matching the Kohaku-NAI
wildcard format directly.

## Ingest

### Local metadata (`Ingest → Import metadata.txt`)

Streaming parser for the NovelAI/SD prompt dump. Notable handling:

- **NAI V4 metadata**: prefers `actual_prompts.prompt.base_caption` (the
  resolved prompt) over the raw prompt field.
- **NAI dynamic prompts** (`|| red skirt | blue skirt ||`): the resolved pick
  is kept *and* every pipe option is added as a tag, so wildcard variety
  isn't lost.
- **Prompt cleaning**: strips LoRA refs, `{{ }}`/`[[ ]]` emphasis,
  `1.5::tag::` weights.
- **Per-image rating inference** (`g/s/q/e`) from the curated dict in
  `backend/ingest/tag_ratings.py` (`rating_source = "inferred"`).

### Booru fetch (`Ingest → Fetch from Booru`)

`POST /api/danbooru/fetch` with modes:

- `popular` — `/explore/posts/popular.json?date=…&scale=day|week|month`
- `rank` — `posts.json?tags=order:rank+…` (numbered pages; cursor pagination
  500s on non-default sort orders)
- `score` — `posts.json?tags=order:score+score:>N+…`
- `tag_search` — arbitrary tag query
- `trending` — recent-vs-baseline window diff

Anonymous access works for all modes (the UI live-counts the 2-paid-tag anon
budget and warns when login + API key would be needed). Rate limit defaults
to 5 req/s; `User-Agent` is always sent.

### Duplicate prevention (three independent layers)

| layer | mechanism |
| ----- | --------- |
| source | `Source.dedup_key` — SHA1 of the absolute path (local) or site+canonical params (booru); re-runs reuse the row |
| image  | local: upsert on `(source_id, external_id=filename-stem)` — tags/scene lines rebuilt in place, never duplicated. Booru: **global per-site dedup** — a post id is stored once no matter which query fetched it |
| export | line-level dedup at write time |

Re-fetching already-present booru posts **backfills** missing
`external_created_at` (the Danbooru post date) and refreshes `score` /
`fav_count` without touching tags or scene lines. Saved **fetch presets**
(preset picker in the card header) snapshot every fetch parameter *except*
credentials.

## Classification pipeline

Three stages, run from `Tags → Smart classify`, all resumable and audited:

1. **Stage 1 — rules** (free, instant): `danbooru-tag-tree` taxonomy +
   `tags.jsonl` categories + heuristics (`*_boy/_girl`, franchise suffixes,
   `(cosplay)`-style qualifiers). Artist/character/meta tags are deterministic.
2. **Stage 2 — embeddings** (free, GPU): sentence-transformers nearest-neighbour
   against seed labels (default `mxbai-embed-large-v1`), threshold-gated, with
   a reset knob for low-confidence relabels.
3. **Stage 3 — LLM** (paid): batches of ~50 residual `other` tags per request
   against `gpt-4o-mini` (or Anthropic, or `echo` for dry-runs).
   - **Concurrent dispatch**: N parallel requests (default 6, max 12) via a
     thread pool; per-batch progress streams into the job tray. Config errors
     (missing key, bad model) fail the job loudly instead of silently skipping.
   - **OpenAI Batch API mode**: submits the whole run as an async batch at
     −50% cost (results ≤24h). Batches are tracked in an `llm_batch` table
     with status polling and a one-click **Apply results** step.
   - **Response cache** (`data/tag_classification_cache.json`): every verdict
     is cached by canonical tag name, so re-runs only pay for uncached tags.
     The queue panel shows exactly how many API calls a run will cost before
     you start it.

Supporting machinery:

- **Audit log**: every relabel (embed/LLM/manual) writes a
  `tag_classification_history` row with before/after state; the Relabel
  history panel supports filtered browsing and one-click revert.
- **Locking**: manual bucket assignments set `locked=True` + a
  `tag_bucket_override` row, so automated passes never overwrite curation.
  The Tag Review tab's Undo genuinely releases a tag back to the
  unclassified pool (`lock=false` API mode) instead of pinning it.
- **Tag Review tab**: frequency-sorted `other` tags with one-click bucket
  assignment — the fast manual triage loop for high-usage residuals.

## Analysis & assembly

- **Trends** (`/api/trends/delta`): per-tag frequency delta between a recent
  window and a baseline. Uses `COALESCE(external_created_at, created_at)` so
  booru rows compare by real post date. Extras: character bucket splits
  multi-character scene lines so each character counts individually; compare
  modes (`previous period` / `1 week earlier` / `1 month earlier` / custom
  offset); per-source filter; inline bucket re-assignment from the table;
  top-N `.txt` export.
- **Builder**: roll random per-bucket combinations into a prompt skeleton,
  lock the buckets you like, reroll single buckets, copy to clipboard.
- **Scenes**: filterable, paginated browser over every ingested image with a
  per-bucket breakdown side panel and copy buttons for raw prompts and
  bucket lines.

## Export

`Export` writes per-bucket wildcard files + a JSON manifest. Knobs:

- source/origin selection (`local` vs `booru`), score floor, NAI model filter
- **scene rating filter** (drop whole scenes) vs **max rating per line**
  (strip only the over-cap tokens, keep the rest of the scene)
- min tags per line, dedup toggle, file prefix
- **named deny lists** (stored in DB, reusable across exports) plus ad-hoc
  paste-in deny tags
- **export presets** — snapshot/restore the whole filter configuration

## Jobs, persistence, and safety

- Long operations (ingest, fetch, classify, export-apply) run as background
  jobs streamed over SSE. The **global job tray** (bottom-left) survives page
  navigation and reloads (`localStorage`), and a finished job invalidates the
  UI caches once.
- **DB backups**: `Settings → Maintenance` snapshots the live SQLite file via
  the WAL-safe `sqlite3` backup API into `backend/data/backups/`, with list
  and delete management.
- Query failures surface as toasts (once per outage, not per poll); tables
  never render confidently-wrong zeros while loading.

## UI / theming

CSS-variable design system (`frontend/src/styles.css` + `tailwind.config.ts`):
light / dark / system modes, four accent presets (violet default), persisted
in `localStorage` and applied pre-paint (no theme flash). Dark mode carries
the visual identity: drifting aurora backdrop + film grain + dot grid, cursor
spotlight on panels, count-up stats — all accent-aware and disabled under
`prefers-reduced-motion`. Design tokens and conventions are documented in
[`DESIGN.md`](./DESIGN.md); product context in [`PRODUCT.md`](./PRODUCT.md).

## HTTP API surface

All routes under `/api` (see `backend/app.py` for registration):

| prefix | highlights |
| ------ | ---------- |
| `/dashboard` | summary counts, coverage, recent jobs |
| `/ingest` | metadata preview/start, defaults |
| `/danbooru` | fetch (5 modes), tag-budget estimate |
| `/scenes` | list/detail, `/sources` |
| `/tags` | list, buckets, `POST /{name}/bucket` (with `lock` flag), history list/stats/revert/backfill |
| `/classify` | queue stats, stage1/stage2/stage3 (+`concurrency`, `use_batch_api`), `llm-batches` list/apply, rebuild-scenes, ratings |
| `/trends` | `delta` (+`compare_offset_days`, `source_id`, `booru_only`), plain-text `export` |
| `/builder` | roll |
| `/export` | run, preset-dirs, default-deny-tags, deny-list CRUD |
| `/presets` | generic named form-snapshot CRUD (`kind=fetch\|export`) |
| `/admin` | `backup` (POST), `backups` (GET/DELETE) |
| `/jobs` | list, get, SSE `/{id}/stream` |

## CLI

`python -m backend.cli` mirrors the core flows for headless use:

```
init-db                                       create / migrate sqlite schema
reset-db --yes                                drop + recreate everything (destructive)
seed-tag-tree                                 download tag_tree.json from github
preview-metadata <path> --sample 10           parse first N records & dump JSON
ingest-metadata  <path>                       full ingest into SQLite
fetch-booru --mode popular|rank|score ...     headless fetch (Task Scheduler friendly);
                                                --date-min/--date-max = per-day backfill,
                                                --classify-after = chain GPT classify
classify-ratings                              backfill Image.rating from tag dict
export <name> --out <dir> --origin local      write wildcard files
serve --host 127.0.0.1 --port 9301            run the API server
```

## Repository layout

```
backend/
  app.py                  FastAPI entry (routers + SPA static mount)
  cli.py                  typer CLI
  settings.py             paths + env config (.env auto-load)
  db.py                   engine, session_scope, lightweight column migrations
  jobs.py                 job store + SSE pub/sub
  models.py               Source, Image, Tag, ImageTag, SceneLine, Job,
                          TagBucketOverride, TagClassificationHistory,
                          DenyList, Preset, LlmBatch, ExportSet
  routes/                 dashboard, ingest, scenes, tags, danbooru, export,
                          trends, builder, classify, jobs, presets, admin
  ingest/
    metadata_parser.py    streaming metadata.txt parser (+ NAI actual_prompts)
    prompt_cleaner.py     weight/lora stripping + || pipe || expansion
    tag_categorizer.py    stage-1 classifier
    stage2_embed.py       embedding classifier
    stage3_llm.py         LLM classifier (concurrent + Batch API) + cache
    tag_ratings.py        curated s/q/e dict + infer_image_rating()
    tag_history.py        audit-log writer
    danbooru_client.py    rate-limited client, 5 modes
    exporter.py           wildcard writer + manifest
    runner.py             background runners (dedup lives here)
  data/                   tag_tree.json, tagforge.db, backups/,
                          tag_classification_cache.json
frontend/
  src/pages/              Dashboard, Ingest, Scenes, Tags, Trends, Builder,
                          Export, Settings
  src/components/         Layout, Panel, forms (Field/RatingFilter/Confirm…),
                          PresetPicker, GlobalJobTray, JobProgress,
                          BucketBadge, CountUp, ClickSpark, TagForgeMark
  src/lib/                api.ts (typed client), theme.tsx, buckets.ts,
                          jobStore.ts, useChartColors, useDebouncedValue
scripts/
  dev.ps1 / dev.bat       one-shot launcher (both servers)
PRODUCT.md / DESIGN.md    product + design-system context
USAGE.md                  full tab-by-tab walkthrough
```

## Environment variables

Set in `.env` (auto-loaded, gitignored) or the shell. See `.env.example`
for a commented template.

| var | purpose |
| --- | ------- |
| `OPENAI_API_KEY` | Stage 3 default provider + Batch API |
| `ANTHROPIC_API_KEY` | Stage 3 alternative provider |
| `TAGFORGE_HOST` / `TAGFORGE_PORT` | backend bind (default 127.0.0.1:9301) |
| `TAGFORGE_ALLOWED_HOSTS` | accepted `Host` headers, comma-separated (default `127.0.0.1,localhost`). DNS-rebinding defense — if you bind `0.0.0.0` for LAN access, add the IP/name you browse to; `*` disables the check |
| `TAGFORGE_DB_PATH` | SQLite file (default `backend/data/tagforge.db`) |
| `TAGFORGE_DEV_ORIGIN` | CORS origin of the Vite dev server (default `http://localhost:9300`) |
| `TAGFORGE_REQ_PER_SECOND` | booru rate limit (default 5) |
| `TAGFORGE_USER_AGENT` | booru User-Agent override |
| `TAGFORGE_BOORU_PROXY` | optional SOCKS/HTTP proxy for booru fetches (probed, falls back to direct; default direct) |
| `TAGFORGE_EXPORTS_DIR` | wildcard output root (default `./exports`) |
| `TAGFORGE_METADATA_FILE` | UI hint: default metadata.txt path on the Ingest page |
| `TAGFORGE_LLAMA_SWAP_URL` | local LLM server for the NAI splitter (default `http://127.0.0.1:8080`) |
| `TAGFORGE_LLAMA_SWAP_BAT` / `TAGFORGE_LLAMA_SWAP_CONFIG` | optional: enable the in-app "start server" button and idle-TTL knob |
| `TAGFORGE_SEETHROUGH_PYTHON` / `_DIR` / `_LAYERDIFF` / `_DEPTH` | Decompose tab: python env + repo checkouts |
| `TAGFORGE_KOHAKU_TAGS_JSONL` / `_WILDCARDS_DIR` / `_COMMON_PROMPTS_DIR` | optional sibling Kohaku-NAI checkout paths |

## Versioning

Semantic versioning; see [`CHANGELOG.md`](./CHANGELOG.md). The version is
declared in exactly two files — `frontend/package.json` and
`backend/pyproject.toml` — and read from there by the sidebar footer,
Settings → About, and `GET /api/health`. Bump both and add a changelog
entry in the same commit as the change.

## Licensing

MIT — see `LICENSE`. Vendored third-party components and the
downloaded-at-setup tag taxonomy are documented in
`THIRD_PARTY_LICENSES.md`.

The logomark is adapted from
[*forge* by Monjin Friends](https://thenounproject.com/icon/forge-1044767/)
(Noun Project), used under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The tag taxonomy is fetched by
`python -m backend.cli seed-tag-tree` on first setup (it is not
redistributed with this repository).

# Tag Forge — Usage Guide

A practical walkthrough of every tab in the app, plus the CLI, the export
workflow, and the things that confuse people on the first run.

> First time? Read [Getting started](#getting-started) and
> [The mental model](#the-mental-model) sections. Skim the rest.

---

## Contents

1. [Getting started](#getting-started)
2. [The mental model](#the-mental-model) — why tags get dropped
3. [Buckets reference](#buckets-reference) — incl. the `extras` bucket
4. [Origin: local vs booru](#origin-local-vs-booru)
5. [Scene rating classifier](#scene-rating-classifier)
6. [Dashboard tab](#dashboard-tab)
7. [Ingest tab](#ingest-tab)
   - [Import `metadata.txt`](#import-metadatatxt)
   - [Fetch from Booru](#fetch-from-booru)
8. [Scenes tab](#scenes-tab)
9. [Tags tab](#tags-tab) — manual overrides + Smart Classify
10. [Trends tab](#trends-tab)
11. [Builder tab](#builder-tab)
12. [Export tab](#export-tab) — produce wildcard files
13. [Headless CLI](#headless-cli)
14. [Recommended end-to-end workflow](#recommended-end-to-end-workflow)
15. [Troubleshooting](#troubleshooting)

---

## Getting started

```bat
scripts\dev.bat
```

Opens two cmd windows:

- **Tag Forge backend** → `http://127.0.0.1:9301` (FastAPI + SQLite)
- **Tag Forge frontend** → `http://localhost:9300` (Vite dev server)

Open <http://localhost:9300> in any browser. On first run the script:

1. Creates `.venv`, installs Python deps from `backend/requirements.txt`
2. Installs `frontend/node_modules`
3. Creates the SQLite schema in `backend/data/tagforge.db`
4. Downloads `KohakuBlueleaf/danbooru-tag-tree/tag_tree.json` into
   `backend/data/`

To stop: `Ctrl-C` (or close) each cmd window. Re-running `dev.bat` auto-kills
any leftover server holding 9300 / 9301 so you can safely re-launch any time.

### Picking a different port

If 9300 / 9301 ever collide with something else, edit three files:

- `frontend/vite.config.ts` (`server.port` + `proxy.target`)
- `backend/settings.py` (`API_PORT`)
- `scripts/dev.bat` (`BACKEND_PORT` / `FRONTEND_PORT` at the top)

Then close both windows and re-run `dev.bat`.

---

## The mental model

Tag Forge is built around **per-image-grouped wildcard files**. Every
prompt you ingest becomes:

```
image #123
├── outfit       : white shirt, microskirt, thighhighs
├── pose         : on back, arms behind back
├── expression   : light smile, half-closed eyes
├── background   : bedroom, dakimakura
├── composition  : full body, low angle
├── accessory    : glasses, hairpin
├── extras       : holding cup of tea, teacup, plate, cake
└── scene        : outfit + pose + expression + background concatenated
```

Each of those rows becomes **one line** in the corresponding `.txt` file
during export — `outfit.txt`, `pose.txt`, etc. So `outfit.txt` is a list of
coherent outfit combinations, each one pulled from a real curated image.

### Why so many tags go into `other`

A large prompt dump contains a lot of things that don't belong in any of the
seven wildcard buckets:

- **Character / body**: `1girl`, `1boy`, `solo`, `mature_female`, `huge_breasts`, `voluptuous`, `bare_shoulders`, `collarbone`, `thighs`, `navel`, `cleavage` … (you control these via your character prompt, not a wildcard)
- **Hair / eye color**: `red_hair`, `blue_eyes`, `low_ponytail`, `bangs`, `ahoge` … (also character-level)
- **Quality / boilerplate**: `masterpiece`, `best_quality`, `absurdres`, `volumetric_lighting`, `perfect_face` … (you handle these in your base prompt)
- **Artist tags**: `artist:elocca`, `artist:inhoso` … (you handle these in your base prompt)
- **Pure body part nouns**: `nose`, `lips`, `fingers`, `arm` …

These all get classified as `other` (or `artist` / `character` /
`quality_meta`) and **never appear in any exported wildcard file**. That's by
design — the wildcard system handles scene variation; you write the
character + base + quality block by hand once.

The Ingest preview shows a per-bucket breakdown plus a `dropped` count so you
can sanity-check what's being kept. Around 60-80% of tokens in a typical
prompt go to `other`. That is correct and expected.

---

## Buckets reference

Every classified tag lands in exactly one of the buckets below. Only the rows
marked **exported** appear in wildcard `.txt` files.

| bucket         | exported | what it captures                                                                |
| -------------- | -------- | ------------------------------------------------------------------------------- |
| `outfit`       | yes      | clothing, shoes, hats, legwear, dresses, swimsuits, lingerie                    |
| `pose`         | yes      | posture verbs, action gerunds (`sitting`, `holding hands`, `walking`)           |
| `expression`   | yes      | face / eyes / mouth tags (`smile`, `blush`, `tongue out`)                       |
| `background`   | yes      | locations, sky, weather, plants, time of day                                    |
| `composition`  | yes      | image composition + focus tags (`upper body`, `from above`, `cowboy shot`)      |
| `accessory`    | yes      | eyewear, hair accessories, piercings                                            |
| `extras`       | yes      | props, objects, food, drink, animals, vehicles, instruments, weapons            |
| `character`    | yes      | **NEW** — specific characters from `tags.jsonl` (e.g. `hatsune_miku_(vocaloid)`) — exportable so you can write `character.txt` from trending Danbooru fetches |
| `scene`        | yes      | aggregate of outfit + pose + expression + background per image (intentionally **excludes** character so you can supply your own) |
| `artist`       | no       | `artist:*` and Danbooru artist-category tags                                    |
| `quality_meta` | no       | `masterpiece`, `best quality`, `absurdres`, year/version tags                   |
| `other`        | no       | residuals — anything not in the tag tree at all (Stage-2/3 can drain this)      |

The `extras` bucket is the one to watch on a fresh ingest. It captures useful
"stuff in the scene" that previously went into `other` — for example
`holding_cup_of_tea`, `teacup`, `cake`, `katana`, `motorcycle`. It overlaps
slightly with `pose` for verb-like tags (`holding`) because the categorizer
routes verbs into Posture / Verbs first. After your first ingest you can
browse `extras.txt` and tell me whether you want it folded into `pose` or kept
separate.

`other` is reserved for tags that aren't in the Danbooru tag tree at all and
shouldn't pollute your wildcards — it's only visible in the Tags page so you
can review residuals and override them.

---

## Origin: local vs booru

Every `Source` row carries a `kind` that classifies it as either:

- **local** — `metadata_file` ingests of your own scraped `metadata.txt`
- **booru** — `danbooru` / `aibooru` / `safebooru` fetches from the Ingest
  tab's "Fetch from Booru" card

Three places let you filter by origin:

- **Scenes** — `Origin` toggle (`all | local | booru`) at the top of the
  Filters panel. Combines with the Source dropdown (which now also auto-filters
  to whichever origin is selected).
- **Builder** — same toggle, restricts which scene_lines can be rolled.
- **Export** — same toggle plus the Sources picker is split into "Local
  imports" and "Booru fetches" headings, each with a one-click "select all"
  shortcut. Pick `local`, hit *select all* under Local imports → exports only
  draw from your local metadata imports. Pick `booru` → exports only draw from
  Danbooru scrapes.

You can also pass `--origin local` (or `booru`) to the `export` CLI command.

---

## Scene rating classifier

Booru posts already carry a rating from the API (`g`/`s`/`q`/`e`). Local
`metadata.txt` records have none, so Tag Forge infers one by walking each
image's tags through a curated dict (`backend/ingest/tag_ratings.py`).

The dict has three lists:

- **`s` (sensitive)** — `bikini`, `swimsuit`, `lingerie`, `cleavage`,
  `huge_breasts`, `bare_shoulders`, `panties`, `thong`, `garter_belt`,
  `sideboob`, `underboob`, `bare_midriff`, `thigh_focus`, etc.
- **`q` (questionable)** — `topless`, `bottomless`, `partially_nude`,
  `naked_apron`, `cameltoe`, `pantyshot`, `bare_butt`, `ass_focus`,
  `breast_grab`, `groping`, `motorboating`, `bondage`, `wardrobe_malfunction`,
  etc.
- **`e` (explicit)** — `nipples`, `nude`, `naked`, `pussy`, `penis`, `sex`,
  `cum`, `cum_on_*`, `bukkake`, `fellatio`, `masturbation`, `vaginal_sex`,
  `anal_sex`, `pubic_hair`, etc.

An image's rating is the **worst severity** present (`g < s < q < e`). The
evidence tags are stored in `Image.rating_evidence` so you can audit any
classification.

### Where it shows up

- **Ingest preview** — every preview row gets a colored `rating g/s/q/e` pill
  with the evidence tags in its tooltip.
- **Scenes detail panel** — pill plus a full `rating evidence` line below the
  header so you can see why a scene was tagged.
- **Backfill** — the *Smart classify* card on the Tags tab has a
  `Classify scene ratings` button (and matching `classify-ratings` CLI). Run
  it after ingesting if you've added tags to `tag_ratings.py` or if you want
  to fill in rows from an old ingest. By default it only touches rows whose
  `rating_source IS NULL`; toggle `overwrite previously inferred ratings` to
  re-process everything (`provided` Booru ratings are always preserved).

### Two different rating filters at export time

The Export tab now has **two** rating-related knobs and they do different
things:

1. **`Scene rating filter`** (comma list, e.g. `g,s`) — whole-scene filter.
   Only includes scenes whose **image rating** is in this list. A scene rated
   `e` is excluded entirely.
2. **`Max rating per line`** (`any | g | s | q | e`) — **per-tag strip mode**.
   Walks every line of every bucket, drops individual tags whose rating
   exceeds the cap, and skips the line if the surviving tag count drops below
   `Min tags / line`. So a `nude, sitting, smile, bedroom` scene line with cap
   `s` becomes `sitting, smile, bedroom`. Tags like `pussy` get redacted
   without dropping the whole scene.

Combine them: set scene rating to `s,q` (skip explicit-rated scenes
altogether) **and** cap per-line to `s` (strip anything questionable that
slipped through). Typical safe build: scene rating `g,s`, max
rating per line `s`.

---

## Dashboard tab

Top-of-page counts:

- **Images** — rows in the `image` table
- **Tags** — unique canonical tags seen across all images
- **Scene lines** — rows in `scene_line` (≈ `images × number_of_active_buckets`)
- **Sources** — distinct ingest jobs (metadata file or Booru fetch)
- **classifier_coverage** — fraction of unique tags assigned to a non-`other` bucket

Below: tag distribution per bucket, recent job log, and detected paths
(`metadata.txt`, wildcards dir, optional `tags.jsonl` from the
dataset if present).

The Dashboard auto-refreshes every 5 s, so you can leave it open in a
background tab and watch ingest progress live.

---

## Ingest tab

Two cards: import a local `metadata.txt` dump, or scrape Danbooru / AIBooru
directly.

### Import metadata.txt

1. **Metadata file path** — the path to a `metadata.txt` produced by an
   image-metadata scraper. The page prefills the `TAGFORGE_METADATA_FILE`
   env var when set.
2. **Label** — free text for your own bookkeeping (shows up on the Scenes
   filter and Builder pages). Optional; defaults to `metadata:<filename>`.
3. **Drop checkboxes**:
   - **Drop artist tags** — strip `artist:*` tokens (you author artist style
     in your base prompt). Recommended ON.
   - **Drop quality / boilerplate tags** — strip `masterpiece`, `absurdres`,
     `year 2025`, `volumetric_lighting`, etc. Recommended ON.
   - **Drop character tags** — strip tags Danbooru categorizes as character
     (e.g. `hatsune_miku_(vocaloid)`). Keep this **OFF** if you want the
     scene to remember which character the original prompt was using; turn
     it **ON** if you intend to swap your own character into every scene.
4. **Preview first 20** — opens a card showing each record's:
   - filename, software (`NovelAI` / SD / …), NAI model
   - per-bucket tag breakdown (outfit / pose / expression / background / composition / accessory / scene)
   - how many tokens were `dropped` (i.e. went to `other`)
   - a collapsible "raw tokens" view of every token the parser found

   Use the preview to sanity-check the parser + classifier on a few records
   before committing to a full ingest. If a particular tag obviously belongs
   in a bucket but the preview shows it as dropped, you can fix that
   manually from the Tags tab (after ingest).
5. **Start ingest** — kicks off a background job that streams progress over
   SSE. The bottom of the card shows a live progress bar with throughput
   and ETA. Re-ingesting the same file is **idempotent** — existing
   `(source_id, external_id)` rows are reused, their `scene_line` rows are
   rebuilt.

Tens of thousands of images take roughly 5-15 minutes on a modern SSD.

### Fetch from Booru

Pull tag-only metadata from Danbooru or AIBooru. Five modes:

| Mode         | What it does                                                     |
| ------------ | ---------------------------------------------------------------- |
| `popular`    | Hits `/explore/posts/popular.json?date=…&scale=day\|week\|month` |
| `rank`       | `posts.json?tags=order:rank+rating:s+date:>=…`                   |
| `score`      | `posts.json?tags=order:score+rating:s+date:…`                    |
| `tag_search` | Arbitrary tag query                                              |
| `trending`   | Recent-vs-baseline window diff, ratio-sorted                     |

Common controls:

- **Site**: `danbooru.donmai.us`, `aibooru.online`, or `safebooru.donmai.us`
- **Rating**: `g` general / `s` sensitive / `q` questionable / `e` explicit (or any). Multiple ratings can be selected as a comma list in the Scenes filter.
- **Score min**: numeric, ≥. Set to `0` to disable.
- **Date min/max** (rank, score, tag_search): ISO `YYYY-MM-DD`.
- **Limit / page**: 1-200, server-capped at 200.
- **Pages**: how many cursor pages to walk (200 × pages = total posts).
- **Login + API key**: optional. The client works anonymously within
  Danbooru's 2-paid-tag limit, which is enough for all four standard modes.

**Tag-budget warning**: as you type, the card live-evaluates your query and
shows whether anonymous access is sufficient:

> `0 paid · 3 free metatags — works anonymously.`

vs.

> `3 paid tags — anon limit is 2. Requires at least a Gold account…`

Free metatags are `rating:`, `date:`, `age:`, `score:`, `favcount:`,
`width:`, etc. (`order:` counts as paid).

**Start fetch** kicks off a background job; results are persisted as
`Source(kind='danbooru')` (or `'aibooru'`) and appear in the Scenes /
Builder / Export filters immediately.

---

## Scenes tab

Browse every ingested image with rich filters and a side-panel detail view.

Filters (all combine):

- **Source** — dropdown of every ingest you've ever run
- **Rating** — comma-separated list (`g,s` for general+sensitive)
- **NAI model** — exact match, e.g. `V4.5 4BDE2A90`
- **Score min** — ≥
- **Search in prompt** — substring match on the raw prompt
- **Has outfit / Has background** — only show images that produced a line in
  that bucket (handy when filling holes)

Click any row → side panel with:

- Per-bucket tag groupings (these are the exact lines that will go to the
  wildcard files on export)
- The raw original prompt
- Tag chips with their assigned bucket + bucket source (`tag_tree`,
  `dataset_category`, `embed`, `llm`, `manual`)

Pagination at the top of the results panel; default 100 per page.

---

## Tags tab

Inspect and curate the unique-tag table. Useful for two things:

1. **Spotting misclassifications** and overriding them manually
2. **Running Stage 2 / Stage 3 smart-classify passes**

Filter and sort controls match the Scenes tab. Each row shows:

- `name`, `bucket`, `bucket_source` (where the assignment came from),
  `confidence`, `usage` (how often this tag appears in your corpus),
  `post_count` (Danbooru's global count)

**Inline override**: click the bucket dropdown on any row to reassign it.
The change is persisted to `tag_bucket_override` so it survives any future
re-classification. The 🔒 icon means the tag is locked from automatic
reassignment.

To make the override take effect in your wildcards, run **Rebuild
scene_line only** (in the Smart Classify card below).

### Smart Classify card

Two optional passes that try to push tags out of `other` into a real bucket:

#### Stage 2 — embedding NN

Encodes every Stage-1-labelled tag and every residual tag with a
sentence-transformer, builds per-bucket centroids, and assigns each residual
to its nearest centroid if `cosine ≥ threshold`.

- **Model**: `mixedbread-ai/mxbai-embed-large-v1` is the default (1024-dim,
  trained for symmetric STS, no instruction prefixes needed). The model
  picker also suggests `Snowflake/snowflake-arctic-embed-l-v2.0` (strong
  alternative) and the older `BAAI/bge-*` line if you want a smaller
  download.
- **Threshold**: `0.55` default. Lower = more aggressive, more noise.
- **Device**: `auto` picks `cuda:0` if torch sees a GPU, otherwise CPU.
  Use the dropdown to force `cuda:1` or `cpu`. The job message in the
  Activity sidebar prints the resolved device + GPU name so you can
  confirm it isn't silently falling back to CPU.
- One-time install:
  1. CUDA torch first (the default `pip install torch` on Windows is
     **CPU-only**):
     `.venv\Scripts\python -m pip install --index-url https://download.pytorch.org/whl/cu124 torch`
  2. Then the embed extras:
     `.venv\Scripts\python -m pip install -e ".[embed]"` inside `backend/`
     (installs `sentence-transformers` + `numpy`).
  3. Sanity check:
     `.venv\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`

#### Stage 3 — LLM

Sends remaining residuals in batches to OpenAI / Anthropic / a local
provider with a few-shot prompt and writes the response back to the DB.

- **Provider**: `openai` (env: `OPENAI_API_KEY`), `anthropic`
  (env: `ANTHROPIC_API_KEY`), or `echo` (no-op dry-run, useful for testing).
- **Model**: `gpt-4o-mini` is the default and is plenty for tag
  classification. Approx **<$1 for ~2,500 tags**.
- **Max tags**: caps how many residuals to query in one run. Useful to
  bound cost.
- Results are cached to `backend/data/tag_classification_cache.json` and
  re-used on future runs.

**Where do I put my OpenAI key?** Copy [`.env.example`](.env.example) to
`.env` in the project root and fill it in:

```ini
OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=...
```

The backend loads `.env` automatically on startup without overwriting
real shell environment variables (so a value in your Windows env wins).
`.env` is gitignored. If you'd rather use the shell, just
`set OPENAI_API_KEY=sk-...` before launching `dev.bat`.

**Important — Stage 3 only sees residuals.** The Stage 3 query is
literally `WHERE Tag.bucket = 'other'`, so anything Stage 2 already moved
out is invisible. If Stage 2 made bad calls, use **Reset embed relabels**
(below) to bring them back to `other` before running Stage 3.

Both buttons run as background jobs with live SSE progress.

#### Rebuild scene_line only

If you've manually overridden a bunch of tags from the Tags table, click
**Rebuild scene_line only** to regenerate every image's per-bucket lines
without re-classifying anything else. This is usually the last step before
running a new export.

#### Reset embed relabels

If a Stage 2 run produced a lot of low-confidence garbage (weapons in
`composition`, animals in `background`, etc.), click **Reset embed
relabels** with a confidence cutoff (default `0.65`). Every tag where
`bucket_source = 'embed'` AND `confidence < cutoff` is returned to
`bucket='other', bucket_source='unknown'`, and the revert is logged as a
`reset` row in the audit history (so you can still trace what changed).
Tags with `locked = true` (your manual overrides) are never touched.

The typical recovery loop after a bad Stage 2 run looks like:

1. **Audit Stage 2** preset in the Tags filter header → eyeball the
   lowest-confidence rows.
2. **Re-run Stage 1 rules** first (see next section) — deterministic and
   free, fixes any tag the embedder misrouted that a Stage 1 rule now
   catches (e.g. franchise-suffix tags into character).
3. **Reset embed relabels** with cutoff `0.65` for anything left.
4. **Run embedding pass** again, this time with threshold `0.65` (and
   the `extras` bucket now active so weapons/objects/animals route there
   instead of being force-fit into composition/pose/background).
5. **Run LLM pass** to mop up whatever is still in `other`.
6. **Rebuild scene_line only** — propagates everything into the
   wildcards.

#### Anthropomorphic subject rule

Stage 1 routes any tag whose canonical form ends in `_boy`, `_boys`,
`_girl`, `_girls`, `_man`, `_men`, `_woman`, or `_women` (and has at
least one segment before that suffix) directly to the `character`
bucket. This catches things like `hedgehog_boy`, `goat_boy`,
`red_panda_girl`, `alpaca_girl`, `cool_old_man` that aren't in
`tags.jsonl` as character category but are clearly subject identifiers.
Without this rule those tags fall to `other` and Stage 2 routes them at
random by cosine of the noun (so `goat_boy` was ending up in `pose`).
The source value on the Tag is `anthro_rule` and you can filter by it in
the Tags table.

#### Franchise / qualifier suffix rule

Danbooru's canonical disambiguation form is `<name>_(<qualifier>)`:

- `_(cosplay)` → routed to `outfit` (it's wearing a character's costume)
- `_(style)`, `_(art_style)` → routed to `composition`
- `_(weapon)`, `_(object)`, `_(item)`, `_(food)` → routed to `extras`
- `_(meme)`, `_(module)` → skipped (left as `other`)
- any other parenthesized suffix — `_(genshin_impact)`, `_(umamusume)`,
  `_(idolmaster)`, `_(pokemon)`, `_(vocaloid)`, etc. — is assumed to be a
  franchise-bound character name and routed to `character`

Tag sources: `qualifier_rule` (cosplay/style/weapon/...) and
`franchise_suffix` (everything else with a parenthesized suffix). Both are
filterable in the Tags source dropdown and the audit panel. Confidence is
~0.85, so an explicit `tags.jsonl` category (1.0) or anthro_rule (0.90)
still wins.

This rule prevents the most common Stage 2 mistake: tags like
`stay_gold_clan_(umamusume)`, `mistsplitter_reforged_(genshin_impact)`,
`jupiter_(pokemon)_(cosplay)` being shoved into `composition` or
`background` because the embedder doesn't know what those franchises are.

#### Re-run Stage 1 rules

When new Stage 1 rules are added (anthro / franchise-suffix / qualifier),
they only take effect on **new** ingests. To apply them to tags that have
already been routed somewhere by an earlier Stage 2 run, click **Re-run
Stage 1 rules** in the Smart classify card.

The pass walks every `Tag` row, calls `classify_tag()` again, and
upgrades the bucket when:

- The tag is currently `other`/`unknown` (always safe), OR
- The tag is currently `bucket_source in {embed, llm}` with
  `confidence ≤ replace_below_confidence` (default `0.85`) AND the new
  Stage 1 result would produce a different bucket. Stage 2/3 calls held
  with very high confidence are left alone.
- `locked = true` tags (your manual overrides) are never touched.

Every upgrade is recorded in the audit history with the new
`to_source` value (`franchise_suffix`, `qualifier_rule`, `anthro_rule`,
or `dataset_category`), so you can revert individual upgrades the same
way you revert embed/llm calls.

This is the **preferred fix** when Stage 2 made structural mistakes
that match a deterministic pattern — it's free, instant, and
non-destructive (no wholesale reset).

### Relabel history (audit panel)

Every Stage 2 / Stage 3 / manual bucket change writes a row to the
`tag_classification_history` table so you can review what changed and roll
back individual relabels. The panel sits below the Tags table.

- **Source**: `embed` (Stage 2), `llm` (Stage 3), `manual` (Tags table
  edits and revert clicks), `franchise_suffix` / `qualifier_rule` /
  `anthro_rule` / `dataset_category` / `tag_tree` (Stage 1 rule
  upgrades from a **Re-run Stage 1 rules** pass), `reset` (rows
  produced by the **Reset embed relabels** button — `to_bucket =
  'other'`), or `backfill` (one-time seed). Default is `embed` because
  that's the noisiest source.
- **From / To bucket**: narrow down a specific flow, e.g.
  `from = other, to = composition`.
- **Sort = lowest confidence (risk audit)** is the default — it surfaces
  the most uncertain relabels first, which is where Stage 2 mistakes
  cluster. Anything ≤ ~0.60 is worth eyeballing.
- **Top from → to flows** strip shows a histogram of where Stage 2 (or
  Stage 3) is moving tags. If you see a giant `other → composition`
  arrow with low average confidence, your threshold may be too low.
- **revert**: rolls a single relabel back to its `from_*` state and
  **locks the tag** so subsequent classifier passes won't redo it. The
  revert itself is recorded as a `manual` history row so the audit log
  stays complete.
- **Backfill from current state**: one-shot seed for runs that happened
  before the audit log existed. It walks every `Tag` row with
  `bucket_source in {embed, llm}` and writes a history row asserting
  `from_bucket = 'other'` (which is invariant — Stage 2/3 only ever
  touch tags that are still in `other`). Idempotent: re-running skips
  tags that already have a row for that source.

You can also use the **Audit Stage 2** button in the Tags filter header —
it presets `source = embed, sort = confidence asc` on the main Tags
table, so you can browse the actual current state of low-confidence
embed-labelled tags side-by-side with the history panel.

---

## Trends tab

Window-vs-baseline tag frequency deltas across the whole DB.

- **Recent window (days)** — default 7
- **Baseline window (days)** — default 30
- **Bucket filter** — restrict the analysis to one bucket

A bar chart on top of the 25 highest-ratio tags, then a full table with
`recent`, `baseline`, and `(recent+1)/(baseline+1)` ratio.

This is only meaningful **after** you've ingested Booru fetches over time
(the Trends tab needs `Image.created_at` spread across multiple days). A
local metadata dump all gets ingested with `created_at ≈ now`, so to use
Trends you need to either:

1. Run a few daily `popular` / `rank` Booru fetches over the course of a
   week or two, or
2. Use the built-in `trending` mode on the Ingest tab (one-shot Danbooru
   query that fetches its own two windows and returns the delta directly).

---

## Builder tab

Interactive prompt assembler. Useful when you want to use Tag Forge as a
manual NovelAI workflow tool rather than just a wildcard generator.

1. **Character / subject** — your character description, e.g.
   `1girl, solo, character_name, alternate costume, …`
2. **Base / quality prompt** — your usual quality block,
   `masterpiece, best quality, very aesthetic, absurdres, …`
3. **Rating filter** — comma-list, restricts which scenes can be rolled
4. **Score min** — restricts further
5. **Sources** — pick which ingests to draw from

Buckets:

- Each of the five buckets (outfit / pose / expression / background /
  composition) gets a textarea and a 🔒 lock toggle.
- Click **Roll** — every unlocked bucket gets a fresh random combo from a
  scene_line row matching your active filters. Rolled lines automatically
  strip **subject counters** (`1girl`, `solo`, …) and **body-anatomy**
  tags (`breasts`, `tail`, `wings`, `ahoge`, …) so they never duplicate
  what you already typed in **Character / subject**.
- Edit any bucket inline if you want to tweak the auto-rolled combo.

The **assembled prompt** at the bottom concatenates:

```
<base>, <character>, <outfit>, <pose>, <expression>, <background>, <composition>
```

Click **Copy** to send it to the clipboard.

---

## Export tab

This is the payoff — write fresh `outfit.txt`, `pose.txt`, etc. wildcard
files for your generation front-end.

1. **Name** — used for the manifest and the default output directory
2. **File prefix** — optional. Prepends to every filename, e.g. prefix
   `good` produces `goodoutfit.txt`, `goodpose.txt`, etc. (matches your
   existing `goodclothing4.txt`-style names)
3. **Scene rating filter** — comma list (`g,s`). Whole-scene filter, see
   [Scene rating classifier](#scene-rating-classifier).
4. **Max rating per line** — `any | g | s | q | e`. Per-tag strip mode that
   redacts individual offending tags from each scene line while keeping the
   rest of the scene. Default is `any` (no stripping).
5. **Score min** — only include scenes from images with this score or higher
6. **Min tags / line** — skip lines with fewer than N tags (default 2,
   prevents single-word lines). Re-checked after `max rating` stripping.
7. **Dedupe** — strip duplicate lines (recommended ON)
8. **Origin** — `all | local | booru` toggle. Restricts to one provenance.
9. **Output directory** — type a path or click one of the preset pills:
   - `tagforge_exports` → `<repo>\exports\<name>\`
   - `wildcards` → wherever your generation front-end reads wildcards from (set `TAGFORGE_WILDCARDS_DIR`)
   - `common_prompts` → a second destination of your choosing (set `TAGFORGE_COMMON_PROMPTS_DIR`)
10. **Sources** — multi-select pills, now split under **Local imports** and
    **Booru fetches** headings with a `select all` shortcut per group. Empty
    selection = include everything (after the Origin / score / rating filters).
11. **Buckets** — which `.txt` files to emit. Defaults are
    `outfit`, `pose`, `expression`, `background`, `composition`, `accessory`,
    `extras`, `character`, `scene`. `character` is special-cased to allow
    single-tag lines (one solo character per image is valid) — `min tags / line`
    is auto-clamped to `1` for that bucket only.
12. **Deny tags (strip from every scene line)** — drops matching tokens
    from every scene line before writing. Two pieces:
    - **Use built-in defaults** checkbox (on by default) applies a curated
      list of tags you almost certainly already supply via your main
      prompt or never want — subject counters (`1girl`, `2girls`,
      `solo`, etc.), male-coded subject tags (`1boy`, `2boys`, …), and
      body-anatomy descriptors (`breasts`, `huge_breasts`, `tail`,
      `wings`, `ahoge`, …), and **eye-color tags** (`red_eyes`,
      `blue_eyes`, `heterochromia`, … — always stripped even when the
      deny checkbox is off) via pattern rules (e.g. any `*_breasts`,
      `*_wings`, safe `*_tail` without catching `ponytail`). Click
      **show default list** to see the exact subject set; anatomy tags
      are also stripped even if not listed. Same logic applies in the
      Builder roll endpoint. Served by `GET /api/export/default-deny-tags`.
    - **Textarea** for ad-hoc additions — comma- or newline-separated
      tags. Case-insensitive, spaces are auto-converted to underscores,
      leading `-`/`!` is tolerated. Example: `tongue out, eye contact,
      looking at viewer`.

    Built-in defaults use exact names plus anatomy heuristics (regex) for
    `breasts` / `tail` / `wings` families. Extra textarea entries are
    exact-match only. A line that drops below `min tags / line`
    after stripping is skipped, same as the rating strip mode. The full
    deny set used (defaults + extras) is recorded in
    `manifest.json[filters]` so reruns are reproducible.

**Run export** writes the files plus a `manifest.json` documenting every
filter used. The result panel below shows the absolute paths and line
counts. Each call to `/api/export/run` also persists an `export_set` row so
the export is reproducible.

### Exporting trending characters from Danbooru

The `character` bucket exists specifically for "give me a wildcard of which
characters are showing up in this week's top posts" style queries.

**If `character.txt` is empty:** that bucket is filled only from **Booru
ingests** (Danbooru's category-4 character tags on each post). A local
`metadata.txt` ingest does not populate it. Re-export with **Origin =
booru** (or **All**). The export result panel will show a warning when
this happens.

1. Ingest tab → **Fetch from Booru** card → pick `mode = popular`, set the
   date and limit, and run. The booru runner now persists per-image
   character tags into `scene_line[character]` automatically (older fetches
   need a one-time **Rebuild scene_line only** from the Tags page —
   `tags_character` was already in `image_tag`, so the rebuild fills in
   the new bucket for free).
2. Export tab → set **Origin** = `booru`, pick the popular Source(s), tick
   only the **character** bucket (untick the rest if you just want
   `character.txt`).
3. Click **Run export** → `character.txt` lands in your output directory
   with one line per image listing that image's characters
   (e.g. `hatsune miku`, or `ganyu (genshin impact), keqing (genshin impact)`
   for group shots).

The combined `scene.txt` bucket intentionally still excludes character
tags so it stays drop-in for your existing `__character__ + __scene__`
prompt structure where you supply the character separately.

### Why there's no date-range filter

For Booru data, a single "Fetch from Booru" run produces a single
`Source` row whose label includes the date (e.g. `popular 2025-04-23 ·
limit 1000`) and whose `filters_json` records the exact query. The
Source picker on this page already groups these and lets you multi-select,
so "popular 2025-04, 2025-05, 2025-06 combined" is just a 3-click
operation — no separate date input needed.

For local `metadata.txt` ingests, `Image.created_at` is set when you
ingested, not when the image was actually generated, so a date filter
would be misleading there too. If you ever start running long-term
monthly Booru pipelines and want to slice by the *original* Danbooru
post creation date across many sources, that requires adding
`Image.posted_at` and a column migration — worth doing then but
unnecessary today.

### Mapping to your existing prompt

Your existing prompt uses `__goodclothing4__`, `__goodexpression2__`, etc.
To replace those with Tag Forge output:

1. **Name**: pick a memorable name like `may2026`.
2. **File prefix**: `good` (matches existing convention).
3. **Output directory**: click the `wildcards` preset.
4. **Min tag count**: 2.
5. **Origin**: `local` (only export from your own metadata.txt ingests).
6. **Max rating per line**: `s` (sensitive) — strips any explicit/questionable
   tokens that snuck through.
7. Click **Run export**. You now have `goodoutfit.txt`, `goodpose.txt`,
   `goodexpression.txt`, `goodbackground.txt`, `goodcomposition.txt`,
   `goodaccessory.txt`, `goodextras.txt`, `goodscene.txt` sitting next to
   your existing files.
8. Update your main prompt to reference the new files:
   ```text
   [[[__goodoutfit__, __goodexpression__, __goodpose__,]]]
   __goodbackground__, __goodcomposition__,
   ```
   Add `__goodextras__` if you want to roll in props/objects (`holding cup of
   tea`, `katana`, etc.). Use `__goodscene__` if you want one big coherent
   scene block instead of separate sub-rolls.

You can keep both old and new wildcard files side-by-side and A/B test.

---

## Headless CLI

Every UI action has a CLI equivalent, useful for scripting and CI.

```bat
:: from the repo root, in the venv
.venv\Scripts\python -m backend.cli --help
```

Common commands:

```bat
:: 1) initialise / migrate the sqlite schema
.venv\Scripts\python -m backend.cli init-db

:: 2) WIPE the schema and start over. Destructive.
.venv\Scripts\python -m backend.cli reset-db --yes

:: 3) re-download the tag taxonomy (only needed once)
.venv\Scripts\python -m backend.cli seed-tag-tree

:: 4) dry-run preview of a metadata file (now shows per-bucket + inferred rating)
.venv\Scripts\python -m backend.cli preview-metadata ^
    "C:\path\to\metadata.txt" --sample 5

:: 5) full ingest of a metadata file (synchronous, no UI required)
.venv\Scripts\python -m backend.cli ingest-metadata ^
    "C:\path\to\metadata.txt" ^
    --label may2026 --drop-artist --drop-quality

:: 6) backfill scene ratings (only images with no rating yet)
.venv\Scripts\python -m backend.cli classify-ratings

:: 7) export wildcards (synchronous). New flags: --origin, --max-rating, --file-prefix
.venv\Scripts\python -m backend.cli export may2026 ^
    --out "C:\path\to\your\wildcards" ^
    --origin local --rating s --max-rating s --file-prefix good --score-min 20

:: 8) just run the API server (without the UI)
.venv\Scripts\python -m backend.cli serve --port 9301
```

For Booru fetches, the easiest path is the UI; programmatic access works
via `POST /api/danbooru/fetch` (see `backend/routes/danbooru.py`).

---

## Recommended end-to-end workflow

A typical "just ingested a batch of new images, refresh my wildcards" run:

1. *(Optional)* **Wipe previous ingest** — if you want a clean baseline after
   a schema change or a category bump:
   ```bat
   .venv\Scripts\python -m backend.cli reset-db --yes
   ```
   Then close any open dev.bat windows and re-run `scripts\dev.bat` so the
   backend creates the new tables on startup.
2. **Dashboard** — note the existing counts so you have a before/after comparison.
3. **Ingest tab → Import metadata.txt**:
   - paste path, leave label blank
   - all three drop-checkboxes on the default (drop artists + quality, keep characters)
   - click **Preview first 20** and skim — do the bucket breakdowns + the
     `rating g/s/q/e` pill on each preview row look reasonable?
   - if yes, **Start ingest**. Watch the dashboard counts climb in the other
     browser tab. The `extras` bucket should start populating with props /
     objects / food / etc.
4. **(Optional) Tags tab → Smart Classify**:
   - if `classifier_coverage` is below ~25%, install the `embed` extra and
     run **Stage 2**. This usually adds another 10-15 percentage points of
     coverage.
   - if you still see misses, run **Stage 3 (echo)** first to verify the
     cache wiring, then run **Stage 3 (openai gpt-4o-mini)** with
     `max_tags = 1000` to put a budget cap on it.
   - if you ever extend `tag_ratings.py` with new explicit tags, click
     **Classify scene ratings** (with `overwrite previously inferred ratings`
     checked) to re-process the corpus with the updated dict.
5. **Tags tab** — scroll through the highest-usage tags and manually fix any
   obvious mistakes (lock them with the override). Click **Rebuild
   scene_line only** when you're done.
6. **Scenes tab** — spot-check 3-4 records. The detail panel now shows the
   inferred rating + evidence tags so you can sanity-check the classifier.
7. **Export tab**:
   - name: `may2026`, prefix: `good`
   - output dir: click `wildcards` preset
   - origin: `local` (or leave `all` to include any Booru fetches too)
   - scene rating filter: `g,s` (skip explicit-rated scenes entirely)
   - max rating per line: `s` (strip any remaining explicit tokens per line)
   - min tag count: 2, dedupe: on
   - click **Run export**
8. **Validate**: open `goodoutfit.txt` from explorer — every line should be
   a coherent outfit combo from one real image. Same for the others.
   `goodextras.txt` is the new one to browse — if most lines look like they
   belong in `pose`, tell me and we can fold extras→pose in the categorizer.
9. **In your generation front-end**: edit your main prompt to use `__goodoutfit__`,
   `__goodexpression__`, etc. Generate a few test images.
10. **Iterate**: any wonky combos? Find them in **Scenes**, see which tags
    are off, fix in **Tags**, **Rebuild scene_line**, re-**Export**.

---

## Troubleshooting

### `WinError 10013` when starting the backend

A previous Tag Forge backend is still bound to 9301. The new `dev.bat`
auto-kills it before launching, so just re-run `dev.bat`. If that's not
enough:

```pwsh
netstat -ano | findstr :9301
# pick the PID from column 5, then:
Stop-Process -Id <PID> -Force
```

If `netstat` shows nothing on 9301, Windows itself has the port reserved
(Hyper-V / WSL2). Verify with:

```bat
netsh int ipv4 show excludedportrange protocol=tcp
```

Pick a port outside every listed range and update the three places in
`vite.config.ts`, `settings.py`, and `dev.bat`.

### Vite shows `http://localhost:5173/` instead of `9300`

Your `frontend/` folder has a stale `vite.config.js` shadowing the `.ts`
file. Delete it:

```bat
del frontend\vite.config.js
del frontend\vite.config.d.ts
del frontend\tsconfig.tsbuildinfo
del frontend\tsconfig.node.tsbuildinfo
```

Then re-launch `dev.bat`. The current `package.json` no longer emits these
files, so it shouldn't recur.

### Proxy errors `/api/* → 127.0.0.1:7860 ECONNREFUSED`

Same root cause as above — stale `vite.config.js` is pointing the proxy at
the old port. Fix as in the previous item.

### Preview shows lots of tags going to `other` / "dropped"

That's normal and expected — see [The mental model](#the-mental-model).
Character / body / hair color / quality / artist tags all live outside the
wildcard buckets by design. If you see a tag in `other` that you think
*should* be in a bucket, go to the Tags tab, find it, change the bucket
inline, then click **Rebuild scene_line only**.

### Ingest job stalled / no progress events

Open the **Tag Forge backend** cmd window — there should be a traceback.
Common causes:

- Bad UTF-8 in a record: the parser uses `errors="replace"` so this shouldn't crash, but please file an issue with the offending filename.
- SQLite lock contention: another process has `backend/data/tagforge.db` open. Make sure DB Browser for SQLite (or similar) is closed.

### Frontend is up but Dashboard shows zeros after ingest

Hard-refresh (`Ctrl-Shift-R`) or wait 5 s — the Dashboard refetches every 5
seconds. If counts are still 0, check the backend window for the ingest
job's "done" log line and inspect `backend/data/tagforge.db` directly:

```bat
.venv\Scripts\python -c "from backend import db; from backend.models import Image; from sqlmodel import select, func; s=next(db.get_session()); print(s.exec(select(func.count(Image.id))).one())"
```

If that prints `0`, the ingest didn't actually persist anything — re-run
with the `preview-metadata` CLI first to see whether the parser is finding
records.

### "Failed to fetch" in browser dev console

The backend hasn't started yet or has crashed. Check the **Tag Forge
backend** cmd window. Refresh the page once `Application startup complete`
is logged.

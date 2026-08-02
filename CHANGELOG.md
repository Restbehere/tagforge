# Changelog

Notable changes per release. This project follows
[semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`, where
MINOR adds features and PATCH is fixes only. Pre-1.0, the API and schema
may still shift within a MINOR bump.

The version is declared in exactly two files — `frontend/package.json`
and `backend/pyproject.toml` — and read from there everywhere else (the
sidebar footer, Settings → About, and `GET /api/health`). Bump both, and
add an entry here, in the same commit as the change.

## 0.8.0 — 2026-08-02

### Added
- **Configurable LLM endpoints** (`Settings → LLM providers`). Tag
  classification and the NAI prompt splitter each point wherever you like
  — OpenAI, any OpenAI-compatible gateway (OpenRouter, Groq, Together, a
  self-hosted server), Anthropic, or the bundled local llama-swap — with
  your own base URL, API key and model name. They are configured
  independently on purpose: this corpus is explicit, and hosted models may
  refuse or quietly soften it, so classification can go to an open-weights
  endpoint without giving up a local splitter.

  A **Test** button round-trips one tiny request so a bad URL, key or
  model name surfaces immediately instead of halfway through a long run.
  Model is free text with suggestions; concurrency is adjustable because
  shared gateways rate-limit the default 6-way fan-out.

- API keys live in a new `api_credential` table, never in a preset — the
  presets endpoint returns every row's data verbatim, so a key stored
  there would be readable over the API. No endpoint ever returns a key;
  only a masked hint. Existing `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
  environment variables keep working as fallbacks.

### Fixed
- **Automatic post-ingest classification ignored your provider choice.**
  It hardcoded OpenAI + gpt-4o-mini, so every newly-ingested tag went to
  OpenAI even after Stage 3 had been pointed elsewhere — precisely the
  leak that switching providers is meant to prevent. It now follows the
  configured endpoint.
- The OpenAI classification path now tolerates replies wrapped in prose or
  ``` fences (the Anthropic path already did). Only OpenAI proper honours
  `response_format=json_object`; gateways that ignore it used to fail the
  whole batch.
- `temperature` is now optional per endpoint. Reasoning models (o-series,
  gpt-5) reject an explicit temperature and would have errored on every
  request.
- Base URLs are joined correctly whether or not they already end in `/v1`,
  so a pasted `https://openrouter.ai/api/v1` no longer becomes `/v1/v1/`.
- The splitter degrades from `json_schema` to `json_object` on non-local
  endpoints, since grammar-constrained output is a llama.cpp feature.

### Changed
- Default OpenAI model for Stage 3 is now `gpt-4.1-mini` (was
  `gpt-4o-mini`) — same cheap/fast tier, better at holding the JSON-map
  instruction across 50 tags, and still accepts a plain `temperature`.

## 0.7.0 — 2026-08-02

### Added
- **Read prompts straight out of images** (`Ingest → Read images directly`).
  Point it at a folder and it pulls each image's embedded generation
  metadata itself — no separate extractor tool and no `metadata.txt` in
  between. Handles NovelAI stealth-PNG (the gzipped JSON hidden in the
  alpha channel's least significant bits), NovelAI's plain PNG text
  chunks, and Stable Diffusion WebUI `parameters` strings. Preview a
  sample before committing to a long run; images carrying no metadata
  are reported as such rather than counted as failures.

  Records are built identically to the `metadata.txt` path — including
  preferring NAI V4's resolved `actual_prompts` caption and expanding
  `|| a | b ||` option blocks — so everything downstream is unchanged and
  the two import routes are interchangeable.

  Adds Pillow and numpy to the backend requirements.

### Fixed
- The ported LSB reader no longer hangs on truncated data. The original
  silently stopped producing bits once it ran past the last pixel, so the
  byte loop spun forever (the old tool papered over it with a per-image
  timeout). Exhaustion is now an explicit failure, so such an image is
  reported as carrying no metadata instead of stalling or yielding a
  half-decoded record.

## 0.6.0 — 2026-07-31

### Added
- **Speech-bubble toggle** on the NAI splitter: `auto` (the model decides
  per scene), `bubble` (always draw one), `no bubble` (always suppress
  with `-1::speech bubble::` so the words sit as bare lettering). The
  explicit settings are enforced in code rather than trusted to the
  prompt, so the toggle always wins.
- **Text-position toggle**: `by speaker` (default), `placed`, `free`.
  *By speaker* attributes the line to whoever makes it — `she says "…"`,
  or for non-speech `a soft "mmm" hanging around her body` — which lets
  the image model place the text beside that character on its own,
  rather than pinning it to frame coordinates. *Placed* uses rough
  positions (top left, next to her face); *free* gives none at all and
  lets the model choose.

  Both settings persist across sessions and only appear while dialogue
  is on.

## 0.5.2 — 2026-07-31

### Changed
- **The NAI splitter now art-directs in-image text instead of just quoting
  a line.** Dialogue is written as plain-English direction — size, colour,
  rendering and shape, optional placement, bubble control (a speech
  bubble, or `-1::speech bubble::` to suppress one for bare impact
  lettering), sparing decoration — followed by the words in quotes. The
  treatment is scaled to the scene, so a quiet moment gets
  `soft light pink text, speech bubble, tiny hearts, "Mmm..."` while an
  impact beat gets `large text, sound effect text, painted white text,
  thick black outline, -1::speech bubble::, "GASP!"`. Compose-from-idea
  mode follows the same grammar.

### Fixed
- Speech could silently come back empty (~19% of calls on long scenes):
  the model intermittently returned nothing, or styling with no quoted
  words — which is unusable, since the image model then invents glyphs.
  Dialogue is now validated and, when missing, repaired with one short
  follow-up call. 16/16 scenes produce usable text, against 13/16 before.

## 0.5.1 — 2026-07-31

### Fixed
- **Decompose no longer aborts when the model finds no head.** A fully
  draped or occluded subject (and back views, or crops above the
  shoulders) produced an empty `head` layer, which cascaded into a `0x0`
  crop and killed the run inside OpenCV — discarding the body layers
  that had already succeeded. The head-detail stage is now skipped in
  that case, transparent stand-ins are written for its 11 layers, and
  the depth pass and PSD assembly still run. Verified end to end: an
  image that previously died at `layerdiff 100%` now finishes with a
  full PSD. The fix lives in the vendored pipeline and is kept in
  `see-through-patches/` so a `git pull` there can't silently drop it.
- Failed decompose runs now explain themselves instead of surfacing a
  raw OpenCV assertion, and keep their partial layer output browsable
  rather than discarding it.

## 0.5.0 — 2026-07-31

First versioned release, cut while preparing the project to be published.

### Added
- **Builder**: roll-by-tag filter, coherent scene rolls, prefetch pool so
  every roll is instant, background limiter, exclude tags, presets and
  last-used settings, subject-count filter.
- **NAI prompt splitter** backed by a local LLM via llama-swap: split and
  split-plus-natural-language modes, dialogue authoring, identity strip,
  background invent/enrich, compose-from-idea, VRAM controls.
- **Decompose** tab (see-through layer decomposition) and **Rig** tab
  (2.5D rig preview with MP4/WebM/GIF capture).
- Danbooru fetch by tag search, ID/URL lookup, and multi-character
  filters; optional SOCKS/HTTP proxy for networks that block boorus.
- Publish tracker and poll helper on Trends; export scene recipes, tag
  share caps, and order-insensitive dedup.

### Changed
- Renamed from PromptFinder to Tag Forge. Env vars moved to the
  `TAGFORGE_` prefix (the old `PROMPTFINDER_` names still work), and an
  existing `promptfinder.db` is picked up in place.
- New logomark and a bundled display face for the wordmark.
- Default NAI-split model is now the abliterated 27B with MTP
  speculative decoding — roughly twice as fast at equal quality.

### Fixed
- Host-header validation, closing a DNS-rebinding hole in the local API.
- Booru credentials are no longer persisted to, or served from, job
  records.
- Missing `image_tag.tag_id` index — the tags page was scanning 11.5M
  rows on every load.
- Metadata ingest inserted the records it reported as skipped; jobs
  interrupted by a restart stayed "running" forever; deny-list renames
  onto an existing name returned 500 instead of 409.

### Security
- Path-traversal fixes in decompose deletion and export naming;
  SSRF fix on the booru `site` parameter; LIKE wildcards from user
  search input are escaped.

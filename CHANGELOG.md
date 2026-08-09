# Changelog

Notable changes per release. This project follows
[semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`, where
MINOR adds features and PATCH is fixes only. Pre-1.0, the API and schema
may still shift within a MINOR bump.

The version is declared in exactly two files — `frontend/package.json`
and `backend/pyproject.toml` — and read from there everywhere else (the
sidebar footer, Settings → About, and `GET /api/health`). Bump both, and
add an entry here, in the same commit as the change.

## 0.10.0 — 2026-08-06

### Added
- **Split handoff for browser userscripts** (`GET /api/llm/handoff`). Every
  split/compose result is kept in a one-slot in-memory buffer with a
  monotonic sequence number, so a userscript running on an image
  generator's page can poll the local API and inject new prompts the
  moment Process finishes — no copy-paste round trip. In-memory only;
  restarting the backend clears it.

## 0.9.5 — 2026-08-06

### Fixed
- **Identity strip no longer leaks modified innate features.** The strip
  backstop matched exact tag names only, so bare `wings`/`horns`/`halo`
  were removed while `purple wings`, `low wings`, `multiple horns` and
  `black halo` sailed through into supposedly character-agnostic prompts.
  Innate features are now caught by their feature noun under any modifier,
  with the promised carve-outs intact (`fake animal ears`/`fake horns`/
  `fake tail` are costume pieces and stay; `french horn` and `party horn`
  are props, and pose tags like `covering ears` are untouched).
- **The scene description leaked the same features as prose** ("her purple
  wings spread slightly behind her") — invisible to any tag-level filter.
  In strip mode, clauses mentioning innate features are now removed from
  scene sentences, keeping the rest ("The girl squats low on a
  sun-drenched sandy beach. She looks directly up at the viewer...").
  The prompt also now spells out that coloured/counted variants are still
  innate and that scene_description must describe pose and setting without
  them. Verified live both ways: strip mode outputs zero feature mentions
  across base, scene and characters with the outfit intact; keep mode
  still carries wings, horns, halo and the character name.

## 0.9.4 — 2026-08-05

### Changed
- **Multi-character splits no longer render synchronized clones.** When a
  group image's tags carried an unattributed action (`arms_up`,
  `holding_swimsuit`, `innertube`...), the splitter hedged by copying it to
  every character — three girls, all arms up, when the source had one. The
  splitter prompt now teaches union semantics: a pose/action/held-object/
  clothing-state tag describes SOME character, usually one, so distinct
  actions are distributed across characters and a one-character guess beats
  duplication. Mutual activities (dancing, holding hands, hugs) stay
  all-participant interaction in base, and expressions may still repeat.

  Measured on a fixed 6-case set (2 runs each, local 27B): duplicated
  singleton actions fell from 4/14 to 1/14 observations on the reference
  beach scene (strip mode) and 2/14 to 1/14 (keep mode), with mutual
  activities, distinct-pose distribution, and single-character splits
  unchanged. Genuine group-photo compositions (four girls all flashing
  peace signs, nothing else to distribute) still read as a group photo.

## 0.9.3 — 2026-08-03

Follow-up to 0.9.2's classification fixes, driven by a full before/after
review of every tag the repair would move. The corpus repair has been
applied: 357 tags re-bucketed and 35 released, with a database backup
taken first and every change in the relabel history.

### Fixed
- **Expressions now beat the verb catch-all.** `PRIORITY_MAP` ranked pose
  above expression, and the tree's flat "tagged verbs" list contains
  anything verb-shaped — so `trembling`, `ahegao`, `pout`, `screaming` and
  friends were about to be re-bucketed into pose. Expression now outranks
  pose, and the verb catch-all only answers when no precise section claims
  the tag (it was also claiming `foreshortening` and `glowing`).
- **Anatomy nested under other sections is no longer claimed by them.**
  The tree files `back` and `navel` under Attire ("Body parts supposed to
  wear dresses"), `collarbone` under "Anatomy of the neck", and `cat_ears`
  under "Cat body parts" — all were about to become clothing and props.
  Anatomy containers now disown their enclosing section, leaving those
  tags wherever Stage 2/3 or the user already put them.
- **The twintails family is classified as the hairstyle it is.** The tree
  has no twintail entry, so after 0.9.2 stopped excluding them they were
  merely unstuck, not placed. A single rule now routes the whole family
  (38 spellings on this corpus) to accessory while leaving actions like
  `grabbing_another's_twintails` excluded.
- **`_(meme)` tags are never characters.** 0.9.2's qualifier reordering
  made unknown-qualifier handling reachable for mapped-to-nothing
  qualifiers, so meme tags started being stamped `character` — ~1,000
  would have moved; 29 already stamped during one evening's ingests are
  released back to the residual pool, along with `genderswap_(mtf)` and
  the other transformation qualifiers stamped before the rule existed.
- **Names merely ending in "tail" are not anatomy.** The tail exclusion
  matched mid-word, so `flametail_(arknights)`, `cottontail_(vtuber)` and
  `cattail` were filed as body parts and stripped from scene output. The
  token must now stand alone, and any parenthesised qualifier marks a
  proper noun. Deliberately NOT released wholesale: the same staleness
  check would have dragged `high_detail`/`intricate_details` (quality
  junk the old regex caught by accident, correctly sitting in
  quality_meta) into scene wildcards.
- `portrait` and `smiley_face` stay composition — "Face tags / Misc" is a
  junk drawer, not evidence of an expression; armour (`pauldrons`,
  `shoulder_pads`) is worn and routes to outfit; `soldier`/`viking` are
  roles, not armour; `whale_tail_(clothing)` is a garment, not a
  character.
- `Re-apply stage 1 rules` gains `release_stale`: a tag whose bucket was
  stamped by the franchise-suffix guess is reset to residual when the
  narrowed rule no longer claims it, so rule fixes cannot leave stale
  `character` labels behind.

## 0.9.2 — 2026-08-02

A full-codebase audit — nine subsystems, every finding independently
verified before it counted. 20 real defects, most of them in the
classification pipeline and none previously visible as an error: they
produced wrong data quietly.

**One-time repair.** Several fixes correct rules that already labelled
existing tags. New ingests are right immediately; stored rows change only
when you run `Tags → Re-apply stage 1 rules`, which now also re-evaluates
tags whose deterministic rule changed but whose confidence did not. On the
current corpus that moves ~163 tags (~94k tag references) — mostly hair
accessories and neckwear into `outfit`, and scene words like `nature`,
`cityscape` and `backlighting` into `background`.

### Security
- **Booru credentials could reach the log file.** `login` and `api_key`
  travel in the query string, and a 4xx raised an exception whose message
  embeds the full URL — logged verbatim on every failed fetch. 4xx now
  raise a redacted error, the job handler redacts before logging, and
  httpx's own request logging (which prints the query string at INFO) is
  quieted. The same 4xx were also retried three times, replaying the
  credentials; they are now excluded from retries.
- Export `buckets` values become output filenames but skipped the path
  validator applied to `name`/`file_prefix`, so `../../x` could write
  outside the export directory.
- No ceiling on gzip-decompressed stealth-PNG payloads: the 4 MB input cap
  bounded only the compressed size, so a crafted image could inflate to
  gigabytes. Capped at 32 MB.

### Fixed — classification
- **Tag-tree conflicts were resolved by JSON walk order, not the documented
  priority.** Tags appear under several parents and `PRIORITY_MAP` is meant
  to pick the winner; the walk kept whichever leaf it reached first. Worse,
  a tag whose first-seen leaf sat under an unmapped section was pinned to
  `other` — hiding a real bucket it had elsewhere, and sending it to the
  paid LLM. `navel`, `collarbone`, `barefoot` and `mustache` were all stuck
  that way. Results are now stable if upstream reorders the tree.
- **`twintails` was classified as an anatomy tail** and stripped from every
  scene line, export and Builder roll — 22k tag references. The anatomy
  regex carved out `ponytail` but not the twintail family.
- **Unknown parenthesised qualifiers were stamped `character` before the
  tag tree was consulted**, so `shower_(place)`, `diamond_(shape)` and
  `dakimakura_(medium)` were exported as characters. The franchise guess now
  runs last, and the qualifier map covers place/shape/symbol/medium/
  software/sex.
- **`echo` dry runs were not dry.** Their all-`other` verdicts were written
  to the LLM cache and stamped `bucket_source='llm'` on every tag touched,
  permanently excluding those tags from Stage 2 and Stage 3. Echo now
  touches neither the cache nor any tag row.
- **A model replying `Outfit` instead of `outfit` had its whole run
  discarded.** Bucket values were compared raw against a lowercase list and
  clamped to `other`, then cached — so the damage was permanent and the job
  still reported success. Values are normalised, and an unrecognisable
  bucket is skipped so the tag is retried rather than pinned.
- Stage 2 built its centroids from any labelled tag including its own prior
  guesses, so each run's noise pulled the next run's centroids further off.
- Stage 2 wrote its verdicts back checking only `locked`, so a Stage 3 run
  or manual edit during the minutes it spends embedding was silently
  overwritten by a stale result.

### Fixed — everything else
- **`character.txt` ignored the Origin filter**: the booru-only override beat
  an explicit choice, so a "local" export shipped 150k booru lines and
  dropped every local one. The override now applies only when no origin is
  set.
- **Builder tag search missed most of the corpus.** Whole-tag matching
  assumed underscores and a single `', '` separator, so space-form tags
  (~42% of the corpus) and 40k newline-separated prompts were invisible:
  searching `long hair` found 692 images where 120,773 match.
- **Recursive folder ingest keyed images by basename**, so `batch1/image_0.png`
  and `batch2/image_0.png` overwrote each other. Ids now come from the path
  relative to the ingest folder, and re-ingest refreshes the prompt fields
  instead of leaving tags and `raw_prompt` disagreeing.
- **ComfyUI PNGs were shredded into hundreds of garbage tags** — their
  `prompt` text chunk holds the workflow JSON, which was parsed as a NovelAI
  parameter dict.
- A1111 `stealth_pnginfo` images never decoded: the shorter `stealth_png`
  magic prefix-matched and consumed `info` as the payload length.
- One malformed record aborted an entire metadata ingest instead of being
  skipped.
- **The Batch API was unusable since 0.9.0** — the route required an explicit
  `provider: "openai"` that the UI had correctly stopped sending. My
  regression; the guard now resolves the configured endpoint.
- An `order:` metatag inside a user's tag search silently used cursor
  pagination, which Danbooru only supports for `order:id` — 500 past page 1,
  or wrong results via the windowed fallback.
- Splitter: forcing bubbles off crashed with an `IndexError` when the whole
  lead-in was the bubble piece; suppression only scrubbed the first block, so
  multi-line dialogue shipped a bubble and its own suppression; weighted
  `::…::` directives were split on their interior commas, silently changing
  what they suppress; identity-strip could re-admit the character name via the
  verbatim backstop; and a remote model returning `null` where a string
  belonged crashed with a 500.
- Status, Test and Unload each resolved the local server's address by a
  different rule — Unload ignored a configured address entirely.
- Export temp files used one fixed `.tmp` name, so two exports sharing a
  mirror folder corrupted each other; a duplicated scene-recipe bucket
  repeated its tags in every line; and the recipe path held all 1.6M rows in
  memory instead of streaming.
- A job's terminal event could be dropped when a stalled subscriber's queue
  filled, leaving the UI showing it as running forever.
- An empty-valued `TAGFORGE_*` variable resolved paths to `.`, which would
  have pointed Decompose's "Update" at Tag Forge's own checkout.
- Settings: switching provider kept the previous provider's model after a
  save; the batch "Applying…" button stuck forever if the apply job failed;
  and manual bucket edits left the relabel-history stats panel stale.

## 0.9.1 — 2026-08-02

### Fixed
- **Splitting on OpenAI failed with `Unrecognized request argument supplied:
  chat_template_kwargs`.** Every splitter request carried that llama.cpp
  extension (it suppresses Qwen's thinking blocks) unconditionally, and
  OpenAI rejects unknown arguments. Request bodies are now built by one
  helper that only includes what the target accepts — the local extension
  stays local, `max_tokens` becomes `max_completion_tokens` on OpenAI (its
  reasoning tier rejects the old name), and the splitter honours the
  "send temperature" setting, whose checkbox is back on its panel.
- **The Settings Test could pass while every real split failed.** The probe
  was a minimal hand-built request, so it never exercised the arguments the
  real call sends. It is now built by the same helper as a real split —
  anything the provider would reject fails at Test.

## 0.9.0 — 2026-08-02

An audit of every field in `Settings → LLM providers`, prompted by the
0.8.3 bug. That one was an instance of a pattern — a blank or stale field
falling back to a value belonging to a *different* provider than the one
selected — and the pattern had five more instances, three of which moved
data or credentials somewhere the user had not chosen.

### Security
- **An OpenAI key could be sent to a third-party gateway.** With a
  provider selected that has no environment variable of its own
  (`openai_compatible`), the key lookup fell through to `OPENAI_API_KEY`
  and put it in an `Authorization` header addressed to that gateway. The
  key hint compounded it by reporting "currently from OPENAI_API_KEY" for
  gateways, making it look deliberate. Environment fallbacks are now bound
  to the provider the variable belongs to.
- **A stored key outlived the provider it was entered for.** Keys were
  saved per feature, so changing provider re-sent the previous provider's
  key to the new host. They are now stored per (feature, provider); an
  existing key is migrated to the provider currently configured, and each
  provider keeps its own.
- **Switching provider kept the previous provider's endpoint.** The Base
  URL field is only rendered for `openai_compatible`, but its value
  survived a switch away and was still saved — so a target displaying
  "OpenAI", with no URL shown anywhere, went on posting to the old
  gateway with the OpenAI key attached. The endpoint is now cleared on
  both sides whenever the provider cannot use one.
- **A blank Base URL silently rerouted.** For Stage 3 it fell through to
  `api.openai.com` — sending the corpus to OpenAI, inverting the reason
  for choosing a gateway — and for the splitter to the local server. A
  provider with no endpoint of its own now requires one.
- **The Batch API ignored the configured provider.** It is an OpenAI
  product and always reached OpenAI on the environment key, whatever
  Stage 3 was set to. It now refuses to submit unless Stage 3 is
  actually on OpenAI.
- A per-run provider on the Tags page could disagree with the configured
  target, whose key and endpoint were still used — so choosing Anthropic
  there sent the *OpenAI* key to Anthropic. Per-run providers are gone
  apart from `echo`, which sends nothing.

### Added
- **Stage 3 can run on the local model.** "Local (llama-swap)" was in the
  dropdown but had no dispatch handler, so selecting the most private
  option failed with `unknown LLM provider: local`. llama-swap speaks the
  OpenAI wire format, so it now drives Stage 3 through the same client as
  every other endpoint — classification with nothing leaving the machine.

### Fixed
- The Tags page ran Stage 3 against hardcoded `openai` + `gpt-4o-mini`,
  overriding Settings on every manual run — the one thing that setting
  exists to control. It now follows the configured endpoint and names it.
- A key entered for Anthropic was stored, reported as stored, and then
  ignored: the client read only `ANTHROPIC_API_KEY` and failed saying no
  key was set.
- The splitter offered Anthropic and echo, neither of which it can drive
  (it speaks chat-completions directly); picking either quietly ran
  against the local server. Each feature now advertises only what it can
  dispatch.
- `echo` demanded a model name it never sends, so dry runs failed once
  the model field was empty.
- Status, Test and Unload each read the local server's address by their
  own rule. Unload ignored a configured address entirely, and a base URL
  ending in `/v1` made status request `/v1/v1/models` and report a
  running server as down. All three now resolve it identically.
- A Base URL with no scheme is rejected on save instead of surfacing later
  as a connection failure.
- "Parallel requests" could be driven to `NaN` by clearing the box,
  producing a raw 422. It is clamped as you type, and both Advanced
  controls are hidden on the splitter panel where neither has any effect.
- Saving one provider panel discarded unsaved edits in the other.
- An empty-valued `TAGFORGE_*` variable resolved paths to `.` rather than
  the default — which would have pointed Decompose's "Update" button at
  Tag Forge's own checkout. Empty now means unset.
- The About panel claimed "nothing leaves this machine" unconditionally,
  which the shipped default contradicts. It now reports where each
  feature's traffic actually goes, and the backend address reflects the
  origin in use rather than a hardcoded literal.

## 0.8.3 — 2026-08-02

### Fixed
- **Selecting a hosted provider without naming a model asked it for the
  local one.** A blank model fell back to the bundled llama-swap default,
  so choosing OpenAI and leaving the model empty produced a baffling
  `404 — the model 'qwen3.6-27b-abliterated' does not exist`, naming a
  model the user had never selected. A blank model means "let the endpoint
  choose", which only llama-swap can do; everywhere else it is now a
  configuration error naming the feature and pointing at Settings.

  The same fallback in Stage 3 referenced an identifier that was never
  imported, so clearing its model raised `NameError` instead. Both go
  through one checked helper now.

- The Settings model field no longer looks filled when it is empty. Its
  placeholder read `e.g. gpt-4.1-mini` in the same monospace as a real
  value — which is how the empty model got saved in the first place. A
  provider that needs a model now shows a required state and blocks Save,
  and switching provider prefills that provider's default.

## 0.8.2 — 2026-08-02

### Fixed
- Saving a provider change now refreshes the splitter panel straight away.
  It previously waited for that panel's 15-second status poll — and since
  the panel is unmounted while you're on Settings, the 30-second global
  `staleTime` meant navigating back to Builder could still render the old
  endpoint. The save now invalidates the status query with
  `refetchType: "all"`, so the cache is warm before you get there.

## 0.8.1 — 2026-08-02

### Fixed
- **The splitter panel ignored a remote endpoint.** Its model dropdown was
  always populated from the local llama-swap server and sent that name
  explicitly, which overrode the configured model — so selecting OpenAI in
  Settings still ran the local Qwen. The panel now reports the resolved
  target, shows the configured model read-only, and hides the
  llama-swap-only controls (start, unload, idle TTL) when pointed
  elsewhere.
- **Testing the splitter demanded a package it never uses.** The test
  routed through the OpenAI SDK and failed with "Stage 3 (openai) needs
  the `[llm]` extras", even though the splitter speaks plain HTTP. It now
  tests over the same path it actually uses.
- `openai` and `anthropic` moved into `backend/requirements.txt`. They
  were optional extras that `scripts/dev.bat` never installed, so Stage 3
  classification failed on a fresh clone despite being the documented
  default.

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

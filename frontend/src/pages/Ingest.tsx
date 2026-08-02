import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Play, RefreshCw, Eye } from "lucide-react";

import { api, type PreviewResponse, type TagBudget } from "@/lib/api";
import { Panel } from "@/components/Panel";
import { BucketBadge } from "@/components/BucketBadge";
import { PresetPicker } from "@/components/PresetPicker";
import {
  Checkbox,
  CopyButton,
  Field,
  ratingPillClass,
} from "@/components/forms";
import { jobStore } from "@/lib/jobStore";

const PREVIEW_BUCKET_ORDER = [
  "outfit",
  "pose",
  "expression",
  "background",
  "composition",
  "accessory",
  "extras",
  "scene",
];

export function Ingest() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Ingest</h1>
        <p className="text-sm text-text-muted">
          Pull prompts from your local metadata dump or scrape Danbooru / AIBooru.
        </p>
      </div>
      <MetadataIngestCard />
      <BooruFetchCard />
    </div>
  );
}

function MetadataIngestCard() {
  const qc = useQueryClient();
  const defaults = useQuery({
    queryKey: ["ingest", "metadata", "defaults"],
    queryFn: api.defaultMetadata,
  });

  const [path, setPath] = useState("");
  const [label, setLabel] = useState("");
  const [dropArtist, setDropArtist] = useState(true);
  const [dropQuality, setDropQuality] = useState(true);
  const [dropCharacter, setDropCharacter] = useState(false);
  const [classifyAfter, setClassifyAfter] = useState(true);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);

  // Prefill the default path once — never refill after the user clears the
  // field to type their own path.
  const defaultApplied = useRef(false);
  useEffect(() => {
    if (defaultApplied.current) return;
    if (defaults.data?.exists && !path) {
      defaultApplied.current = true;
      setPath(defaults.data.path);
    }
  }, [defaults.data, path]);

  const previewMutation = useMutation({
    mutationFn: () => api.previewMetadata(path, 20),
    onSuccess: (data) => {
      setPreview(data);
      toast.success(`parsed ${data.samples.length} sample records`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const startMutation = useMutation({
    mutationFn: () =>
      api.ingestMetadata({
        path,
        label: label || undefined,
        drop_artist_tags: dropArtist,
        drop_quality_tags: dropQuality,
        drop_character_tags: dropCharacter,
        classify_after: classifyAfter,
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success(`Started job #${job_id}`);
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <Panel
      title="Import metadata.txt"
      description="Stream-parse the user's NovelAI / SD prompt dump and populate the DB."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (path && !previewMutation.isPending) previewMutation.mutate();
        }}
      >
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <label className="pf-label" htmlFor="md-path">
            Metadata file path
          </label>
          <input
            id="md-path"
            className="pf-input mt-1 font-mono text-xs"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder={defaults.data?.path}
          />
          {defaults.data?.exists ? (
            <p className="mt-1 text-[11px] text-text-subtle">
              default exists — {(defaults.data.size_bytes / 1e6).toFixed(1)} MB
            </p>
          ) : (
            <p className="mt-1 text-[11px] text-accent-amber">
              default path not found; paste an explicit path
            </p>
          )}
        </div>
        <div>
          <label className="pf-label" htmlFor="md-label">
            Label (optional)
          </label>
          <input
            id="md-label"
            className="pf-input mt-1"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. May2026 pixiv batch"
          />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-4 text-sm">
        <Checkbox
          checked={dropArtist}
          onChange={setDropArtist}
          label="Drop artist tags"
        />
        <Checkbox
          checked={dropQuality}
          onChange={setDropQuality}
          label="Drop quality / boilerplate tags"
        />
        <Checkbox
          checked={dropCharacter}
          onChange={setDropCharacter}
          label="Drop character tags (keep for per-image coherence)"
        />
        <Checkbox
          checked={classifyAfter}
          onChange={setClassifyAfter}
          label="Classify new tags after ingest (Stage 1 rules + GPT-4o-mini) and rebuild scenes"
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="submit"
          className="pf-btn"
          disabled={!path || previewMutation.isPending}
        >
          <Eye size={14} />
          {previewMutation.isPending ? "Parsing…" : "Preview first 20"}
        </button>
        <button
          type="button"
          className="pf-btn-primary"
          disabled={!path || startMutation.isPending}
          onClick={() => startMutation.mutate()}
        >
          <Play size={14} />
          {startMutation.isPending ? "Starting…" : "Start ingest"}
        </button>
      </div>

      {preview && <PreviewBlock data={preview} />}
      </form>
    </Panel>
  );
}

function PreviewBlock({ data }: { data: PreviewResponse }) {
  return (
    <div className="mt-4 rounded-md border border-line bg-bg-subtle/60 p-3">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs text-text-muted">
        <span>
          {data.samples.length} preview records · {(data.size_bytes / 1e6).toFixed(1)} MB file
        </span>
        <span className="text-[11px] text-text-subtle">
          buckets below = what the ingest will write to wildcard files
        </span>
      </div>
      <ul className="max-h-[28rem] space-y-2 overflow-y-auto pr-2 text-xs">
        {data.samples.map((s) => {
          const orderedBuckets = PREVIEW_BUCKET_ORDER.filter(
            (b) => s.buckets[b]?.length,
          );
          return (
            <li
              key={s.filename}
              className="rounded border border-line/60 bg-bg p-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono">{s.filename}</span>
                <span className="flex items-center gap-2 font-mono text-[10px] text-text-subtle">
                  <span
                    className={`pf-pill h-5 ${ratingPillClass(
                      s.inferred_rating,
                    )}`}
                    title={
                      s.rating_evidence.length
                        ? `evidence: ${s.rating_evidence.join(", ")}`
                        : "no explicit/sensitive tags found"
                    }
                  >
                    rating {s.inferred_rating}
                  </span>
                  {s.tag_count} parsed · {s.dropped_count} dropped ·{" "}
                  {s.software ?? "—"}
                  {s.nai_model ? ` · ${s.nai_model}` : ""}
                </span>
              </div>

              <div className="mt-1 flex items-center gap-1">
                <span
                  className="min-w-0 flex-1 truncate font-mono text-[10px] text-text-subtle"
                  title={s.raw_prompt_excerpt}
                >
                  {s.raw_prompt_excerpt}
                </span>
                <CopyButton
                  text={s.raw_prompt_excerpt}
                  title="Copy raw prompt"
                  className="shrink-0"
                />
              </div>

              {orderedBuckets.length > 0 ? (
                <div className="mt-2 space-y-1">
                  {orderedBuckets.map((b) => (
                    <div key={b} className="flex items-start gap-2">
                      <BucketBadge bucket={b} className="mt-0.5 shrink-0" />
                      <span className="font-mono text-[11px] text-text">
                        {s.buckets[b].join(", ")}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-1 text-[11px] text-text-subtle">
                  no bucketed tags found — all {s.tag_count} tokens fell into
                  &ldquo;other&rdquo; (character / body / quality descriptors)
                </div>
              )}

              <details className="mt-2">
                <summary className="cursor-pointer text-[10px] text-text-subtle hover:text-text">
                  raw tokens ({s.tag_count})
                </summary>
                <div className="mt-1 font-mono text-[10px] text-text-muted">
                  {s.canonical_tags.slice(0, 60).join(", ")}
                  {s.canonical_tags.length > 60 && " …"}
                </div>
              </details>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function BooruFetchCard() {
  const qc = useQueryClient();
  const [site, setSite] = useState("danbooru");
  const [mode, setMode] = useState("popular");
  const [date, setDate] = useState("");
  const [scale, setScale] = useState("day");
  const [dateMin, setDateMin] = useState("");
  const [dateMax, setDateMax] = useState("");
  const [rating, setRating] = useState("s");
  const [scoreMin, setScoreMin] = useState<number | "">(20);
  const [tags, setTags] = useState("");
  const [limit, setLimit] = useState(200);
  const [pages, setPages] = useState(1);
  const [recentDays, setRecentDays] = useState(7);
  const [baselineDays, setBaselineDays] = useState(30);
  const [login, setLogin] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [classifyAfter, setClassifyAfter] = useState(true);

  // Estimate the tag budget for the constructed query (for tag_search + rank modes)
  const constructedTags = useMemo(() => {
    const parts: string[] = [];
    if (mode === "rank") parts.push("order:rank");
    if (mode === "score") parts.push("order:score");
    if (mode === "tag_search") {
      tags
        .split(/[\s,]+/)
        .filter(Boolean)
        .forEach((t) => parts.push(t));
    }
    if (rating) parts.push(`rating:${rating}`);
    if (dateMin) parts.push(`date:>=${dateMin}`);
    if (typeof scoreMin === "number") parts.push(`score:>${scoreMin}`);
    return parts.join(" ");
  }, [mode, tags, rating, dateMin, scoreMin]);

  const budget = useQuery({
    queryKey: ["danbooru", "budget", constructedTags],
    queryFn: () => api.estimateTagBudget(constructedTags),
    enabled: !!constructedTags,
  });

  const fetchMut = useMutation({
    mutationFn: () =>
      api.fetchBooru({
        site,
        mode,
        date: date || undefined,
        scale,
        date_min: dateMin || undefined,
        date_max: dateMax || undefined,
        rating: rating || undefined,
        score_min: typeof scoreMin === "number" ? scoreMin : undefined,
        tags: tags
          .split(/[\s,]+/)
          .map((t) => t.trim())
          .filter(Boolean),
        limit,
        pages,
        recent_days: recentDays,
        baseline_days: baselineDays,
        login: login || undefined,
        api_key: apiKey || undefined,
        classify_after: classifyAfter,
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success(`Started ${site}:${mode} fetch (job #${job_id})`);
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <Panel
      title="Fetch from Booru"
      description="Pull tag-only metadata from Danbooru or AIBooru. Rate-limited and anon-safe by default."
      actions={
        <PresetPicker
          kind="fetch"
          getSnapshot={() => ({
            site,
            mode,
            date,
            scale,
            dateMin,
            dateMax,
            rating,
            scoreMin,
            tags,
            limit,
            pages,
            recentDays,
            baselineDays,
            classifyAfter,
          })}
          applySnapshot={(data) => {
            if (typeof data.site === "string") setSite(data.site);
            if (typeof data.mode === "string") setMode(data.mode);
            if (typeof data.date === "string") setDate(data.date);
            if (typeof data.scale === "string") setScale(data.scale);
            // Reset when absent: pre-range presets must not inherit a stale
            // date range and turn a single fetch into a multi-day backfill.
            setDateMin(typeof data.dateMin === "string" ? data.dateMin : "");
            setDateMax(typeof data.dateMax === "string" ? data.dateMax : "");
            if (typeof data.rating === "string") setRating(data.rating);
            if (typeof data.scoreMin === "number" || data.scoreMin === "")
              setScoreMin(data.scoreMin);
            if (typeof data.tags === "string") setTags(data.tags);
            if (typeof data.limit === "number") setLimit(data.limit);
            if (typeof data.pages === "number") setPages(data.pages);
            if (typeof data.recentDays === "number")
              setRecentDays(data.recentDays);
            if (typeof data.baselineDays === "number")
              setBaselineDays(data.baselineDays);
            setClassifyAfter(
              typeof data.classifyAfter === "boolean"
                ? data.classifyAfter
                : true,
            );
          }}
        />
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!fetchMut.isPending) fetchMut.mutate();
        }}
      >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Field label="Site">
          <select
            className="pf-input"
            value={site}
            onChange={(e) => setSite(e.target.value)}
          >
            <option value="danbooru">danbooru.donmai.us</option>
            <option value="aibooru">aibooru.online</option>
            <option value="safebooru-donmai">safebooru.donmai.us</option>
          </select>
        </Field>
        <Field label="Mode">
          <select
            className="pf-input"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="popular">popular (explore endpoint)</option>
            <option value="rank">order:rank</option>
            <option value="score">order:score</option>
            <option value="tag_search">tag search</option>
            <option value="trending">trending (delta vs baseline)</option>
          </select>
        </Field>
        <Field label="Rating">
          <select
            className="pf-input"
            value={rating}
            onChange={(e) => setRating(e.target.value)}
          >
            <option value="">any</option>
            <option value="g">g — general</option>
            <option value="s">s — sensitive</option>
            <option value="q">q — questionable</option>
            <option value="e">e — explicit</option>
          </select>
        </Field>
        <Field label="Score min (>=)">
          <input
            type="number"
            className="pf-input"
            value={scoreMin}
            onChange={(e) =>
              setScoreMin(e.target.value === "" ? "" : Number(e.target.value))
            }
          />
        </Field>

        {mode === "popular" && (
          <>
            <Field label="Date">
              <input
                type="date"
                className="pf-input"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </Field>
            <Field label="Scale">
              <select
                className="pf-input"
                value={scale}
                onChange={(e) => setScale(e.target.value)}
              >
                <option value="day">day</option>
                <option value="week">week</option>
                <option value="month">month</option>
              </select>
            </Field>
          </>
        )}

        {(mode === "popular" ||
          mode === "rank" ||
          mode === "score" ||
          mode === "tag_search") && (
          <>
            <Field label="Date min">
              <input
                type="date"
                className="pf-input"
                value={dateMin}
                onChange={(e) => setDateMin(e.target.value)}
              />
              {(mode === "popular" || mode === "rank") && (
                <p className="mt-1 text-[11px] text-text-subtle">
                  (both set = fetch each day in the range)
                </p>
              )}
            </Field>
            <Field label="Date max">
              <input
                type="date"
                className="pf-input"
                value={dateMax}
                onChange={(e) => setDateMax(e.target.value)}
              />
            </Field>
          </>
        )}

        {mode === "tag_search" && (
          <Field label="Tags (space or comma separated)" className="col-span-2 lg:col-span-4">
            <input
              className="pf-input font-mono"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="serafuku indoor"
            />
          </Field>
        )}

        {mode === "trending" && (
          <>
            <Field label="Recent window (days)">
              <input
                type="number"
                className="pf-input"
                value={recentDays}
                onChange={(e) => setRecentDays(Number(e.target.value))}
              />
            </Field>
            <Field label="Baseline window (days)">
              <input
                type="number"
                className="pf-input"
                value={baselineDays}
                onChange={(e) => setBaselineDays(Number(e.target.value))}
              />
            </Field>
          </>
        )}

        <Field label="Limit / page">
          <input
            type="number"
            className="pf-input"
            value={limit}
            min={1}
            max={200}
            onChange={(e) => setLimit(Number(e.target.value))}
          />
        </Field>
        <Field label="Pages">
          <input
            type="number"
            className="pf-input"
            value={pages}
            min={1}
            max={50}
            onChange={(e) => setPages(Number(e.target.value))}
          />
        </Field>

        <Field label="Login (optional)">
          <input
            className="pf-input font-mono"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            placeholder="leave blank for anonymous"
          />
        </Field>
        <Field label="API key (optional)">
          <input
            className="pf-input font-mono"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </Field>
      </div>

      {budget.data && (
        <BudgetWarning budget={budget.data} hasCredentials={!!(login && apiKey)} />
      )}

      <div className="mt-4">
        <Checkbox
          checked={classifyAfter}
          onChange={setClassifyAfter}
          label="Classify new tags after ingest (Stage 1 rules + GPT-4o-mini) and rebuild scenes"
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="submit"
          className="pf-btn-primary"
          disabled={fetchMut.isPending}
        >
          <RefreshCw size={14} className={fetchMut.isPending ? "animate-spin" : ""} />
          {fetchMut.isPending ? "Submitting…" : "Start fetch"}
        </button>
        <span className="font-mono text-[11px] text-text-subtle">
          query: {constructedTags || "(empty)"}
        </span>
      </div>
      </form>
    </Panel>
  );
}

function BudgetWarning({
  budget,
  hasCredentials,
}: {
  budget: TagBudget;
  hasCredentials: boolean;
}) {
  if (budget.anon_ok) {
    return (
      <div className="mt-3 rounded-md border border-accent-green/30 bg-accent-green/10 px-3 py-2 text-xs text-accent-green">
        {budget.paid} paid · {budget.free} free metatags — works anonymously.
      </div>
    );
  }
  const tone = budget.gold_ok
    ? "border-accent-amber/30 bg-accent-amber/10 text-accent-amber"
    : "border-accent-rose/30 bg-accent-rose/10 text-accent-rose";
  return (
    <div className={`mt-3 rounded-md border px-3 py-2 text-xs ${tone}`}>
      {budget.paid} paid tag{budget.paid === 1 ? "" : "s"} — anon limit is 2.
      {!hasCredentials && " Fill in login + api key to authenticate."} {budget.message}
    </div>
  );
}

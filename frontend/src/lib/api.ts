/* Typed fetch helpers + shared types. */

const BASE = "/api";

export interface DashboardSummary {
  image_count: number;
  tag_count: number;
  source_count: number;
  scene_count: number;
  classifier_coverage: number;
  by_source: { kind: string; count: number }[];
  by_bucket: { bucket: string; count: number }[];
  scene_by_bucket: { bucket: string; count: number }[];
  recent_jobs: JobSummary[];
  default_metadata_path: string;
  default_wildcards_dir: string;
  tags_jsonl_exists: boolean;
}

export interface JobSummary {
  id: number;
  kind: string;
  label: string;
  status: "pending" | "running" | "done" | "error" | "cancelled";
  progress: number;
  message: string;
  error?: string | null;
  detail?: unknown;
  created_at?: string;
  updated_at?: string;
  finished_at?: string | null;
}

export interface SceneRow {
  id: number;
  source_id: number;
  external_id: string;
  rating: string | null;
  rating_source: string | null;
  score: number | null;
  fav_count: number | null;
  nai_model: string | null;
  software: string | null;
  width: number | null;
  height: number | null;
  buckets: Record<string, string>;
}

export interface SceneDetail extends SceneRow {
  origin?: "local" | "booru" | "other";
  subjects?: string;
  raw_prompt: string;
  raw_negative: string | null;
  rating_evidence: string[];
  tags: { name: string; bucket: string; bucket_source: string; order: number }[];
}

export interface SceneListResponse {
  total: number;
  items: SceneRow[];
}

export interface SourceRow {
  id: number;
  kind: string;
  origin: "local" | "booru" | "other";
  label: string;
  fetched_at: string;
  image_count: number;
  note: string | null;
}

export interface TagRow {
  id: number;
  name: string;
  bucket: string;
  bucket_source: string;
  confidence: number;
  post_count: number;
  category: number;
  locked: boolean;
  usage: number;
}

export interface TagListResponse {
  total: number;
  items: TagRow[];
}

export interface ClassifyQueueStats {
  total_tags: number;
  other_unlocked: number;
  stage2: { pending: number; description: string };
  stage3: {
    pending_total: number;
    would_process: number;
    uncached: number;
    cached_in_batch: number;
    max_tags_cap: number | null;
    description: string;
  };
  stage1: {
    eligible: number;
    candidates_scanned: number;
    replace_below_confidence: number;
    touch_other: boolean;
  };
  stage2_reset: { pending: number; below_confidence: number };
  ratings: { pending: number; only_missing: boolean };
}

export interface TagHistoryRow {
  id: number;
  tag_id: number;
  tag_name: string;
  from_bucket: string | null;
  from_source: string | null;
  from_confidence: number | null;
  to_bucket: string;
  to_source: string; // 'embed' | 'llm' | 'manual' | 'backfill'
  to_confidence: number;
  model: string | null;
  job_id: number | null;
  at: string;
}

export interface TagHistoryResponse {
  total: number;
  items: TagHistoryRow[];
}

export interface TagHistoryStats {
  items: {
    from_bucket: string | null;
    to_bucket: string;
    count: number;
    avg_confidence: number | null;
  }[];
}

export interface PreviewResponse {
  path: string;
  size_bytes: number;
  samples: {
    filename: string;
    software: string | null;
    nai_model: string | null;
    width: number | null;
    height: number | null;
    raw_prompt_excerpt: string;
    canonical_tags: string[];
    tag_count: number;
    artist_tag_count: number;
    buckets: Record<string, string[]>;
    dropped_count: number;
    inferred_rating: string;
    rating_evidence: string[];
  }[];
}

export interface ExportManifest {
  name: string;
  created_at: string;
  output_dir: string;
  files: Record<string, string>;
  line_counts: Record<string, number>;
  filters: Record<string, unknown>;
  warnings?: string[];
}

export interface TagBudget {
  paid: number;
  free: number;
  anon_ok: boolean;
  gold_ok: boolean;
  message: string;
}

export interface TrendItem {
  tag_id: number;
  name: string;
  bucket: string;
  recent: number;
  baseline: number;
  ratio: number;
}

export interface TrendResponse {
  recent_days: number;
  baseline_days: number;
  recent_start: string;
  baseline_start: string;
  items: TrendItem[];
}

export interface DenyListRow {
  id: number;
  name: string;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface PresetRow {
  id: number;
  kind: string;
  name: string;
  data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface BackupMirrorResult {
  dir: string;
  ok: boolean;
  error?: string;
}

export interface BackupRow {
  name: string;
  size_bytes: number;
  created_at: string;
  mirrors?: BackupMirrorResult[];
}

export interface AutoBackupResult extends Partial<BackupRow> {
  backed_up: boolean;
  last_backup_at?: string;
  next_due_at?: string;
}

export interface BackupConfig {
  mirror_dirs: string[];
  auto_days: number;
}

export interface FeatureLogRow {
  id: number;
  character: string;
  channel: "x" | "patreon";
  at: string;
}

/** Sample of what a folder of images would yield, before committing to a run. */
export interface FolderPreview {
  path: string;
  total_images: number;
  sampled: number;
  with_metadata: number;
  samples: {
    filename: string;
    has_metadata: boolean;
    software: string | null;
    nai_model: string | null;
    prompt: string;
  }[];
}

export interface RolledScene {
  buckets: Record<string, string>;
  image?: {
    id: number;
    external_id: string;
    rating: string | null;
    score: number | null;
    origin?: "local" | "booru" | "other";
    subjects?: string;
  } | null;
}

export interface LlmBatchRow {
  id: number;
  openai_batch_id: string;
  status: string;
  model: string;
  tag_count: number;
  request_count: number;
  submitted_at: string;
  completed_at: string | null;
  applied: boolean;
  error: string | null;
}

/* ---- Decompose (See-through layer decomposition) ---- */

export interface DecompParams {
  pipeline: "full" | "quantized" | "blockswap";
  resolution: number;
  resolution_depth: number;
  inference_steps: number;
  inference_steps_depth: number;
  seed: number;
  tblr_split: boolean;
  group_offload: boolean;
  gpu: number;
}

export interface DecompItemRow {
  id: number;
  original_name: string;
  input_path: string;
  params: Partial<DecompParams>;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number;
  message: string;
  error: string | null;
  has_psd: boolean;
  has_depth_psd: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DecompLayer {
  name: string;
  file: string;
}

export interface DecompItemDetail extends DecompItemRow {
  layers: DecompLayer[];
  has_reconstruction: boolean;
  has_src: boolean;
}

export interface DecompConfig {
  python_path: string;
  repo_dir: string;
  layerdiff_dir: string;
  depth_dir: string;
}

export interface DecompStatus {
  defaults: DecompParams;
  python_ok: boolean;
  repo_ok: boolean;
  layerdiff_ok: boolean;
  depth_ok: boolean;
  queued: number;
  running_item_id: number | null;
  config: DecompConfig;
}

export interface DecompRepoStatus {
  ok: boolean;
  error?: string;
  commit?: string;
  branch?: string;
  behind?: number | null;
  fetched?: boolean;
  fetch_error?: string | null;
  checked_at?: string;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
  }
  return (await res.json()) as T;
}

export const api = {
  dashboard: () => request<DashboardSummary>("/dashboard/summary"),

  listJobs: () => request<JobSummary[]>("/jobs"),
  getJob: (id: number) => request<JobSummary>(`/jobs/${id}`),

  listSources: () => request<SourceRow[]>("/scenes/sources"),

  listScenes: (
    params: Record<string, string | number | boolean | undefined | null>,
  ) => {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "" || v === false) continue;
      sp.set(k, String(v));
    }
    const q = sp.toString();
    return request<SceneListResponse>(`/scenes${q ? `?${q}` : ""}`);
  },
  getScene: (id: number) => request<SceneDetail>(`/scenes/${id}`),

  listTags: (params: Record<string, string | number | undefined>) => {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === "") continue;
      sp.set(k, String(v));
    }
    const q = sp.toString();
    return request<TagListResponse>(`/tags${q ? `?${q}` : ""}`);
  },
  listBuckets: () => request<string[]>("/tags/buckets"),
  setTagBucket: (
    name: string,
    bucket: string,
    opts?: { note?: string; lock?: boolean },
  ) =>
    request<{ ok: boolean }>(`/tags/${encodeURIComponent(name)}/bucket`, {
      method: "POST",
      body: JSON.stringify({
        bucket,
        note: opts?.note,
        lock: opts?.lock ?? true,
      }),
    }),

  listTagHistory: (
    params: Record<string, string | number | undefined>,
  ) => {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === "") continue;
      sp.set(k, String(v));
    }
    const q = sp.toString();
    return request<TagHistoryResponse>(`/tags/history${q ? `?${q}` : ""}`);
  },
  tagHistoryStats: (params: Record<string, string | number | undefined>) => {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === "") continue;
      sp.set(k, String(v));
    }
    const q = sp.toString();
    return request<TagHistoryStats>(`/tags/history/stats${q ? `?${q}` : ""}`);
  },
  revertTagHistory: (id: number, lock = true, note?: string) =>
    request<{ ok: boolean }>(`/tags/history/${id}/revert`, {
      method: "POST",
      body: JSON.stringify({ lock, note }),
    }),
  backfillTagHistory: (sources: string[] = ["embed", "llm"]) =>
    request<{ inserted: number; skipped: number }>("/tags/history/backfill", {
      method: "POST",
      body: JSON.stringify({ sources }),
    }),

  previewMetadata: (path: string, sample_size = 20) =>
    request<PreviewResponse>("/ingest/metadata/preview", {
      method: "POST",
      body: JSON.stringify({ path, sample_size }),
    }),

  ingestMetadata: (body: {
    path: string;
    label?: string;
    drop_artist_tags?: boolean;
    drop_quality_tags?: boolean;
    drop_character_tags?: boolean;
    classify_after?: boolean;
  }) =>
    request<{ job_id: number }>("/ingest/metadata/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  defaultMetadata: () =>
    request<{ path: string; exists: boolean; size_bytes: number }>(
      "/ingest/metadata/defaults",
    ),

  previewImageFolder: (path: string, recursive = true, sample_size = 12) =>
    request<FolderPreview>("/ingest/images/preview", {
      method: "POST",
      body: JSON.stringify({ path, recursive, sample_size }),
    }),

  ingestImageFolder: (body: {
    path: string;
    label?: string;
    recursive?: boolean;
    drop_artist_tags?: boolean;
    drop_quality_tags?: boolean;
    drop_character_tags?: boolean;
    classify_after?: boolean;
  }) =>
    request<{ job_id: number }>("/ingest/images/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  runExport: (body: {
    name: string;
    output_dir?: string;
    source_ids?: number[];
    origin?: string | null;
    ratings?: string[];
    nai_models?: string[];
    score_min?: number;
    max_rating?: string | null;
    buckets?: string[];
    min_tag_count?: number;
    deduplicate?: boolean;
    dedupe_ignore_order?: boolean;
    file_prefix?: string;
    use_default_deny?: boolean;
    extra_deny_tags?: string[];
    scene_buckets?: string[];
    cap_tags?: string[];
    cap_percent?: number;
    mirror_dir?: string;
  }) =>
    // Export now runs as a tracked background job; the manifest arrives in
    // the job's detail once it finishes.
    request<{ job_id: number }>("/export/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  exportPresetDirs: () => request<Record<string, string>>("/export/preset-dirs"),
  defaultDenyTags: () =>
    request<{ tags: string[] }>("/export/default-deny-tags"),

  fetchBooru: (body: Record<string, unknown>) =>
    request<{ job_id: number }>("/danbooru/fetch", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  estimateTagBudget: (tags: string) =>
    request<TagBudget>(`/danbooru/estimate-tag-budget?tags=${encodeURIComponent(tags)}`),

  trendDelta: (params: {
    recent_days: number;
    baseline_days: number;
    bucket?: string;
    booru_only?: boolean;
    source_id?: number;
    compare_offset_days?: number;
    limit?: number;
  }) => {
    const sp = new URLSearchParams({
      recent_days: String(params.recent_days),
      baseline_days: String(params.baseline_days),
    });
    if (params.bucket) sp.set("bucket", params.bucket);
    if (params.booru_only) sp.set("booru_only", "true");
    if (params.source_id) sp.set("source_id", String(params.source_id));
    if (params.compare_offset_days)
      sp.set("compare_offset_days", String(params.compare_offset_days));
    if (params.limit) sp.set("limit", String(params.limit));
    return request<TrendResponse>(`/trends/delta?${sp.toString()}`);
  },

  trendExportUrl: (params: {
    recent_days: number;
    baseline_days: number;
    bucket?: string;
    booru_only?: boolean;
    source_id?: number;
    compare_offset_days?: number;
    top: number;
  }) => {
    const sp = new URLSearchParams({
      recent_days: String(params.recent_days),
      baseline_days: String(params.baseline_days),
      top: String(params.top),
    });
    if (params.bucket) sp.set("bucket", params.bucket);
    if (params.booru_only) sp.set("booru_only", "true");
    if (params.source_id) sp.set("source_id", String(params.source_id));
    if (params.compare_offset_days)
      sp.set("compare_offset_days", String(params.compare_offset_days));
    return `/api/trends/export?${sp.toString()}`;
  },

  listPresets: (kind: string) =>
    request<PresetRow[]>(`/presets?kind=${encodeURIComponent(kind)}`),
  createPreset: (kind: string, name: string, data: Record<string, unknown>) =>
    request<PresetRow>("/presets", {
      method: "POST",
      body: JSON.stringify({ kind, name, data }),
    }),
  updatePreset: (id: number, data: Record<string, unknown>, name?: string) =>
    request<PresetRow>(`/presets/${id}`, {
      method: "PUT",
      body: JSON.stringify({ name, data }),
    }),
  deletePreset: (id: number) =>
    request<{ ok: boolean }>(`/presets/${id}`, { method: "DELETE" }),

  runBackup: () =>
    request<BackupRow>("/admin/backup", { method: "POST" }),
  autoBackup: () =>
    request<AutoBackupResult>("/admin/backup/auto", { method: "POST" }),
  getBackupConfig: () => request<BackupConfig>("/admin/backup-config"),
  putBackupConfig: (mirror_dirs: string[]) =>
    request<BackupConfig>("/admin/backup-config", {
      method: "PUT",
      body: JSON.stringify({ mirror_dirs }),
    }),
  listBackups: () => request<BackupRow[]>("/admin/backups"),
  deleteBackup: (name: string) =>
    request<{ ok: boolean }>(`/admin/backups/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  listFeatureLog: () => request<FeatureLogRow[]>("/trends/feature-log"),
  logFeature: (character: string, channel: "x" | "patreon") =>
    request<FeatureLogRow>("/trends/feature-log", {
      method: "POST",
      body: JSON.stringify({ character, channel }),
    }),
  deleteFeatureLog: (id: number) =>
    request<{ ok: boolean }>(`/trends/feature-log/${id}`, {
      method: "DELETE",
    }),

  listLlmBatches: () => request<LlmBatchRow[]>("/classify/llm-batches"),
  applyLlmBatch: (id: number) =>
    request<{ job_id: number }>(`/classify/llm-batches/${id}/apply`, {
      method: "POST",
    }),

  listDenyLists: () => request<DenyListRow[]>("/export/deny-lists"),
  createDenyList: (name: string, tags: string[]) =>
    request<DenyListRow>("/export/deny-lists", {
      method: "POST",
      body: JSON.stringify({ name, tags }),
    }),
  updateDenyList: (id: number, tags: string[], name?: string) =>
    request<DenyListRow>(`/export/deny-lists/${id}`, {
      method: "PUT",
      body: JSON.stringify({ name, tags }),
    }),
  deleteDenyList: (id: number) =>
    request<{ ok: boolean }>(`/export/deny-lists/${id}`, { method: "DELETE" }),

  rollBuilder: (body: {
    buckets: string[];
    locked?: Record<string, string>;
    source_ids?: number[];
    origin?: string | null;
    ratings?: string[];
    nai_models?: string[];
    score_min?: number;
    coherent?: boolean;
    cap_tags?: string[];
    cap_percent?: number;
    exclude_tags?: string[];
    require_tags?: string[];
    multi_char?: boolean;
    subjects?: string;
    count?: number;
  }) =>
    request<{
      buckets: Record<string, string>;
      image?: RolledScene["image"];
      // Whole batch when count > 1 (first entry mirrors buckets/image).
      rolls?: RolledScene[];
    }>("/builder/roll", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  classifyQueue: (params: {
    replace_below_confidence?: number;
    reset_below_confidence?: number;
    touch_other?: boolean;
    max_tags?: number;
    rating_only_missing?: boolean;
  } = {}) => {
    const q = new URLSearchParams();
    if (params.replace_below_confidence != null)
      q.set("replace_below_confidence", String(params.replace_below_confidence));
    if (params.reset_below_confidence != null)
      q.set("reset_below_confidence", String(params.reset_below_confidence));
    if (params.touch_other != null)
      q.set("touch_other", String(params.touch_other));
    if (params.max_tags != null) q.set("max_tags", String(params.max_tags));
    if (params.rating_only_missing != null)
      q.set("rating_only_missing", String(params.rating_only_missing));
    const qs = q.toString();
    return request<ClassifyQueueStats>(
      `/classify/queue${qs ? `?${qs}` : ""}`,
    );
  },

  classifyStage2: (body: {
    model_name?: string;
    threshold?: number;
    batch_size?: number;
    rebuild_scenes?: boolean;
    device?: string;
  }) =>
    request<{ job_id: number }>("/classify/stage2", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  classifyStage2Reset: (body: {
    below_confidence?: number;
    rebuild_scenes?: boolean;
  } = {}) =>
    request<{ job_id: number }>("/classify/stage2/reset", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  classifyStage1Reclassify: (body: {
    replace_below_confidence?: number;
    touch_other?: boolean;
    rebuild_scenes?: boolean;
  } = {}) =>
    request<{ job_id: number }>("/classify/stage1/reclassify", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  classifyStage3: (body: {
    provider?: string;
    model?: string;
    batch_size?: number;
    max_tags?: number;
    rebuild_scenes?: boolean;
    concurrency?: number;
    use_batch_api?: boolean;
  }) =>
    request<{ job_id: number }>("/classify/stage3", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  rebuildScenes: (body: {
    drop_character_tags?: boolean;
    booru_character_only?: boolean;
  } = {}) =>
    request<{ job_id: number }>("/classify/rebuild-scenes", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  classifyRatings: (body: {
    only_missing?: boolean;
    overwrite_inferred?: boolean;
  } = {}) =>
    request<{ job_id: number }>("/classify/ratings", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  decomposeStatus: () => request<DecompStatus>("/decompose/status"),
  decomposeConfig: () => request<DecompConfig>("/decompose/config"),
  saveDecomposeConfig: (body: Partial<DecompConfig>) =>
    request<DecompConfig>("/decompose/config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  decomposeRepoStatus: (fetch = false) =>
    request<DecompRepoStatus>(`/decompose/repo-status?fetch=${fetch}`),
  decomposeRepoUpdate: () =>
    request<{ ok: boolean; output: string; status: DecompRepoStatus }>(
      "/decompose/repo-update",
      { method: "POST" },
    ),
  decomposeUpload: async (file: File) => {
    // Multipart — bypass request() so the browser sets the boundary header.
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/decompose/upload`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
    }
    return (await res.json()) as { path: string; name: string };
  },
  decomposeQueue: (
    inputs: { path: string; name?: string }[],
    params: Partial<DecompParams>,
  ) =>
    request<{ queued: DecompItemRow[] }>("/decompose/queue", {
      method: "POST",
      body: JSON.stringify({ inputs, params }),
    }),
  decomposeItems: (limit = 100) =>
    request<{ items: DecompItemRow[] }>(`/decompose/items?limit=${limit}`),
  decomposeItem: (id: number) =>
    request<DecompItemDetail>(`/decompose/items/${id}`),
  decomposeCancel: (id: number) =>
    request<{ status: string }>(`/decompose/items/${id}/cancel`, {
      method: "POST",
    }),
  decomposeRequeue: (id: number) =>
    request<DecompItemRow>(`/decompose/items/${id}/requeue`, {
      method: "POST",
    }),
  decomposeDelete: (id: number) =>
    request<{ deleted: boolean }>(`/decompose/items/${id}`, {
      method: "DELETE",
    }),
  decomposeOpen: (id: number, target: "psd" | "depth" | "folder" | "input") =>
    request<{ opened: boolean }>(`/decompose/items/${id}/open`, {
      method: "POST",
      body: JSON.stringify({ target }),
    }),
};

/* `v` is a cache-buster: SQLite reuses ids of deleted items, so the same
 * URL can point at a different image later. Pass a per-item timestamp so
 * the browser never shows the previous item's cached bytes. */
export function decomposeInputUrl(id: number, v?: string | null): string {
  const q = v ? `?v=${encodeURIComponent(v)}` : "";
  return `${BASE}/decompose/items/${id}/input${q}`;
}

export function decomposeAssetUrl(
  id: number,
  file: string,
  v?: string | null,
): string {
  const q = v ? `?v=${encodeURIComponent(v)}` : "";
  return `${BASE}/decompose/items/${id}/asset/${encodeURIComponent(file)}${q}`;
}

export function decomposePsdUrl(id: number): string {
  return `${BASE}/decompose/items/${id}/psd`;
}

/* ---- Local LLM (llama-swap) ---- */

export interface LlmStatus {
  up: boolean;
  models: string[];
  running: { model: string | null; state: string | null }[];
  default_model: string | null;
  ttl_minutes: number | null;
  /** True when the splitter points somewhere other than the local
   *  llama-swap server — the start/unload/TTL controls do not apply. */
  remote?: boolean;
  /** Human-readable resolved endpoint, e.g. "openai:gpt-4.1-mini". */
  target?: string;
}

/** Speech-bubble handling: let the model decide, or force it on/off. */
export type BubbleMode = "auto" | "on" | "off";
/** How in-image text is anchored. "attributed" writes `she says "…"`, which
 *  lets the image model place the text beside the speaker on its own. */
export type TextPosition = "attributed" | "placed" | "free";

export interface NaiSplitResult {
  base_prompt: string;
  base_tags: string;
  scene_description: string;
  dialogue: string;
  characters: { name: string; prompt: string }[];
  model: string;
  mode: string;
  include_speech: boolean;
  strip_identity: boolean;
  secs: number;
}

export type LlmKind =
  | "openai"
  | "openai_compatible"
  | "anthropic"
  | "local"
  | "echo";

export interface LlmFeatureConfig {
  kind: LlmKind;
  base_url: string;
  model: string;
  max_concurrency: number;
  send_temperature: boolean;
}

export interface LlmConfigResponse {
  config: { stage3: LlmFeatureConfig; splitter: LlmFeatureConfig };
  /** Masked only — the key itself is never returned. */
  key_hints: { stage3: string; splitter: string };
  suggested_models: { stage3: string[]; splitter: string[] };
  kinds: LlmKind[];
  /** Model to prefill when switching provider; blank where the endpoint
   *  picks its own (local) or none is universal (gateways). */
  default_models: Record<LlmKind, string>;
  /** Kinds each feature can actually dispatch — the splitter speaks the
   *  OpenAI chat-completions format, so it cannot drive Anthropic. */
  supported_kinds: { stage3: LlmKind[]; splitter: LlmKind[] };
}

export const llmApi = {
  getConfig: () => request<LlmConfigResponse>("/llm/config"),
  putConfig: (body: {
    stage3?: Partial<LlmFeatureConfig> & { api_key?: string };
    splitter?: Partial<LlmFeatureConfig> & { api_key?: string };
  }) =>
    request<LlmConfigResponse>("/llm/config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  testConfig: (feature: "stage3" | "splitter") =>
    request<{ ok: boolean; detail: string }>("/llm/config/test", {
      method: "POST",
      body: JSON.stringify({ feature }),
    }),
  status: () => request<LlmStatus>("/llm/status"),
  start: () =>
    request<LlmStatus & { started: boolean; error?: string }>("/llm/start", {
      method: "POST",
    }),
  unload: () =>
    request<{ ok: boolean }>("/llm/unload", { method: "POST" }),
  naiSplit: (body: {
    tags: string;
    mode: "split" | "natural";
    model?: string;
    include_speech?: boolean;
    strip_identity?: boolean;
    invent_background?: boolean;
    enrich_background?: boolean;
    bubble?: BubbleMode;
    text_position?: TextPosition;
    extra_instructions?: string;
  }) =>
    request<NaiSplitResult>("/llm/nai-split", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  naiCompose: (body: { idea: string; model?: string; extra_instructions?: string }) =>
    request<NaiSplitResult>("/llm/nai-compose", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  setTtl: (minutes: number) =>
    request<{ ok: boolean; ttl_minutes: number }>("/llm/ttl", {
      method: "POST",
      body: JSON.stringify({ minutes }),
    }),
};

export function subscribeJobStream(
  jobId: number,
  onEvent: (event: JobSummary) => void,
): EventSource {
  const es = new EventSource(`${BASE}/jobs/${jobId}/stream`);
  es.addEventListener("job", (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as JobSummary;
      onEvent(data);
    } catch (err) {
      console.warn("bad job event", err);
    }
  });
  return es;
}

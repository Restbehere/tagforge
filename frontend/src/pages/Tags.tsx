import { useMemo, useState, type ReactNode } from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";
import { toast } from "sonner";
import {
  Lock,
  Sparkles,
  RefreshCcw,
  Brain,
  History,
  Undo2,
  Search,
  RotateCcw,
  Wand2,
  ClipboardList,
  ChevronRight,
  Loader2,
} from "lucide-react";

import { api, llmApi, type TagRow, type TagHistoryRow } from "@/lib/api";
import { ASSIGN_BUCKETS, bucketButtonClass } from "@/lib/buckets";
import { Panel } from "@/components/Panel";
import { BucketBadge } from "@/components/BucketBadge";
import { Checkbox, ConfirmButton, Field } from "@/components/forms";
import { invalidateTagDerived } from "@/lib/invalidate";
import { jobStore } from "@/lib/jobStore";
import { useDebouncedValue } from "@/lib/useDebouncedValue";

type TagsTab = "browse" | "review";

type TagSort = "usage" | "name" | "confidence" | "confidence_asc" | "post_count";

export function Tags() {
  const [activeTab, setActiveTab] = useState<TagsTab>("browse");
  const [search, setSearch] = useState("");
  const [bucket, setBucket] = useState("");
  const [bucketSource, setBucketSource] = useState("");
  const [minConfidence, setMinConfidence] = useState<number | "">("");
  const [sort, setSort] = useState<TagSort>("usage");
  const [limit] = useState(800);

  const presetAuditEmbed = () => {
    setBucketSource("embed");
    setBucket("");
    setSort("confidence_asc");
    setMinConfidence("");
    setSearch("");
  };

  const debouncedSearch = useDebouncedValue(search, 250);

  const buckets = useQuery({ queryKey: ["buckets"], queryFn: api.listBuckets });
  const tags = useQuery({
    queryKey: [
      "tags",
      debouncedSearch,
      bucket,
      bucketSource,
      minConfidence,
      sort,
      limit,
    ],
    queryFn: () =>
      api.listTags({
        search: debouncedSearch || undefined,
        bucket: bucket || undefined,
        bucket_source: bucketSource || undefined,
        min_confidence:
          typeof minConfidence === "number" ? minConfidence : undefined,
        sort,
        limit,
      }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">Tags</h1>
          <p className="text-sm text-text-muted">
            Curated taxonomy — inline-edit a bucket to override the classifier.
          </p>
        </div>
        {/* Tab switcher */}
        <div className="flex rounded-lg border border-line bg-bg-subtle p-0.5 text-xs">
          {(
            [
              { id: "browse" as const, label: "Browse", icon: <Search size={12} /> },
              { id: "review" as const, label: "Tag Review", icon: <ClipboardList size={12} /> },
            ] as { id: TagsTab; label: string; icon: React.ReactNode }[]
          ).map((t) => (
            <button
              key={t.id}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-colors ${
                activeTab === t.id
                  ? "bg-brand text-brand-fg"
                  : "text-text-muted hover:text-text"
              }`}
              onClick={() => setActiveTab(t.id)}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "browse" && (
        <>
          <Panel
            title="Filters"
            actions={
              <div className="flex items-center gap-2">
                <button
                  className="pf-btn h-7 text-[11px]"
                  onClick={presetAuditEmbed}
                  title="Filter source=embed and sort by lowest confidence first — the riskiest Stage-2 relabels float to the top."
                >
                  <Sparkles size={12} /> Audit Stage 2
                </button>
                <span className="text-xs text-text-muted">
                  {(tags.data?.total ?? 0).toLocaleString()} total
                </span>
              </div>
            }
          >
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Field label="Search">
                <input
                  className="pf-input font-mono"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="serafuku…"
                />
              </Field>
              <Field label="Bucket">
                <select
                  className="pf-input"
                  value={bucket}
                  onChange={(e) => setBucket(e.target.value)}
                >
                  <option value="">any</option>
                  {(buckets.data ?? []).map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Source">
                <select
                  className="pf-input"
                  value={bucketSource}
                  onChange={(e) => setBucketSource(e.target.value)}
                >
                  <option value="">any</option>
                  <option value="tag_tree">tag_tree</option>
                  <option value="dataset_category">dataset_category</option>
                  <option value="anthro_rule">anthro_rule (*_boy/_girl)</option>
                  <option value="franchise_suffix">franchise_suffix (*_(genshin)…)</option>
                  <option value="qualifier_rule">qualifier_rule (*_(cosplay/style/weapon))</option>
                  <option value="embed">embed</option>
                  <option value="llm">llm</option>
                  <option value="manual">manual</option>
                  <option value="unknown">unknown</option>
                </select>
              </Field>
              <Field label="Sort">
                <select
                  className="pf-input"
                  value={sort}
                  onChange={(e) => setSort(e.target.value as never)}
                >
                  <option value="usage">most used in corpus</option>
                  <option value="name">name</option>
                  <option value="confidence">confidence (highest first)</option>
                  <option value="confidence_asc">
                    confidence (lowest first · audit mode)
                  </option>
                  <option value="post_count">danbooru post count</option>
                </select>
              </Field>
              <Field label="Min confidence">
                <input
                  type="number"
                  step="0.05"
                  min={0}
                  max={1}
                  className="pf-input"
                  value={minConfidence}
                  onChange={(e) =>
                    setMinConfidence(
                      e.target.value === "" ? "" : Number(e.target.value),
                    )
                  }
                />
              </Field>
            </div>
          </Panel>

          <SmartClassifyCard />

          <Panel title="Tags" bodyClassName="p-0">
            {tags.data && tags.data.total > tags.data.items.length && (
              <div className="border-b border-line px-3 py-2 text-[11px] text-text-muted">
                showing {tags.data.items.length.toLocaleString()} of{" "}
                {tags.data.total.toLocaleString()} — refine filters to see the
                rest
              </div>
            )}
            <TagsTable
              rows={tags.data?.items ?? []}
              buckets={buckets.data ?? []}
              loading={tags.isPending}
            />
          </Panel>

          <RelabelHistoryPanel />
        </>
      )}

      {activeTab === "review" && <TagReviewPanel />}
    </div>
  );
}

function formatQueueCount(n: number): string {
  return n.toLocaleString();
}

function batchStatusClass(status: string): string {
  if (status === "completed") return "text-accent-green";
  if (status === "failed" || status === "expired" || status === "cancelled")
    return "text-accent-rose";
  return "text-text-muted";
}

function QueueHint({
  children,
  loading,
}: {
  children: ReactNode;
  loading?: boolean;
}) {
  return (
    <p className="mt-2 text-[11px] text-text-muted">
      {loading ? (
        <span className="text-text-subtle">counting…</span>
      ) : (
        children
      )}
    </p>
  );
}

const CLASSIFY_PANEL_OPEN_KEY = "tagforge.classifyPanelOpen";

function SmartClassifyCard() {
  const qc = useQueryClient();

  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(CLASSIFY_PANEL_OPEN_KEY) === "true";
    } catch {
      return false;
    }
  });
  const toggleOpen = () =>
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(CLASSIFY_PANEL_OPEN_KEY, String(next));
      } catch {
        /* private mode */
      }
      return next;
    });

  const [threshold, setThreshold] = useState(0.55);
  const [embedModel, setEmbedModel] = useState(
    "mixedbread-ai/mxbai-embed-large-v1",
  );
  const [embedDevice, setEmbedDevice] = useState("auto");
  // Blank = follow Settings → LLM providers. These used to default to
  // OpenAI + gpt-4o-mini, so every manual run silently overrode the
  // configured endpoint — the one thing that setting exists to control.
  const [llmProvider, setLlmProvider] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [llmMaxTags, setLlmMaxTags] = useState<number | "">(500);
  const [llmConcurrency, setLlmConcurrency] = useState(6);
  const [useBatchApi, setUseBatchApi] = useState(false);

  const llmCfgQ = useQuery({ queryKey: ["llm", "config"], queryFn: llmApi.getConfig });
  const stage3Cfg = llmCfgQ.data?.config.stage3;
  const configuredTarget = stage3Cfg
    ? `${stage3Cfg.kind}:${stage3Cfg.model || "default"}`
    : "Settings";
  // The Batch API is an OpenAI product, and the backend refuses to submit
  // while Stage 3 points anywhere else — so do not offer it there.
  const batchAvailable = (llmProvider || stage3Cfg?.kind) === "openai";

  const stage2Mut = useMutation({
    mutationFn: () =>
      api.classifyStage2({
        model_name: embedModel,
        threshold,
        rebuild_scenes: true,
        device: embedDevice,
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success("started embedding classifier");
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["classify-queue"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const stage3Mut = useMutation({
    mutationFn: () =>
      api.classifyStage3({
        // Omitted entirely when blank, so the backend resolves the
        // configured endpoint rather than being handed an override.
        provider: llmProvider || undefined,
        model: llmModel || undefined,
        max_tags: typeof llmMaxTags === "number" ? llmMaxTags : undefined,
        rebuild_scenes: true,
        concurrency: llmConcurrency,
        // Batch API is OpenAI-only; never send it for other providers.
        use_batch_api: batchAvailable && useBatchApi,
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success(`started LLM classifier (${llmProvider || configuredTarget})`);
      qc.invalidateQueries({ queryKey: ["classify-queue"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const batchesQ = useQuery({
    queryKey: ["llm-batches"],
    queryFn: api.listLlmBatches,
    refetchInterval: 30_000,
  });

  // Track applies per batch id: the POST returns immediately (background
  // job), so the mutation's isPending can't gate the button for the whole
  // apply duration. Ids stay in the set until the refetch shows applied=true.
  const [applyingBatches, setApplyingBatches] = useState<Set<number>>(
    new Set(),
  );
  const applyBatchMut = useMutation({
    mutationFn: (id: number) => api.applyLlmBatch(id),
    onMutate: (id) => {
      setApplyingBatches((prev) => new Set(prev).add(id));
    },
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success("applying batch results");
      qc.invalidateQueries({ queryKey: ["llm-batches"] });
    },
    onError: (err: Error, id) => {
      setApplyingBatches((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      toast.error(err.message);
    },
  });

  const rebuildMut = useMutation({
    mutationFn: () => api.rebuildScenes({ drop_character_tags: false }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success("rebuilding scene_line (character lines kept)");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const fixCharMut = useMutation({
    mutationFn: () => api.rebuildScenes({ booru_character_only: true }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success("rebuilding booru character lines only");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const [resetThreshold, setResetThreshold] = useState(0.65);
  const resetMut = useMutation({
    mutationFn: () =>
      api.classifyStage2Reset({
        below_confidence: resetThreshold,
        rebuild_scenes: true,
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success(
        `resetting embed relabels with conf < ${resetThreshold.toFixed(2)}`,
      );
      qc.invalidateQueries({ queryKey: ["tags"] });
      qc.invalidateQueries({ queryKey: ["tag-history"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["classify-queue"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const [reclassifyCap, setReclassifyCap] = useState(0.85);
  const [reclassifyTouchOther, setReclassifyTouchOther] = useState(true);
  const reclassifyMut = useMutation({
    mutationFn: () =>
      api.classifyStage1Reclassify({
        replace_below_confidence: reclassifyCap,
        touch_other: reclassifyTouchOther,
        rebuild_scenes: true,
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success("re-applying stage 1 rules");
      qc.invalidateQueries({ queryKey: ["tags"] });
      qc.invalidateQueries({ queryKey: ["tag-history"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["classify-queue"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const [ratingOverwrite, setRatingOverwrite] = useState(false);
  const [ratingOnlyMissing, setRatingOnlyMissing] = useState(true);

  const queueQ = useQuery({
    queryKey: [
      "classify-queue",
      threshold,
      reclassifyCap,
      reclassifyTouchOther,
      resetThreshold,
      llmMaxTags,
      ratingOnlyMissing,
    ],
    queryFn: () =>
      api.classifyQueue({
        replace_below_confidence: reclassifyCap,
        reset_below_confidence: resetThreshold,
        touch_other: reclassifyTouchOther,
        max_tags: typeof llmMaxTags === "number" ? llmMaxTags : undefined,
        rating_only_missing: ratingOnlyMissing,
      }),
    refetchInterval: 8_000,
  });

  const q = queueQ.data;

  const ratingMut = useMutation({
    mutationFn: () =>
      api.classifyRatings({
        only_missing: ratingOnlyMissing,
        overwrite_inferred: ratingOverwrite,
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success("classifying scene ratings");
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["classify-queue"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const stage3Would = q?.stage3.would_process ?? 0;
  const stage3Uncached = q?.stage3.uncached ?? 0;
  const stage3Total = q?.stage3.pending_total ?? 0;

  return (
    <Panel
      title={
        <button
          type="button"
          onClick={toggleOpen}
          aria-expanded={open}
          className="flex items-center gap-1.5"
          title={open ? "Collapse" : "Expand"}
        >
          <ChevronRight
            size={14}
            className={`text-text-muted transition-transform ${
              open ? "rotate-90" : ""
            }`}
          />
          Smart classify
        </button>
      }
      description="Address residual ‘other’ tags with embeddings (stage 2) or an LLM (stage 3)."
      bodyClassName={open ? undefined : "hidden"}
    >
      {q && (
        <p className="mb-3 text-xs text-text-muted">
          <span className="font-medium text-text">
            {formatQueueCount(q.other_unlocked)}
          </span>{" "}
          tags in <code>other</code> (unlocked) ·{" "}
          <span className="font-medium text-text">
            {formatQueueCount(q.total_tags)}
          </span>{" "}
          tags total
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-md border border-line bg-bg-subtle/40 p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Sparkles size={14} className="text-brand" /> Stage 2 — embedding NN
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <Field label="Model" className="col-span-3">
              <input
                className="pf-input font-mono text-xs"
                value={embedModel}
                onChange={(e) => setEmbedModel(e.target.value)}
                list="embed-model-suggestions"
              />
              <datalist id="embed-model-suggestions">
                <option value="mixedbread-ai/mxbai-embed-large-v1" />
                <option value="Snowflake/snowflake-arctic-embed-l-v2.0" />
                <option value="sentence-transformers/all-mpnet-base-v2" />
                <option value="BAAI/bge-base-en-v1.5" />
                <option value="BAAI/bge-small-en-v1.5" />
              </datalist>
            </Field>
            <Field label="Threshold">
              <input
                type="number"
                step={0.05}
                min={0}
                max={1}
                className="pf-input"
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
              />
            </Field>
            <Field label="Device" className="col-span-2">
              <select
                className="pf-input"
                value={embedDevice}
                onChange={(e) => setEmbedDevice(e.target.value)}
              >
                <option value="auto">auto (prefer GPU)</option>
                <option value="cuda:0">cuda:0</option>
                <option value="cuda:1">cuda:1</option>
                <option value="cpu">cpu</option>
              </select>
            </Field>
          </div>
          <button
            className="pf-btn-primary mt-3 w-full"
            disabled={stage2Mut.isPending}
            onClick={() => stage2Mut.mutate()}
          >
            <Sparkles size={14} /> Run embedding pass
          </button>
          <QueueHint loading={queueQ.isFetching && !q}>
            {q && (
              <>
                <span className="font-medium text-text">
                  {formatQueueCount(q.stage2.pending)}
                </span>{" "}
                tags left for Stage 2 (unknown/tag_tree residuals)
              </>
            )}
          </QueueHint>
          <p className="mt-1 text-[11px] text-text-subtle">
            requires <code>pip install -e .[embed]</code> +{" "}
            <code>torch</code> with CUDA. Job message will show the actual
            device picked.
          </p>
        </div>

        <div className="rounded-md border border-line bg-bg-subtle/40 p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Brain size={14} className="text-accent-amber" /> Stage 3 — LLM
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <Field label="Provider">
              <select
                className="pf-input"
                value={llmProvider}
                onChange={(e) => setLlmProvider(e.target.value)}
                title="Change the endpoint under Settings → LLM providers. The key and URL live there, so a per-run provider could only mismatch them."
              >
                <option value="">From Settings — {configuredTarget}</option>
                <option value="echo">echo (dry-run, no network)</option>
              </select>
            </Field>
            <Field label="Model">
              <input
                className="pf-input font-mono text-xs"
                value={llmModel}
                onChange={(e) => setLlmModel(e.target.value)}
                placeholder={stage3Cfg?.model || "(from Settings)"}
              />
            </Field>
            <Field label="Max tags">
              <input
                type="number"
                className="pf-input"
                value={llmMaxTags}
                onChange={(e) =>
                  setLlmMaxTags(e.target.value === "" ? "" : Number(e.target.value))
                }
              />
            </Field>
            <Field label="Parallel requests">
              <input
                type="number"
                min={1}
                max={12}
                className="pf-input"
                value={llmConcurrency}
                onChange={(e) => setLlmConcurrency(Number(e.target.value))}
              />
            </Field>
          </div>
          {batchAvailable && (
            <div className="mt-2">
              <Checkbox
                checked={useBatchApi}
                onChange={setUseBatchApi}
                label="Use OpenAI Batch API (−50% cost, results within 24h)"
              />
            </div>
          )}
          <button
            className="pf-btn-primary mt-3 w-full"
            disabled={stage3Mut.isPending}
            onClick={() => stage3Mut.mutate()}
          >
            <Brain size={14} /> Run LLM pass
          </button>
          <QueueHint loading={queueQ.isFetching && !q}>
            {q && (
              <>
                <span className="font-medium text-text">
                  {formatQueueCount(stage3Would)}
                </span>{" "}
                tags would run
                {typeof llmMaxTags === "number" &&
                  stage3Total > stage3Would &&
                  ` (max ${formatQueueCount(llmMaxTags)})`}
                {" · "}
                <span className="font-medium text-text">
                  {formatQueueCount(stage3Uncached)}
                </span>{" "}
                uncached API calls
                {stage3Uncached < stage3Would && (
                  <>
                    {" "}
                    (
                    {formatQueueCount(stage3Would - stage3Uncached)} already in
                    cache)
                  </>
                )}
                {" · "}
                {formatQueueCount(stage3Total)} total in{" "}
                <code>other</code>
              </>
            )}
          </QueueHint>
          <p className="mt-1 text-[11px] text-text-subtle">
            results cached to <code>data/tag_classification_cache.json</code>.
          </p>
        </div>
      </div>

      {batchesQ.data && batchesQ.data.length > 0 && (
        <div className="mt-3 rounded-md border border-line bg-bg-subtle/40 p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <ClipboardList size={14} className="text-accent-cyan" /> LLM
              batches
            </div>
            <button
              className="pf-btn text-xs"
              onClick={() =>
                qc.invalidateQueries({ queryKey: ["llm-batches"] })
              }
              title="Refresh batch statuses"
              aria-label="Refresh batch statuses"
            >
              <RefreshCcw size={12} />
            </button>
          </div>
          <ul className="space-y-1.5">
            {batchesQ.data.map((b) => (
              <li
                key={b.id}
                className="flex flex-wrap items-center gap-2 text-xs"
              >
                <span className={`pf-pill ${batchStatusClass(b.status)}`}>
                  {b.status}
                </span>
                <span className="font-mono">{b.model}</span>
                <span className="text-text-muted">
                  {b.tag_count.toLocaleString()} tags /{" "}
                  {b.request_count.toLocaleString()} reqs
                </span>
                <span className="text-text-subtle">
                  {new Date(b.submitted_at).toLocaleString()}
                </span>
                <span className="ml-auto">
                  {b.applied ? (
                    <span className="text-[11px] text-text-subtle">
                      applied ✓
                    </span>
                  ) : (
                    <button
                      className="pf-btn text-xs"
                      disabled={
                        b.status !== "completed" || applyingBatches.has(b.id)
                      }
                      onClick={() => applyBatchMut.mutate(b.id)}
                    >
                      {applyingBatches.has(b.id) ? "Applying…" : "Apply results"}
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          className="pf-btn"
          disabled={rebuildMut.isPending}
          onClick={() => rebuildMut.mutate()}
        >
          <RefreshCcw size={14} /> Rebuild scene_line only
        </button>
        <span className="text-[11px] text-text-subtle">
          (regenerates bucket lines; keeps character lines for export)
        </span>
        <button
          className="pf-btn text-xs"
          disabled={fixCharMut.isPending}
          onClick={() => fixCharMut.mutate()}
          title="Only repopulates scene_line[character] from Danbooru/AIBooru ingests — use if character.txt export was empty after an older rebuild."
        >
          Fix booru character lines
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 rounded-md border border-line bg-bg-subtle/40 p-3">
        <ConfirmButton
          className="pf-btn"
          disabled={reclassifyMut.isPending}
          onConfirm={() => reclassifyMut.mutate()}
          confirmLabel={
            q
              ? `Upgrade ${formatQueueCount(q.stage1.eligible)}?`
              : "Re-run rules?"
          }
          title="Re-run Stage 1 deterministic rules (tags.jsonl, *_boy/_girl, *_(franchise)/_(cosplay)/_(weapon)) on every existing tag and upgrade where the rule is more authoritative than the current label. Each upgrade is logged in the audit history."
        >
          <Wand2 size={14} /> Re-run Stage 1 rules
        </ConfirmButton>
        <label className="flex items-center gap-2 text-[11px] text-text-muted">
          replace embed/llm if conf ≤
          <input
            type="number"
            step={0.05}
            min={0}
            max={1}
            className="pf-input h-7 w-20"
            value={reclassifyCap}
            onChange={(e) => setReclassifyCap(Number(e.target.value))}
          />
        </label>
        <label className="flex items-center gap-2 text-[11px] text-text-muted">
          <input
            type="checkbox"
            className="accent-brand"
            checked={reclassifyTouchOther}
            onChange={(e) => setReclassifyTouchOther(e.target.checked)}
          />
          also upgrade tags currently in <code>other</code>
        </label>
        <QueueHint loading={queueQ.isFetching && !q}>
          {q && (
            <>
              <span className="font-medium text-text">
                {formatQueueCount(q.stage1.eligible)}
              </span>{" "}
              tags would upgrade (scanned{" "}
              {formatQueueCount(q.stage1.candidates_scanned)} candidates)
            </>
          )}
        </QueueHint>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 rounded-md border border-line bg-bg-subtle/40 p-3">
        <ConfirmButton
          className="pf-btn"
          disabled={resetMut.isPending}
          onConfirm={() => resetMut.mutate()}
          confirmLabel={
            q
              ? `Reset ${formatQueueCount(q.stage2_reset.pending)}?`
              : "Reset relabels?"
          }
          title="Return all embed-relabelled tags whose confidence is below the cutoff back to 'other'. Useful before re-running Stage 2 with a higher threshold or after adding new active buckets like extras. Each revert is logged as a 'reset' row in the audit history."
        >
          <RotateCcw size={14} /> Reset embed relabels
        </ConfirmButton>
        <label className="flex items-center gap-2 text-[11px] text-text-muted">
          below confidence
          <input
            type="number"
            step={0.05}
            min={0}
            max={1}
            className="pf-input h-7 w-20"
            value={resetThreshold}
            onChange={(e) => setResetThreshold(Number(e.target.value))}
          />
        </label>
        <QueueHint loading={queueQ.isFetching && !q}>
          {q && (
            <>
              <span className="font-medium text-text">
                {formatQueueCount(q.stage2_reset.pending)}
              </span>{" "}
              embed relabels below {resetThreshold.toFixed(2)} would reset
            </>
          )}
        </QueueHint>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 rounded-md border border-line bg-bg-subtle/40 p-3">
        {ratingOverwrite ? (
          <ConfirmButton
            className="pf-btn"
            disabled={ratingMut.isPending}
            onConfirm={() => ratingMut.mutate()}
            confirmLabel={
              q
                ? `Overwrite ${formatQueueCount(q.ratings.pending)}?`
                : "Overwrite ratings?"
            }
            title="Re-runs rating inference and overwrites previously inferred ratings."
          >
            <Sparkles size={14} /> Classify scene ratings
          </ConfirmButton>
        ) : (
          <button
            className="pf-btn"
            disabled={ratingMut.isPending}
            onClick={() => ratingMut.mutate()}
          >
            <Sparkles size={14} /> Classify scene ratings
          </button>
        )}
        <label className="flex items-center gap-2 text-[11px] text-text-muted">
          <input
            type="checkbox"
            className="accent-brand"
            checked={ratingOnlyMissing}
            onChange={(e) => setRatingOnlyMissing(e.target.checked)}
          />
          only images with no rating yet
        </label>
        <label className="flex items-center gap-2 text-[11px] text-text-muted">
          <input
            type="checkbox"
            className="accent-brand"
            checked={ratingOverwrite}
            onChange={(e) => setRatingOverwrite(e.target.checked)}
          />
          overwrite previously inferred ratings
        </label>
        <QueueHint loading={queueQ.isFetching && !q}>
          {q && (
            <>
              <span className="font-medium text-text">
                {formatQueueCount(q.ratings.pending)}
              </span>{" "}
              images left for rating inference
            </>
          )}
        </QueueHint>
      </div>
    </Panel>
  );
}

function TagsTable({
  rows,
  buckets,
  loading,
}: {
  rows: TagRow[];
  buckets: string[];
  loading?: boolean;
}) {
  const qc = useQueryClient();
  const parentRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    estimateSize: () => 38,
    getScrollElement: () => parentRef.current,
    overscan: 12,
  });

  const setBucketMut = useMutation({
    mutationFn: (vars: { name: string; bucket: string }) =>
      api.setTagBucket(vars.name, vars.bucket),
    onSuccess: () => {
      toast.success("override saved");
      invalidateTagDerived(qc);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div ref={parentRef} className="max-h-[70vh] overflow-y-auto">
      <div className="sticky top-0 z-10 grid grid-cols-[1.6fr_0.9fr_0.9fr_0.6fr_0.6fr_0.6fr] gap-2 border-b border-line bg-bg-panel px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
        <span>name</span>
        <span>bucket</span>
        <span>source</span>
        <span className="text-right">conf</span>
        <span className="text-right">usage</span>
        <span className="text-right">post#</span>
      </div>
      {loading && (
        <div className="flex items-center justify-center gap-2 px-3 py-10 text-xs text-text-muted">
          <Loader2 size={14} className="animate-spin" /> loading tags…
        </div>
      )}
      {!loading && rows.length === 0 && (
        <div className="px-3 py-10 text-center text-xs text-text-muted">
          No tags match these filters.
        </div>
      )}
      <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
        {rowVirtualizer.getVirtualItems().map((vrow) => {
          const r = rows[vrow.index];
          return (
            <div
              key={r.id}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${vrow.start}px)`,
                height: vrow.size,
              }}
              className="grid grid-cols-[1.6fr_0.9fr_0.9fr_0.6fr_0.6fr_0.6fr] items-center gap-2 border-b border-line/60 px-3 text-xs"
            >
              <span className="flex items-center gap-2 truncate font-mono">
                {r.locked && (
                  <Lock size={10} className="text-accent-amber" />
                )}
                {r.name}
              </span>
              <select
                value={r.bucket}
                onChange={(e) =>
                  setBucketMut.mutate({ name: r.name, bucket: e.target.value })
                }
                className="pf-input h-7"
              >
                {buckets.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
              <BucketBadge bucket={r.bucket_source} />
              <span className="text-right font-mono tabular-nums">
                {r.confidence.toFixed(2)}
              </span>
              <span className="text-right font-mono tabular-nums">
                {r.usage}
              </span>
              <span className="text-right font-mono tabular-nums">
                {r.post_count}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tag Review panel — frequency-sorted "other" tags with one-click bucket assign
// ---------------------------------------------------------------------------

// "other" is what the review panel assigns OUT of, so exclude it here.
const REVIEW_ASSIGN_BUCKETS = ASSIGN_BUCKETS.filter((b) => b !== "other");

function TagReviewPanel() {
  const qc = useQueryClient();
  const [reviewSource, setReviewSource] = useState("");
  const [reviewSearch, setReviewSearch] = useState("");
  const [reviewLimit] = useState(500);
  const [assigned, setAssigned] = useState<Set<string>>(new Set());

  const debouncedReviewSearch = useDebouncedValue(reviewSearch, 250);

  const tags = useQuery({
    queryKey: ["tags-review", reviewSource, debouncedReviewSearch, reviewLimit],
    queryFn: () =>
      api.listTags({
        bucket: "other",
        bucket_source: reviewSource || undefined,
        search: debouncedReviewSearch || undefined,
        sort: "usage",
        limit: reviewLimit,
      }),
    placeholderData: keepPreviousData,
  });

  const bucketSources = [
    { value: "", label: "all (any source)" },
    { value: "llm", label: "llm — GPT confirmed other" },
    { value: "unknown", label: "unknown — never processed" },
    { value: "tag_tree", label: "tag_tree residuals" },
  ];

  const assignMut = useMutation({
    mutationFn: ({ name, bucket }: { name: string; bucket: string }) =>
      api.setTagBucket(name, bucket),
    onSuccess: (_data, vars) => {
      setAssigned((prev) => new Set([...prev, vars.name]));
      invalidateTagDerived(qc);
      // Rapid-fire triage UI — misclicks happen. One-click revert.
      // lock:false returns the tag to the unclassified pool (other/unknown,
      // unlocked) instead of pinning it to "other" as a manual override.
      toast.success(`${vars.name} → ${vars.bucket}`, {
        action: {
          label: "Undo",
          onClick: async () => {
            try {
              await api.setTagBucket(vars.name, "other", { lock: false });
              setAssigned((prev) => {
                const next = new Set(prev);
                next.delete(vars.name);
                return next;
              });
              invalidateTagDerived(qc);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Undo failed");
            }
          },
        },
      });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const rows = (tags.data?.items ?? []).filter((t) => !assigned.has(t.name));
  const total = tags.data?.total ?? 0;

  return (
    <div className="space-y-4">
      {/* Stats bar */}
      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-line bg-bg-panel p-3 text-xs text-text-muted">
        <span>
          <span className="font-mono text-text">{total.toLocaleString()}</span> tags in{" "}
          <span className="font-mono text-accent-amber">other</span>
        </span>
        <span>
          Showing top <span className="font-mono text-text">{rows.length}</span> by image frequency
        </span>
        {assigned.size > 0 && (
          <span className="text-accent-green">
            ✓ {assigned.size} assigned this session
          </span>
        )}
        <span className="ml-auto text-[11px] text-text-subtle">
          Click a bucket button to assign and lock the tag. Changes are immediate.
        </span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <div>
          <label className="pf-label">Source filter</label>
          <select
            className="pf-input mt-1 h-8 text-xs"
            value={reviewSource}
            onChange={(e) => setReviewSource(e.target.value)}
          >
            {bucketSources.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="pf-label">Search</label>
          <input
            className="pf-input mt-1 h-8 font-mono text-xs"
            placeholder="filter by name…"
            value={reviewSearch}
            onChange={(e) => setReviewSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Tag rows */}
      <Panel title="Other tags — sorted by usage" bodyClassName="p-0">
        <div className="max-h-[70vh] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10 bg-bg-panel text-text-muted">
              <tr className="border-b border-line">
                <th className="px-3 py-2 text-left">Tag</th>
                <th className="px-3 py-2 text-left">Source</th>
                <th className="px-3 py-2 text-right">Images</th>
                <th className="px-3 py-2 text-left">Assign to bucket</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((tag) => (
                <tr key={tag.name} className="border-b border-line/40 hover:bg-bg-subtle/30">
                  <td className="px-3 py-2 font-mono">{tag.name}</td>
                  <td className="px-3 py-2">
                    <span className="rounded px-1.5 py-0.5 text-[10px] font-mono bg-bg-subtle text-text-muted">
                      {tag.bucket_source}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-text-muted">
                    {tag.usage.toLocaleString()}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {REVIEW_ASSIGN_BUCKETS.map((b) => (
                        <button
                          key={b}
                          className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${bucketButtonClass(b)}`}
                          onClick={() => assignMut.mutate({ name: tag.name, bucket: b })}
                          disabled={assignMut.isPending}
                        >
                          {b}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
              {!tags.isLoading && rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-8 text-center text-text-muted">
                    No unassigned tags in <span className="font-mono">other</span>
                    {reviewSource ? ` with source=${reviewSource}` : ""}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function RelabelHistoryPanel() {
  const qc = useQueryClient();
  const [toSource, setToSource] = useState<string>("embed");
  const [fromBucket, setFromBucket] = useState("");
  const [toBucket, setToBucket] = useState("");
  const [sort, setSort] = useState<"recent" | "confidence_asc">(
    "confidence_asc",
  );
  const [search, setSearch] = useState("");
  const [maxConfidence, setMaxConfidence] = useState<number | "">("");
  const [limit] = useState(300);

  const buckets = useQuery({ queryKey: ["buckets"], queryFn: api.listBuckets });

  const debouncedSearch = useDebouncedValue(search, 250);
  const debouncedMaxConfidence = useDebouncedValue(maxConfidence, 250);

  const params = useMemo(
    () => ({
      to_source: toSource || undefined,
      from_bucket: fromBucket || undefined,
      to_bucket: toBucket || undefined,
      search: debouncedSearch || undefined,
      max_confidence:
        typeof debouncedMaxConfidence === "number"
          ? debouncedMaxConfidence
          : undefined,
      sort,
      limit,
    }),
    [
      toSource,
      fromBucket,
      toBucket,
      debouncedSearch,
      debouncedMaxConfidence,
      sort,
      limit,
    ],
  );

  const history = useQuery({
    queryKey: ["tag-history", params],
    queryFn: () => api.listTagHistory(params as never),
    placeholderData: keepPreviousData,
  });

  const stats = useQuery({
    queryKey: ["tag-history-stats", toSource],
    queryFn: () =>
      api.tagHistoryStats({ to_source: toSource || undefined }),
  });

  const revertMut = useMutation({
    mutationFn: (id: number) => api.revertTagHistory(id, true),
    onSuccess: () => {
      toast.success("relabel reverted (tag is now locked)");
      qc.invalidateQueries({ queryKey: ["tag-history"] });
      qc.invalidateQueries({ queryKey: ["tag-history-stats"] });
      qc.invalidateQueries({ queryKey: ["tags"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const backfillMut = useMutation({
    mutationFn: () => api.backfillTagHistory(["embed", "llm"]),
    onSuccess: ({ inserted, skipped }) => {
      toast.success(
        `backfilled ${inserted.toLocaleString()} rows (skipped ${skipped.toLocaleString()} already-logged)`,
      );
      qc.invalidateQueries({ queryKey: ["tag-history"] });
      qc.invalidateQueries({ queryKey: ["tag-history-stats"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <History size={14} /> Relabel history
        </span>
      }
      description="Every Stage-2 / Stage-3 / manual bucket change is logged here so you can audit what changed and roll back individual relabels."
      actions={
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">
            {(history.data?.total ?? 0).toLocaleString()} entries
          </span>
          <button
            className="pf-btn h-7 text-[11px]"
            disabled={backfillMut.isPending}
            onClick={() => backfillMut.mutate()}
            title="Populate the audit log from the current tag state. Use this once for runs that happened before the audit log existed."
          >
            <RefreshCcw size={12} /> Backfill from current state
          </button>
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Field label="Source">
          <select
            className="pf-input"
            value={toSource}
            onChange={(e) => setToSource(e.target.value)}
          >
            <option value="">any</option>
            <option value="embed">embed</option>
            <option value="llm">llm</option>
            <option value="manual">manual</option>
            <option value="franchise_suffix">franchise_suffix</option>
            <option value="qualifier_rule">qualifier_rule</option>
            <option value="anthro_rule">anthro_rule</option>
            <option value="dataset_category">dataset_category</option>
            <option value="tag_tree">tag_tree</option>
            <option value="reset">reset (undo)</option>
            <option value="backfill">backfill</option>
          </select>
        </Field>
        <Field label="From bucket">
          <select
            className="pf-input"
            value={fromBucket}
            onChange={(e) => setFromBucket(e.target.value)}
          >
            <option value="">any</option>
            {(buckets.data ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </Field>
        <Field label="To bucket">
          <select
            className="pf-input"
            value={toBucket}
            onChange={(e) => setToBucket(e.target.value)}
          >
            <option value="">any</option>
            {(buckets.data ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Sort">
          <select
            className="pf-input"
            value={sort}
            onChange={(e) => setSort(e.target.value as never)}
          >
            <option value="confidence_asc">lowest confidence (risk audit)</option>
            <option value="recent">newest first</option>
          </select>
        </Field>
        <Field label="Max confidence">
          <input
            type="number"
            step={0.05}
            min={0}
            max={1}
            className="pf-input"
            placeholder="e.g. 0.65"
            value={maxConfidence}
            onChange={(e) =>
              setMaxConfidence(e.target.value === "" ? "" : Number(e.target.value))
            }
          />
        </Field>
        <Field label="Search tag">
          <div className="relative">
            <Search
              size={12}
              className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-subtle"
            />
            <input
              className="pf-input pl-7 font-mono"
              placeholder="cup of tea…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </Field>
      </div>

      <HistoryStats data={stats.data?.items ?? []} />

      <div className="mt-3 max-h-[60vh] overflow-y-auto rounded-md border border-line">
        <div className="sticky top-0 z-10 grid grid-cols-[1.6fr_1fr_1fr_0.6fr_0.8fr_0.9fr_0.4fr] gap-2 border-b border-line bg-bg-panel px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
          <span>tag</span>
          <span>from</span>
          <span>to</span>
          <span>conf</span>
          <span>source · model</span>
          <span>when</span>
          <span className="text-right">action</span>
        </div>
        {(history.data?.items ?? []).map((row) => (
          <HistoryRow
            key={row.id}
            row={row}
            onRevert={() => revertMut.mutate(row.id)}
            disabled={revertMut.isPending}
          />
        ))}
        {history.isPending && (
          <div className="flex items-center justify-center gap-2 px-4 py-8 text-xs text-text-muted">
            <Loader2 size={14} className="animate-spin" /> loading history…
          </div>
        )}
        {!history.isPending && (history.data?.items ?? []).length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-text-subtle">
            No history rows match these filters. If you just upgraded, click{" "}
            <span className="font-semibold text-text-muted">
              Backfill from current state
            </span>{" "}
            above to seed the log from your last classification run.
          </div>
        )}
      </div>
    </Panel>
  );
}

function HistoryStats({
  data,
}: {
  data: {
    from_bucket: string | null;
    to_bucket: string;
    count: number;
    avg_confidence: number | null;
  }[];
}) {
  if (!data.length) return null;
  const sorted = [...data].sort((a, b) => b.count - a.count).slice(0, 10);
  const max = Math.max(...sorted.map((d) => d.count));
  return (
    <div className="mt-3 rounded-md border border-line bg-bg-subtle/40 p-3">
      <div className="pf-section-title mb-2">
        Top from → to flows for the current source
      </div>
      <div className="space-y-1.5">
        {sorted.map((d, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className="w-24 truncate font-mono text-text-muted">
              {d.from_bucket ?? "—"}
            </span>
            <span className="text-text-subtle">→</span>
            <span className="w-24 truncate font-mono">{d.to_bucket}</span>
            <div className="relative h-2 flex-1 overflow-hidden rounded bg-bg-panel">
              <div
                className="h-full bg-brand/70"
                style={{ width: `${(d.count / max) * 100}%` }}
              />
            </div>
            <span className="w-14 text-right font-mono tabular-nums">
              {d.count.toLocaleString()}
            </span>
            <span className="w-12 text-right font-mono text-text-subtle">
              {d.avg_confidence != null ? d.avg_confidence.toFixed(2) : "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function HistoryRow({
  row,
  onRevert,
  disabled,
}: {
  row: TagHistoryRow;
  onRevert: () => void;
  disabled: boolean;
}) {
  const confColor =
    row.to_confidence >= 0.75
      ? "text-accent-green"
      : row.to_confidence >= 0.6
        ? "text-accent-amber"
        : "text-accent-rose";
  return (
    <div className="grid grid-cols-[1.6fr_1fr_1fr_0.6fr_0.8fr_0.9fr_0.4fr] items-center gap-2 border-b border-line/50 px-3 py-1.5 text-xs">
      <span className="truncate font-mono">{row.tag_name}</span>
      <span className="truncate text-text-muted">
        {row.from_bucket ?? "—"}
        {row.from_source ? (
          <span className="ml-1 text-[10px] text-text-subtle">
            ({row.from_source})
          </span>
        ) : null}
      </span>
      <span className="truncate">
        <BucketBadge bucket={row.to_bucket as never} />
      </span>
      <span className={`font-mono tabular-nums ${confColor}`}>
        {row.to_confidence.toFixed(2)}
      </span>
      <span
        className="truncate text-[11px] text-text-muted"
        title={row.model ?? ""}
      >
        {row.to_source}
        {row.model ? ` · ${row.model.split("/").pop()}` : ""}
      </span>
      <span className="text-[11px] text-text-subtle">
        {new Date(row.at).toLocaleString()}
      </span>
      <span className="text-right">
        <button
          className="pf-btn h-6 text-[10px]"
          disabled={disabled}
          onClick={onRevert}
          title="Roll back this single relabel and lock the tag so future passes won't redo it."
        >
          <Undo2 size={10} /> revert
        </button>
      </span>
    </div>
  );
}

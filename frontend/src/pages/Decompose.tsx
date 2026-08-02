/* Decompose — See-through anime layer decomposition.
 *
 * Drop an image (or pick a recent one), queue it through the local
 * see-through pipeline, watch progress, then browse the per-layer pieces
 * and open the layered PSD. All processing is local (see-through conda env).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileImage,
  FolderOpen,
  Layers,
  Loader2,
  PersonStanding,
  RefreshCw,
  RotateCcw,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { useLocation } from "wouter";

import {
  api,
  decomposeAssetUrl,
  decomposeInputUrl,
  type DecompItemRow,
  type DecompParams,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Panel } from "@/components/Panel";
import { Checkbox, ConfirmButton, Field } from "@/components/forms";

/* ------------------------------------------------------------------ */
/* Constants                                                           */
/* ------------------------------------------------------------------ */

const RECOMMENDED: DecompParams = {
  pipeline: "full",
  resolution: 1280,
  resolution_depth: 768,
  inference_steps: 30,
  inference_steps_depth: -1,
  seed: 42,
  tblr_split: false,
  group_offload: false,
  gpu: 0,
};

const PARAMS_LS_KEY = "tagforge-decompose-params";

const PIPELINES: { value: DecompParams["pipeline"]; label: string; hint: string }[] = [
  { value: "full", label: "Full", hint: "best quality, ~17 GB VRAM" },
  {
    value: "quantized",
    label: "NF4",
    hint: "~8 GB VRAM; downloads NF4 models on first run",
  },
  {
    value: "blockswap",
    label: "Block swap",
    hint: "lowest VRAM; downloads the depth model from HF on first run",
  },
];

/** Checkerboard behind transparent layer PNGs. */
const checker: CSSProperties = {
  backgroundImage:
    "repeating-conic-gradient(rgba(128,128,128,0.18) 0% 25%, transparent 0% 50%)",
  backgroundSize: "16px 16px",
};

function loadSavedParams(): DecompParams {
  try {
    const raw = localStorage.getItem(PARAMS_LS_KEY);
    if (raw) return { ...RECOMMENDED, ...(JSON.parse(raw) as Partial<DecompParams>) };
  } catch {
    /* corrupted — fall through to defaults */
  }
  return { ...RECOMMENDED };
}

/* ------------------------------------------------------------------ */
/* Small bits                                                          */
/* ------------------------------------------------------------------ */

/** Subtle "recommended value" dot: green when matching, hollow otherwise.
 * Clicking it resets the field to the recommended value. */
function RecDot({
  current,
  recommended,
  onReset,
  label,
}: {
  current: number | string | boolean;
  recommended: number | string | boolean;
  onReset: () => void;
  label: string;
}) {
  const match = current === recommended;
  return (
    <button
      type="button"
      onClick={onReset}
      title={match ? `recommended (${label})` : `recommended: ${label} — click to reset`}
      className={cn(
        "ml-1.5 inline-block h-2 w-2 shrink-0 rounded-full align-middle transition",
        match
          ? "bg-accent-green/70"
          : "border border-text-subtle/60 hover:border-accent-green hover:bg-accent-green/30",
      )}
    />
  );
}

function StatusPill({ status }: { status: DecompItemRow["status"] }) {
  const cls: Record<DecompItemRow["status"], string> = {
    queued: "border-line text-text-muted",
    running: "border-accent-amber/50 text-accent-amber",
    done: "border-accent-green/50 text-accent-green",
    error: "border-accent-rose/50 text-accent-rose",
    cancelled: "border-line text-text-subtle line-through",
  };
  return <span className={cn("pf-pill font-mono", cls[status])}>{status}</span>;
}

function EnvDot({ ok, label }: { ok: boolean | undefined; label: string }) {
  return (
    <span
      className="flex items-center gap-1 text-[11px] text-text-subtle"
      title={ok ? `${label}: found` : `${label}: missing — check Advanced paths`}
    >
      <span
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          ok === undefined ? "bg-text-subtle/40" : ok ? "bg-accent-green" : "bg-accent-rose",
        )}
      />
      {label}
    </span>
  );
}

function fmtDuration(start?: string | null, end?: string | null): string | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

interface StagedFile {
  path: string;
  name: string;
  previewUrl: string;
}

export function Decompose() {
  const qc = useQueryClient();
  const [, navigate] = useLocation();
  const invalidateItems = () =>
    qc.invalidateQueries({ queryKey: ["decompose", "items"] });

  /* ---- queries ---- */

  const status = useQuery({
    queryKey: ["decompose", "status"],
    queryFn: api.decomposeStatus,
    refetchInterval: 30_000,
  });

  const items = useQuery({
    queryKey: ["decompose", "items"],
    queryFn: () => api.decomposeItems(200),
    refetchInterval: (q) =>
      q.state.data?.items.some(
        (i) => i.status === "queued" || i.status === "running",
      )
        ? 1500
        : 15_000,
  });

  // git fetch on mount only (network); manual re-check via the button.
  const repo = useQuery({
    queryKey: ["decompose", "repo"],
    queryFn: () => api.decomposeRepoStatus(true),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });

  /* ---- params (persisted) ---- */

  const [params, setParams] = useState<DecompParams>(loadSavedParams);
  useEffect(() => {
    try {
      localStorage.setItem(PARAMS_LS_KEY, JSON.stringify(params));
    } catch {
      /* quota — non-fatal */
    }
  }, [params]);
  const set = <K extends keyof DecompParams>(key: K, value: DecompParams[K]) =>
    setParams((p) => ({ ...p, [key]: value }));

  /* ---- staging / upload ---- */

  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [uploading, setUploading] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const stageFiles = useCallback(async (files: File[]) => {
    const images = files.filter((f) => /\.(png|jpe?g|webp|bmp)$/i.test(f.name));
    if (!images.length) {
      toast.error("No supported images (png / jpg / webp / bmp)");
      return;
    }
    setUploading((n) => n + images.length);
    for (const file of images) {
      try {
        const res = await api.decomposeUpload(file);
        // Created outside the updater: StrictMode double-invokes updaters,
        // which would leak one blob URL per upload.
        const previewUrl = URL.createObjectURL(file);
        setStaged((s) => [...s, { path: res.path, name: res.name, previewUrl }]);
      } catch (err) {
        toast.error(`${file.name}: ${(err as Error).message}`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
  }, []);

  // Paste an image anywhere on the page to stage it (e.g. from Photoshop).
  useEffect(() => {
    function onPaste(e: ClipboardEvent) {
      const files = Array.from(e.clipboardData?.files ?? []);
      if (files.length) {
        e.preventDefault();
        void stageFiles(
          files.map((f, i) =>
            f.name ? f : new File([f], `pasted-${Date.now()}-${i}.png`, { type: f.type }),
          ),
        );
      }
    }
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [stageFiles]);

  // Revoke object URLs when staged entries go away.
  const stagedRef = useRef(staged);
  stagedRef.current = staged;
  useEffect(
    () => () => stagedRef.current.forEach((s) => URL.revokeObjectURL(s.previewUrl)),
    [],
  );

  function unstage(path: string) {
    const gone = stagedRef.current.find((x) => x.path === path);
    if (gone) URL.revokeObjectURL(gone.previewUrl);
    setStaged((s) => s.filter((x) => x.path !== path));
  }

  /* ---- mutations ---- */

  const queueMut = useMutation({
    mutationFn: (inputs: { path: string; name?: string }[]) =>
      api.decomposeQueue(inputs, params),
    onSuccess: (res, inputs) => {
      toast.success(
        res.queued.length === 1
          ? `Queued ${res.queued[0].original_name}`
          : `Queued ${res.queued.length} images`,
      );
      // Clear only what this call queued — a Recent-thumbnail re-queue (or
      // a file staged while the POST was in flight) must not be wiped.
      const queuedPaths = new Set(inputs.map((i) => i.path));
      stagedRef.current
        .filter((x) => queuedPaths.has(x.path))
        .forEach((x) => URL.revokeObjectURL(x.previewUrl));
      setStaged((s) => s.filter((x) => !queuedPaths.has(x.path)));
      invalidateItems();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const cancelMut = useMutation({
    mutationFn: api.decomposeCancel,
    onSuccess: invalidateItems,
    onError: (err: Error) => toast.error(err.message),
  });
  const requeueMut = useMutation({
    mutationFn: api.decomposeRequeue,
    onSuccess: () => {
      toast.success("Requeued");
      invalidateItems();
    },
    onError: (err: Error) => toast.error(err.message),
  });
  const deleteMut = useMutation({
    mutationFn: api.decomposeDelete,
    onSuccess: invalidateItems,
    onError: (err: Error) => toast.error(err.message),
  });
  const openMut = useMutation({
    mutationFn: ({ id, target }: { id: number; target: "psd" | "depth" | "folder" | "input" }) =>
      api.decomposeOpen(id, target),
    onError: (err: Error) => toast.error(err.message),
  });
  const updateMut = useMutation({
    mutationFn: api.decomposeRepoUpdate,
    onSuccess: (res) => {
      if (res.ok) toast.success("see-through updated — restart any running jobs to pick it up");
      else toast.error(`Update failed: ${res.output.slice(0, 300)}`);
      void repo.refetch();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  /* ---- selection + completion toasts ---- */

  const rows = useMemo(() => items.data?.items ?? [], [items.data]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = rows.find((r) => r.id === selectedId) ?? null;

  useEffect(() => {
    if (selectedId !== null && rows.some((r) => r.id === selectedId)) return;
    const firstDone = rows.find((r) => r.status === "done");
    setSelectedId(firstDone ? firstDone.id : null);
  }, [rows, selectedId]);

  const prevStatuses = useRef<Map<number, string>>(new Map());
  useEffect(() => {
    const prev = prevStatuses.current;
    for (const r of rows) {
      const before = prev.get(r.id);
      if (before && before !== r.status) {
        if (r.status === "done") {
          toast.success(`${r.original_name} decomposed`);
          // Jump to the fresh result only if the user isn't inspecting
          // something else — don't steal their selection mid-batch.
          setSelectedId((cur) => (cur === null || cur === r.id ? r.id : cur));
        } else if (r.status === "error") {
          toast.error(`${r.original_name} failed`);
        }
      }
    }
    prevStatuses.current = new Map(rows.map((r) => [r.id, r.status]));
  }, [rows]);

  const detail = useQuery({
    queryKey: ["decompose", "item", selectedId, selected?.status],
    queryFn: () => api.decomposeItem(selectedId!),
    enabled: selectedId !== null,
  });

  // Which image the "After" pane shows: composite or a single layer file.
  const [afterView, setAfterView] = useState<string>("reconstruction.png");
  useEffect(() => setAfterView("reconstruction.png"), [selectedId]);

  /* ---- recent inputs gallery (dedupe by input path) ---- */

  const recent = useMemo(() => {
    const seen = new Set<string>();
    const out: DecompItemRow[] = [];
    for (const r of rows) {
      if (seen.has(r.input_path)) continue;
      seen.add(r.input_path);
      out.push(r);
      if (out.length >= 12) break;
    }
    return out;
  }, [rows]);

  /* ---- config (advanced) ---- */

  const [cfgDraft, setCfgDraft] = useState<Record<string, string> | null>(null);
  const cfg = cfgDraft ?? status.data?.config ?? null;
  const saveCfgMut = useMutation({
    mutationFn: () => api.saveDecomposeConfig(cfgDraft ?? {}),
    onSuccess: () => {
      toast.success("Paths saved");
      setCfgDraft(null);
      void qc.invalidateQueries({ queryKey: ["decompose", "status"] });
      // repo_dir may have changed and the repo query never refetches on
      // its own (staleTime: Infinity).
      void qc.invalidateQueries({ queryKey: ["decompose", "repo"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  /* ---- render ---- */

  const envOk =
    status.data &&
    status.data.python_ok &&
    status.data.repo_ok &&
    status.data.layerdiff_ok &&
    status.data.depth_ok;
  const isFull = params.pipeline === "full";
  const behind = repo.data?.behind;

  return (
    <div className="space-y-4">
      {/* Top strip: env + repo status */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="pf-section-title flex items-center gap-2">
          <Layers size={16} className="text-text-muted" />
          Image decomposition
        </h1>
        <div className="flex items-center gap-3">
          <EnvDot ok={status.data?.python_ok} label="env" />
          <EnvDot ok={status.data?.repo_ok} label="repo" />
          <EnvDot ok={status.data?.layerdiff_ok} label="layerdiff" />
          <EnvDot ok={status.data?.depth_ok} label="depth" />
        </div>
        <div className="ml-auto flex items-center gap-2">
          {repo.isFetching ? (
            <span className="flex items-center gap-1.5 text-[11px] text-text-subtle">
              <Loader2 size={11} className="animate-spin" /> checking updates…
            </span>
          ) : repo.data?.ok ? (
            <span
              className={cn(
                "pf-pill font-mono",
                behind === 0 && "border-accent-green/40 text-accent-green",
                (behind ?? 0) > 0 && "border-accent-amber/50 text-accent-amber",
                behind == null && "border-line text-text-subtle",
              )}
              title={
                repo.data.fetch_error
                  ? `git fetch failed (${repo.data.fetch_error}) — showing last known state`
                  : `see-through @ ${repo.data.commit} (${repo.data.branch})`
              }
            >
              {behind === 0
                ? `up to date @ ${repo.data.commit}`
                : behind != null
                  ? `${behind} update${behind === 1 ? "" : "s"} available`
                  : `@ ${repo.data.commit}`}
            </span>
          ) : (
            <span className="pf-pill border-line text-text-subtle" title={repo.data?.error}>
              repo status unknown
            </span>
          )}
          {(behind ?? 0) > 0 && (
            <ConfirmButton
              className="pf-btn h-7 px-2 text-xs"
              confirmLabel="git pull?"
              onConfirm={() => updateMut.mutate()}
              disabled={updateMut.isPending}
              title="Fast-forward the see-through repo"
            >
              {updateMut.isPending ? "updating…" : "Update"}
            </ConfirmButton>
          )}
          <button
            type="button"
            className="pf-btn-ghost h-7 px-2"
            title="Check the see-through repo for updates (git fetch)"
            onClick={() => void repo.refetch()}
            disabled={repo.isFetching}
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {envOk === false && (
        <div className="rounded-md border border-accent-rose/40 bg-accent-rose/5 px-3 py-2 text-xs text-accent-rose">
          Some pipeline components are missing — check the paths under
          Options → Advanced before queueing.
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-12">
        {/* ---------------- Left: input + options ---------------- */}
        <div className="space-y-4 xl:col-span-5">
          <Panel title="Add images" description="Drop files, click to browse, or paste from the clipboard.">
            <div
              className={cn(
                "grid min-h-28 cursor-pointer place-items-center rounded-md border-2 border-dashed px-4 py-6 text-center transition",
                dragOver
                  ? "border-brand bg-brand/5"
                  : "border-line hover:border-text-subtle",
              )}
              onClick={() => fileInput.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={(e) => {
                // dragleave also fires when moving onto a child element.
                if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
                setDragOver(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                void stageFiles(Array.from(e.dataTransfer.files));
              }}
            >
              <div className="space-y-1 text-text-muted">
                <Upload size={18} className="mx-auto" />
                <div className="text-sm">Drop anime images here</div>
                <div className="text-[11px] text-text-subtle">
                  png · jpg · webp · bmp — single character works best
                </div>
              </div>
              <input
                ref={fileInput}
                type="file"
                accept=".png,.jpg,.jpeg,.webp,.bmp"
                multiple
                className="hidden"
                onChange={(e) => {
                  void stageFiles(Array.from(e.target.files ?? []));
                  e.target.value = "";
                }}
              />
            </div>

            {uploading > 0 && (
              <div className="mt-2 flex items-center gap-2 text-xs text-text-muted">
                <Loader2 size={12} className="animate-spin" />
                uploading {uploading} file{uploading === 1 ? "" : "s"}…
              </div>
            )}

            {staged.length > 0 && (
              <div className="mt-3 space-y-2">
                <div className="flex flex-wrap gap-2">
                  {staged.map((s) => (
                    <div
                      key={s.path}
                      className="relative overflow-hidden rounded-md border border-line"
                      title={s.name}
                    >
                      <img
                        src={s.previewUrl}
                        alt={s.name}
                        className="h-16 w-16 object-cover"
                      />
                      <button
                        type="button"
                        className="absolute right-0.5 top-0.5 rounded bg-bg/80 p-0.5 text-text-muted hover:text-accent-rose"
                        onClick={() => unstage(s.path)}
                        title="Remove"
                      >
                        <X size={11} />
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className="pf-btn-primary w-full"
                  disabled={queueMut.isPending || uploading > 0}
                  onClick={() =>
                    queueMut.mutate(staged.map((s) => ({ path: s.path, name: s.name })))
                  }
                >
                  Decompose {staged.length} image{staged.length === 1 ? "" : "s"}
                </button>
              </div>
            )}

            {recent.length > 0 && (
              <>
                <div className="pf-divider my-3" />
                <div className="pf-label">
                  Recent images{" "}
                  <span className="font-normal normal-case text-text-subtle">
                    — click to queue again with current options
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {recent.map((r) => (
                    <button
                      key={r.id}
                      type="button"
                      className="overflow-hidden rounded-md border border-line transition hover:border-brand"
                      title={`${r.original_name} — queue again`}
                      onClick={() =>
                        queueMut.mutate([{ path: r.input_path, name: r.original_name }])
                      }
                    >
                      <img
                        src={decomposeInputUrl(r.id, r.created_at)}
                        alt={r.original_name}
                        loading="lazy"
                        className="h-14 w-14 object-cover"
                      />
                    </button>
                  ))}
                </div>
              </>
            )}
          </Panel>

          <Panel
            title="Options"
            description="Green dot = recommended value; click a dot to reset that field."
            actions={
              <button
                type="button"
                className="pf-btn-ghost h-7 px-2 text-xs"
                title="Reset all options to recommended values"
                onClick={() => setParams({ ...RECOMMENDED })}
              >
                <RotateCcw size={12} className="mr-1 inline" />
                defaults
              </button>
            }
          >
            <div className="space-y-3">
              <Field label="Pipeline">
                <div className="flex flex-col gap-1">
                  <div className="inline-flex h-9 w-fit overflow-hidden rounded-md border border-line bg-bg-subtle text-xs">
                    {PIPELINES.map((p) => (
                      <button
                        key={p.value}
                        type="button"
                        title={p.hint}
                        onClick={() => set("pipeline", p.value)}
                        className={cn(
                          "flex items-center gap-1.5 px-3 transition",
                          params.pipeline === p.value
                            ? "bg-brand text-brand-fg"
                            : "text-text-muted hover:text-text",
                        )}
                      >
                        {p.label}
                        {p.value === RECOMMENDED.pipeline && (
                          <span
                            className={cn(
                              "inline-block h-1.5 w-1.5 rounded-full",
                              params.pipeline === p.value
                                ? "bg-brand-fg/80"
                                : "bg-accent-green/70",
                            )}
                            title="recommended"
                          />
                        )}
                      </button>
                    ))}
                  </div>
                  <span className="text-[11px] text-text-subtle">
                    {PIPELINES.find((p) => p.value === params.pipeline)?.hint}
                  </span>
                </div>
              </Field>

              <div className="grid grid-cols-2 gap-3">
                <Field
                  label={<>
                        Resolution
                        <RecDot
                          current={params.resolution}
                          recommended={RECOMMENDED.resolution}
                          label="1280"
                          onReset={() => set("resolution", RECOMMENDED.resolution)}
                        />
                      </>}
                >
                  <input
                    type="number"
                    className="pf-input w-full"
                    min={256}
                    max={2048}
                    step={64}
                    value={params.resolution}
                    onChange={(e) => set("resolution", Number(e.target.value))}
                  />
                </Field>
                <Field
                  label={<>
                        Depth resolution
                        <RecDot
                          current={params.resolution_depth}
                          recommended={RECOMMENDED.resolution_depth}
                          label="768 (-1 = match)"
                          onReset={() =>
                            set("resolution_depth", RECOMMENDED.resolution_depth)
                          }
                        />
                      </>}
                >
                  <input
                    type="number"
                    className="pf-input w-full"
                    min={-1}
                    max={2048}
                    step={64}
                    value={params.resolution_depth}
                    onChange={(e) => set("resolution_depth", Number(e.target.value))}
                  />
                </Field>
                <Field
                  label={<>
                        Diffusion steps
                        <RecDot
                          current={params.inference_steps}
                          recommended={RECOMMENDED.inference_steps}
                          label="30"
                          onReset={() =>
                            set("inference_steps", RECOMMENDED.inference_steps)
                          }
                        />
                      </>}
                >
                  <input
                    type="number"
                    className="pf-input w-full"
                    min={1}
                    max={200}
                    value={params.inference_steps}
                    onChange={(e) => set("inference_steps", Number(e.target.value))}
                  />
                </Field>
                <Field
                  label={<>
                        Depth steps
                        <RecDot
                          current={params.inference_steps_depth}
                          recommended={RECOMMENDED.inference_steps_depth}
                          label="-1 = auto"
                          onReset={() =>
                            set("inference_steps_depth", RECOMMENDED.inference_steps_depth)
                          }
                        />
                      </>}
                >
                  <input
                    type="number"
                    className="pf-input w-full"
                    min={-1}
                    max={200}
                    value={params.inference_steps_depth}
                    disabled={!isFull}
                    title={isFull ? undefined : "only the Full pipeline exposes this"}
                    onChange={(e) => set("inference_steps_depth", Number(e.target.value))}
                  />
                </Field>
                <Field
                  label={<>
                        Seed
                        <RecDot
                          current={params.seed}
                          recommended={RECOMMENDED.seed}
                          label="42"
                          onReset={() => set("seed", RECOMMENDED.seed)}
                        />
                      </>}
                >
                  <input
                    type="number"
                    className="pf-input w-full"
                    min={0}
                    value={params.seed}
                    onChange={(e) => set("seed", Number(e.target.value))}
                  />
                </Field>
                <Field
                  label={<>
                        GPU index
                        <RecDot
                          current={params.gpu}
                          recommended={RECOMMENDED.gpu}
                          label="0"
                          onReset={() => set("gpu", RECOMMENDED.gpu)}
                        />
                      </>}
                >
                  <input
                    type="number"
                    className="pf-input w-full"
                    min={0}
                    max={8}
                    value={params.gpu}
                    onChange={(e) => set("gpu", Number(e.target.value))}
                  />
                </Field>
              </div>

              <div className="space-y-2">
                <Checkbox
                  checked={params.tblr_split}
                  onChange={(v) => set("tblr_split", v)}
                  label="Split left/right parts (e.g. twin-tails into separate layers)"
                />
                <Checkbox
                  checked={params.group_offload}
                  onChange={(v) => set("group_offload", v)}
                  label="Group offload — lower VRAM, slower (not needed on 24 GB)"
                />
              </div>

              <details className="group">
                <summary className="cursor-pointer select-none text-xs text-text-muted hover:text-text">
                  Advanced — pipeline paths
                </summary>
                {cfg && (
                  <div className="mt-2 space-y-2">
                    {(
                      [
                        ["python_path", "Python (see_through env)"],
                        ["repo_dir", "see-through repo"],
                        ["layerdiff_dir", "LayerDiff model dir"],
                        ["depth_dir", "Depth model dir"],
                      ] as const
                    ).map(([key, label]) => (
                      <Field key={key} label={label}>
                        <input
                          className="pf-input w-full font-mono text-xs"
                          value={cfg[key] ?? ""}
                          onChange={(e) =>
                            setCfgDraft({ ...cfg, [key]: e.target.value })
                          }
                        />
                      </Field>
                    ))}
                    <button
                      type="button"
                      className="pf-btn text-xs"
                      disabled={!cfgDraft || saveCfgMut.isPending}
                      onClick={() => saveCfgMut.mutate()}
                    >
                      Save paths
                    </button>
                  </div>
                )}
              </details>
            </div>
          </Panel>
        </div>

        {/* ---------------- Right: queue + result ---------------- */}
        <div className="space-y-4 xl:col-span-7">
          <Panel
            title={`Queue${status.data?.queued ? ` — ${status.data.queued} waiting` : ""}`}
            description="Items run one at a time on the GPU. A full-quality decomposition takes a few minutes."
          >
            {rows.length === 0 ? (
              <div className="py-6 text-center text-sm text-text-muted">
                Nothing here yet — drop an image on the left to get started.
              </div>
            ) : (
              <div className="max-h-80 space-y-1 overflow-y-auto pr-1">
                {rows.map((r) => {
                  const dur = fmtDuration(r.started_at, r.finished_at);
                  return (
                    <div
                      key={r.id}
                      className={cn(
                        "flex cursor-pointer items-center gap-3 rounded-md border px-2 py-1.5 transition",
                        selectedId === r.id
                          ? "border-brand/60 bg-brand/5"
                          : "border-transparent hover:bg-bg-hover",
                      )}
                      onClick={() => setSelectedId(r.id)}
                    >
                      <img
                        src={decomposeInputUrl(r.id, r.created_at)}
                        alt=""
                        loading="lazy"
                        className="h-10 w-10 shrink-0 rounded object-cover"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm text-text">
                            {r.original_name}
                          </span>
                          <StatusPill status={r.status} />
                          {dur && (
                            <span className="text-[11px] text-text-subtle">{dur}</span>
                          )}
                        </div>
                        {r.status === "running" && (
                          <div className="mt-1 flex items-center gap-2">
                            <div className="h-1 flex-1 overflow-hidden rounded bg-bg-subtle">
                              <div
                                className="h-full bg-accent-amber transition-all"
                                style={{ width: `${Math.round(r.progress * 100)}%` }}
                              />
                            </div>
                            <span className="whitespace-nowrap text-[11px] text-text-subtle">
                              {r.message}
                            </span>
                          </div>
                        )}
                      </div>
                      <div
                        className="flex shrink-0 items-center gap-1"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {r.status === "done" && (
                          <>
                            <button
                              type="button"
                              className="pf-btn h-7 px-2 text-xs"
                              title="Open the layered PSD"
                              onClick={() => openMut.mutate({ id: r.id, target: "psd" })}
                            >
                              <FileImage size={12} className="mr-1 inline" />
                              PSD
                            </button>
                            <button
                              type="button"
                              className="pf-btn-ghost h-7 px-2"
                              title="Open output folder"
                              onClick={() =>
                                openMut.mutate({ id: r.id, target: "folder" })
                              }
                            >
                              <FolderOpen size={13} />
                            </button>
                          </>
                        )}
                        {(r.status === "queued" || r.status === "running") && (
                          <ConfirmButton
                            className="pf-btn-ghost h-7 px-2 text-xs"
                            confirmLabel="stop?"
                            title="Cancel this item"
                            onConfirm={() => cancelMut.mutate(r.id)}
                          >
                            cancel
                          </ConfirmButton>
                        )}
                        {(r.status === "done" ||
                          r.status === "error" ||
                          r.status === "cancelled") && (
                          <>
                            <button
                              type="button"
                              className="pf-btn-ghost h-7 px-2 text-xs"
                              title="Queue again with the same options it ran with"
                              onClick={() => requeueMut.mutate(r.id)}
                            >
                              <RefreshCw size={12} />
                            </button>
                            <ConfirmButton
                              className="pf-btn-ghost h-7 px-2 text-xs"
                              confirmLabel="delete?"
                              title="Delete this item and its output files"
                              onConfirm={() => deleteMut.mutate(r.id)}
                            >
                              <X size={12} />
                            </ConfirmButton>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Panel>

          {selected && (
            <Panel
              title={`Result — ${selected.original_name}`}
              actions={
                selected.status === "done" ? (
                  <>
                    <button
                      type="button"
                      className="pf-btn-primary h-8 px-3 text-xs"
                      onClick={() => openMut.mutate({ id: selected.id, target: "psd" })}
                    >
                      <FileImage size={13} className="mr-1.5 inline" />
                      Open PSD
                    </button>
                    <button
                      type="button"
                      className="pf-btn h-8 px-2 text-xs"
                      title="Animate this character on the Rig tab"
                      onClick={() => navigate(`/rig?item=${selected.id}`)}
                    >
                      <PersonStanding size={13} className="mr-1 inline" />
                      Rig avatar
                    </button>
                    {selected.has_depth_psd && (
                      <button
                        type="button"
                        className="pf-btn h-8 px-2 text-xs"
                        title="Open the depth-map PSD"
                        onClick={() =>
                          openMut.mutate({ id: selected.id, target: "depth" })
                        }
                      >
                        depth PSD
                      </button>
                    )}
                    <button
                      type="button"
                      className="pf-btn-ghost h-8 px-2"
                      title="Open output folder in Explorer"
                      onClick={() =>
                        openMut.mutate({ id: selected.id, target: "folder" })
                      }
                    >
                      <FolderOpen size={14} />
                    </button>
                  </>
                ) : undefined
              }
            >
              {selected.status === "error" && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-accent-rose/30 bg-accent-rose/5 p-3 text-xs text-accent-rose">
                  {selected.error ?? "unknown error"}
                </pre>
              )}
              {(selected.status === "queued" || selected.status === "running") && (
                <div className="flex items-center gap-2 py-4 text-sm text-text-muted">
                  <Loader2 size={14} className="animate-spin" />
                  {selected.status === "queued"
                    ? "waiting in queue…"
                    : `${selected.message} (${Math.round(selected.progress * 100)}%)`}
                </div>
              )}

              {selected.status === "done" && detail.data && (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <figure>
                      <div className="overflow-hidden rounded-md border border-line">
                        <img
                          src={decomposeInputUrl(selected.id, selected.created_at)}
                          alt="original"
                          className="max-h-[420px] w-full object-contain"
                        />
                      </div>
                      <figcaption className="mt-1 text-center text-[11px] text-text-subtle">
                        before — original
                      </figcaption>
                    </figure>
                    <figure>
                      <div
                        className="overflow-hidden rounded-md border border-line"
                        style={checker}
                      >
                        <img
                          src={decomposeAssetUrl(selected.id, afterView, selected.finished_at)}
                          alt="decomposed"
                          className="max-h-[420px] w-full object-contain"
                        />
                      </div>
                      <figcaption className="mt-1 text-center text-[11px] text-text-subtle">
                        after —{" "}
                        {afterView === "reconstruction.png"
                          ? "all layers composited"
                          : afterView.replace(/\.png$/, "")}
                      </figcaption>
                    </figure>
                  </div>

                  {detail.data.layers.length > 0 && (
                    <div>
                      <div className="pf-label mb-2">
                        Layer pieces — {detail.data.layers.length}
                        <span className="ml-1 font-normal normal-case text-text-subtle">
                          (click to inspect, occluded regions are inpainted)
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {detail.data.has_reconstruction && (
                          <LayerThumb
                            id={selected.id}
                            file="reconstruction.png"
                            label="composite"
                            v={selected.finished_at}
                            active={afterView === "reconstruction.png"}
                            onClick={() => setAfterView("reconstruction.png")}
                          />
                        )}
                        {detail.data.layers.map((l) => (
                          <LayerThumb
                            key={l.file}
                            id={selected.id}
                            file={l.file}
                            label={l.name}
                            v={selected.finished_at}
                            active={afterView === l.file}
                            onClick={() =>
                              setAfterView((v) =>
                                v === l.file ? "reconstruction.png" : l.file,
                              )
                            }
                          />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

function LayerThumb({
  id,
  file,
  label,
  v,
  active,
  onClick,
}: {
  id: number;
  file: string;
  label: string;
  v?: string | null;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      className={cn(
        "w-[72px] overflow-hidden rounded-md border text-left transition",
        active ? "border-brand ring-1 ring-brand" : "border-line hover:border-text-subtle",
      )}
    >
      <div style={checker}>
        <img
          src={decomposeAssetUrl(id, file, v)}
          alt={label}
          loading="lazy"
          className="h-[72px] w-full object-contain"
        />
      </div>
      <div className="truncate bg-bg-subtle px-1 py-0.5 text-[10px] text-text-muted">
        {label}
      </div>
    </button>
  );
}

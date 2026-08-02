import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, FolderTree, Save, Trash2 } from "lucide-react";

import {
  api,
  subscribeJobStream,
  type DenyListRow,
  type ExportManifest,
  type SourceRow,
} from "@/lib/api";
import { jobStore } from "@/lib/jobStore";
import { Panel } from "@/components/Panel";
import { PresetPicker } from "@/components/PresetPicker";
import {
  Checkbox,
  ConfirmButton,
  CopyButton,
  Field,
  RatingFilter,
  SegmentedControl,
} from "@/components/forms";

const ALL_BUCKETS = [
  "outfit",
  "pose",
  "expression",
  "background",
  "composition",
  "accessory",
  "extras",
  "character",
  "scene",
];

/** Per-bucket lines that can be composed into scene.txt at export time. */
const SCENE_RECIPE_BUCKETS = [
  "outfit",
  "pose",
  "expression",
  "background",
  "composition",
  "accessory",
  "extras",
];

type OriginFilter = "" | "local" | "booru";

const MAX_RATING_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "any (no per-tag stripping)" },
  { value: "g", label: "general (g) — strip s/q/e" },
  { value: "s", label: "sensitive (s) — strip q/e" },
  { value: "q", label: "questionable (q) — strip e" },
  { value: "e", label: "explicit (e) — keep everything" },
];

export function Export() {
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.listSources });
  const presets = useQuery({
    queryKey: ["export", "presets"],
    queryFn: api.exportPresetDirs,
  });
  const defaultDeny = useQuery({
    queryKey: ["export", "default-deny"],
    queryFn: api.defaultDenyTags,
  });
  const denyLists = useQuery({
    queryKey: ["export", "deny-lists"],
    queryFn: api.listDenyLists,
  });
  const qc = useQueryClient();

  const [name, setName] = useState(() => {
    const d = new Date();
    return `export_${d.toISOString().slice(0, 10).replace(/-/g, "")}`;
  });
  const [outputDir, setOutputDir] = useState<string>("");
  const [mirrorDir, setMirrorDir] = useState<string>("");
  const [filePrefix, setFilePrefix] = useState("");
  const [origin, setOrigin] = useState<OriginFilter>("");
  const [sourceIds, setSourceIds] = useState<number[]>([]);
  const [rating, setRating] = useState("");
  const [scoreMin, setScoreMin] = useState<number | "">("");
  const [maxRating, setMaxRating] = useState<string>("");
  const [minTagCount, setMinTagCount] = useState<number>(2);
  const [dedupe, setDedupe] = useState(true);
  const [dedupeIgnoreOrder, setDedupeIgnoreOrder] = useState(true);
  const [buckets, setBuckets] = useState<string[]>([...ALL_BUCKETS]);
  const [sceneBuckets, setSceneBuckets] = useState<string[]>([
    "outfit",
    "pose",
    "expression",
    "background",
  ]);
  const [capTags, setCapTags] = useState(
    "white background, simple background",
  );
  const [capEnabled, setCapEnabled] = useState(false);
  const [capPercent, setCapPercent] = useState(25);
  const [useDefaultDeny, setUseDefaultDeny] = useState(true);
  const [extraDeny, setExtraDeny] = useState("");
  const [showDenyList, setShowDenyList] = useState(false);
  const [selectedDenyListId, setSelectedDenyListId] = useState<number | "new" | "">("");
  const [saveAsName, setSaveAsName] = useState("");
  const [result, setResult] = useState<ExportManifest | null>(null);
  const [exporting, setExporting] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // Close any live job stream when the page unmounts.
  useEffect(() => () => esRef.current?.close(), []);

  useEffect(() => {
    if (!outputDir && presets.data?.tagforge_exports) {
      setOutputDir(presets.data.tagforge_exports);
    }
  }, [presets.data, outputDir]);

  const parsedExtraDeny = extraDeny
    .split(/[,\n]/)
    .map((t) => t.trim())
    .filter(Boolean);

  const parsedCapTags = capTags
    .split(/[,\n]/)
    .map((t) => t.trim())
    .filter(Boolean);

  const runMut = useMutation({
    mutationFn: () =>
      api.runExport({
        name,
        output_dir: outputDir || undefined,
        source_ids: sourceIds,
        origin: origin || undefined,
        ratings: rating
          ? rating.split(",").map((r) => r.trim()).filter(Boolean)
          : [],
        score_min: typeof scoreMin === "number" ? scoreMin : undefined,
        max_rating: maxRating || undefined,
        buckets,
        scene_buckets: sceneBuckets,
        min_tag_count: minTagCount,
        deduplicate: dedupe,
        dedupe_ignore_order: dedupeIgnoreOrder,
        file_prefix: filePrefix,
        use_default_deny: useDefaultDeny,
        extra_deny_tags: parsedExtraDeny,
        mirror_dir: mirrorDir || undefined,
        ...(capEnabled && parsedCapTags.length
          ? { cap_tags: parsedCapTags, cap_percent: capPercent }
          : {}),
      }),
    onSuccess: ({ job_id }) => {
      jobStore.add(job_id);
      toast.success("export started");
      setExporting(true);
      // A new run supersedes any stream still open from the previous one.
      esRef.current?.close();
      const es = subscribeJobStream(job_id, (job) => {
        if (job.status === "done" && job.detail) {
          setResult(job.detail as ExportManifest);
          setExporting(false);
          es.close();
        }
        if (job.status === "error" || job.status === "cancelled") {
          setExporting(false);
          es.close();
        }
      });
      esRef.current = es;
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const createDenyListMut = useMutation({
    mutationFn: (vars: { name: string; tags: string[] }) =>
      api.createDenyList(vars.name, vars.tags),
    onSuccess: (created: DenyListRow) => {
      setSelectedDenyListId(created.id);
      setSaveAsName("");
      qc.invalidateQueries({ queryKey: ["export", "deny-lists"] });
      toast.success(`saved "${created.name}"`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const updateDenyListMut = useMutation({
    mutationFn: (vars: { id: number; name: string; tags: string[] }) =>
      api.updateDenyList(vars.id, vars.tags),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["export", "deny-lists"] });
      toast.success(`updated "${vars.name}"`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteDenyListMut = useMutation({
    mutationFn: (vars: { id: number; name: string }) =>
      api.deleteDenyList(vars.id),
    onSuccess: (_res, vars) => {
      setSelectedDenyListId("");
      setExtraDeny("");
      qc.invalidateQueries({ queryKey: ["export", "deny-lists"] });
      toast.success(`deleted "${vars.name}"`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const savePending = createDenyListMut.isPending || updateDenyListMut.isPending;

  function saveDenyList() {
    if (savePending) return;
    if (selectedDenyListId === "new") {
      const trimmed = saveAsName.trim();
      if (!trimmed) {
        toast.error("enter a name for the new list");
        return;
      }
      if (!parsedExtraDeny.length) {
        toast.error("deny list is empty — paste some tags first");
        return;
      }
      createDenyListMut.mutate({ name: trimmed, tags: parsedExtraDeny });
    } else if (typeof selectedDenyListId === "number") {
      const existing = denyLists.data?.find((l) => l.id === selectedDenyListId);
      updateDenyListMut.mutate({
        id: selectedDenyListId,
        name: existing?.name ?? "list",
        tags: parsedExtraDeny,
      });
    }
  }

  function applyExportSnapshot(data: Record<string, unknown>) {
    if (typeof data.outputDir === "string") setOutputDir(data.outputDir);
    // Reset when absent: presets saved before this field existed must not
    // leave a stale mirror dir silently duplicating exports to another drive.
    setMirrorDir(typeof data.mirrorDir === "string" ? data.mirrorDir : "");
    if (typeof data.filePrefix === "string") setFilePrefix(data.filePrefix);
    if (data.origin === "" || data.origin === "local" || data.origin === "booru") {
      setOrigin(data.origin);
    }
    if (Array.isArray(data.sourceIds)) {
      setSourceIds(
        data.sourceIds.filter((x): x is number => typeof x === "number"),
      );
    }
    if (typeof data.rating === "string") setRating(data.rating);
    const sm = data.scoreMin;
    if (typeof sm === "number") setScoreMin(sm);
    else if (sm === "") setScoreMin("");
    if (typeof data.maxRating === "string") setMaxRating(data.maxRating);
    if (typeof data.minTagCount === "number") setMinTagCount(data.minTagCount);
    if (typeof data.dedupe === "boolean") setDedupe(data.dedupe);
    if (typeof data.dedupeIgnoreOrder === "boolean") {
      setDedupeIgnoreOrder(data.dedupeIgnoreOrder);
    }
    if (Array.isArray(data.buckets)) {
      setBuckets(
        data.buckets.filter(
          (b): b is string => typeof b === "string" && ALL_BUCKETS.includes(b),
        ),
      );
    }
    if (Array.isArray(data.sceneBuckets)) {
      const next = data.sceneBuckets.filter(
        (b): b is string =>
          typeof b === "string" && SCENE_RECIPE_BUCKETS.includes(b),
      );
      // Never restore an empty recipe — scene.txt needs at least one bucket.
      if (next.length) setSceneBuckets(next);
    }
    if (typeof data.capTags === "string") setCapTags(data.capTags);
    if (typeof data.capEnabled === "boolean") setCapEnabled(data.capEnabled);
    if (typeof data.capPercent === "number") {
      setCapPercent(Math.min(99, Math.max(1, data.capPercent)));
    }
    if (typeof data.useDefaultDeny === "boolean") {
      setUseDefaultDeny(data.useDefaultDeny);
    }
    // Deny-list selection: only restore ids that still exist. Note we do NOT
    // load the saved list's tags here — the snapshot's extraDeny wins, so it
    // is applied last.
    if ("selectedDenyListId" in data) {
      const id = data.selectedDenyListId;
      if (
        typeof id === "number" &&
        (denyLists.data ?? []).some((l) => l.id === id)
      ) {
        setSelectedDenyListId(id);
      } else {
        setSelectedDenyListId("");
      }
    }
    if (typeof data.extraDeny === "string") setExtraDeny(data.extraDeny);
  }

  // The manifest's mirror_dir isn't in the shared ExportManifest type yet;
  // read it via a local widening so rendering stays type-safe.
  const resultMirrorDir = result
    ? (result as ExportManifest & { mirror_dir?: string }).mirror_dir
    : undefined;

  const groupedSources: Record<"local" | "booru" | "other", SourceRow[]> = {
    local: [],
    booru: [],
    other: [],
  };
  for (const s of sources.data ?? []) {
    groupedSources[s.origin].push(s);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Export</h1>
        <p className="text-sm text-text-muted">
          Emit per-bucket wildcard files. One line per coherent scene from one image.
        </p>
      </div>

      <Panel
        title="Filters"
        actions={
          <PresetPicker
            kind="export"
            getSnapshot={() => ({
              outputDir,
              mirrorDir,
              filePrefix,
              origin,
              sourceIds,
              rating,
              scoreMin,
              maxRating,
              minTagCount,
              dedupe,
              dedupeIgnoreOrder,
              buckets,
              sceneBuckets,
              capTags,
              capEnabled,
              capPercent,
              useDefaultDeny,
              extraDeny,
              selectedDenyListId,
            })}
            applySnapshot={applyExportSnapshot}
          />
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!runMut.isPending && !exporting) runMut.mutate();
          }}
        >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Name">
            <input
              className="pf-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="File prefix (optional)">
            <input
              className="pf-input font-mono"
              value={filePrefix}
              onChange={(e) => setFilePrefix(e.target.value)}
              placeholder="e.g. good"
            />
          </Field>
          <Field label="Scene rating filter">
            <RatingFilter value={rating} onChange={setRating} />
          </Field>
          <Field label="Score min">
            <input
              type="number"
              className="pf-input"
              value={scoreMin}
              onChange={(e) =>
                setScoreMin(e.target.value === "" ? "" : Number(e.target.value))
              }
            />
          </Field>

          <Field label="Max rating per line (strip mode)">
            <select
              className="pf-input"
              value={maxRating}
              onChange={(e) => setMaxRating(e.target.value)}
            >
              {MAX_RATING_OPTIONS.map((opt) => (
                <option key={opt.value || "any"} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Min tags / line">
            <input
              type="number"
              className="pf-input"
              value={minTagCount}
              onChange={(e) => setMinTagCount(Number(e.target.value))}
            />
          </Field>

          <Field label="Dedupe">
            <label className="flex h-9 items-center gap-2 rounded-md border border-line bg-bg-subtle px-3 text-sm">
              <input
                type="checkbox"
                checked={dedupe}
                onChange={(e) => setDedupe(e.target.checked)}
                className="accent-brand"
              />
              skip duplicate lines
            </label>
            {dedupe && (
              <div className="mt-2">
                <Checkbox
                  checked={dedupeIgnoreOrder}
                  onChange={setDedupeIgnoreOrder}
                  label="ignore tag order"
                />
              </div>
            )}
          </Field>

          <Field label="Tag share cap" className="md:col-span-2">
            <div className="space-y-2">
              <div className="flex h-9 items-center">
                <Checkbox
                  checked={capEnabled}
                  onChange={setCapEnabled}
                  label="Cap plain-background share"
                />
              </div>
              {capEnabled && (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      className="pf-input h-8 min-w-56 flex-1 font-mono text-xs"
                      value={capTags}
                      onChange={(e) => setCapTags(e.target.value)}
                      placeholder="white background, simple background"
                    />
                    <label className="flex items-center gap-1.5 text-xs text-text-muted">
                      max %
                      <input
                        type="number"
                        min={1}
                        max={99}
                        className="pf-input h-8 w-16"
                        value={capPercent}
                        onChange={(e) => {
                          const n = Number(e.target.value);
                          if (Number.isFinite(n)) {
                            setCapPercent(Math.min(99, Math.max(1, n)));
                          }
                        }}
                      />
                    </label>
                  </div>
                  <p className="text-[11px] text-text-subtle">
                    lines containing these tags are down-sampled to at most this
                    share of each file
                  </p>
                </>
              )}
            </div>
          </Field>

          <Field label="Origin" className="md:col-span-2">
            <SegmentedControl<OriginFilter>
              options={[
                { value: "", label: "all" },
                { value: "local", label: "local" },
                { value: "booru", label: "booru" },
              ]}
              value={origin}
              onChange={(o) => {
                setOrigin(o);
                // Drop selections the new origin hides — otherwise the export
                // sends contradictory source_ids + origin and matches nothing.
                if (o) {
                  setSourceIds((prev) =>
                    prev.filter((id) =>
                      (sources.data ?? []).some(
                        (s) => s.id === id && s.origin === o,
                      ),
                    ),
                  );
                }
              }}
            />
          </Field>

          <Field label="Output directory" className="md:col-span-2">
            <input
              className="pf-input font-mono"
              value={outputDir}
              onChange={(e) => setOutputDir(e.target.value)}
              placeholder="auto"
            />
            <div className="mt-2 flex flex-wrap gap-1">
              {presets.data &&
                Object.entries(presets.data).map(([k, v]) => (
                  <button
                    key={k}
                    type="button"
                    className="pf-pill cursor-pointer text-text-muted hover:text-text"
                    onClick={() => setOutputDir(v)}
                  >
                    <FolderTree size={10} /> {k}
                  </button>
                ))}
            </div>
          </Field>

          <Field label="Also export to (optional)" className="md:col-span-2">
            <input
              className="pf-input font-mono"
              value={mirrorDir}
              onChange={(e) => setMirrorDir(e.target.value)}
              placeholder="second output dir e.g. another drive"
            />
          </Field>

          <Field label="Sources" className="md:col-span-4">
            <div className="space-y-3">
              {(["local", "booru", "other"] as const).map((group) => {
                const list = groupedSources[group].filter(
                  (s) => !origin || s.origin === origin,
                );
                if (!list.length) return null;
                const groupIds = list.map((s) => s.id);
                const allSelected = groupIds.every((id) =>
                  sourceIds.includes(id),
                );
                return (
                  <div key={group}>
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="pf-section-title">
                        {group === "local"
                          ? "Local imports"
                          : group === "booru"
                          ? "Booru fetches"
                          : "Other"}{" "}
                        <span className="ml-1 font-mono text-text-subtle">
                          {list.length}
                        </span>
                      </span>
                      <div className="flex gap-1">
                        <button
                          type="button"
                          className="pf-btn-ghost h-6 px-2 text-[11px]"
                          onClick={() =>
                            setSourceIds((prev) =>
                              allSelected
                                ? prev.filter((x) => !groupIds.includes(x))
                                : Array.from(new Set([...prev, ...groupIds])),
                            )
                          }
                        >
                          {allSelected ? "deselect all" : "select all"}
                        </button>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {list.map((s) => (
                        <label
                          key={s.id}
                          className={`pf-pill cursor-pointer ${
                            sourceIds.includes(s.id)
                              ? "border-brand bg-brand/15 text-brand"
                              : ""
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="hidden"
                            checked={sourceIds.includes(s.id)}
                            onChange={(e) =>
                              setSourceIds((prev) =>
                                e.target.checked
                                  ? [...prev, s.id]
                                  : prev.filter((x) => x !== s.id),
                              )
                            }
                          />
                          {s.label} · {s.kind} ·{" "}
                          {s.image_count.toLocaleString()}
                        </label>
                      ))}
                    </div>
                  </div>
                );
              })}
              {!sources.data?.length && (
                <span className="text-xs text-text-muted">
                  no sources yet — ingest first
                </span>
              )}
              {!!sources.data?.length && !sourceIds.length && (
                <p className="text-[11px] text-text-subtle">
                  no sources picked — export will include every source.
                </p>
              )}
            </div>
          </Field>

          <Field label="Buckets" className="md:col-span-4">
            <div className="flex flex-wrap gap-2">
              {ALL_BUCKETS.map((b) => (
                <label
                  key={b}
                  className={`pf-pill cursor-pointer ${
                    buckets.includes(b)
                      ? "border-brand bg-brand/15 text-brand"
                      : ""
                  }`}
                >
                  <input
                    type="checkbox"
                    className="hidden"
                    checked={buckets.includes(b)}
                    onChange={(e) =>
                      setBuckets((prev) =>
                        e.target.checked
                          ? [...prev, b]
                          : prev.filter((x) => x !== b),
                      )
                    }
                  />
                  {b}
                </label>
              ))}
            </div>
            {buckets.includes("scene") && (
              <div className="mt-3">
                <span className="pf-label">Scene recipe</span>
                <div className="mt-1 flex flex-wrap gap-2">
                  {SCENE_RECIPE_BUCKETS.map((b) => {
                    const active = sceneBuckets.includes(b);
                    const lastOne = active && sceneBuckets.length === 1;
                    return (
                      <label
                        key={b}
                        className={`pf-pill ${
                          lastOne ? "cursor-not-allowed" : "cursor-pointer"
                        } ${active ? "border-brand bg-brand/15 text-brand" : ""}`}
                        title={
                          lastOne
                            ? "scene.txt needs at least one bucket"
                            : undefined
                        }
                      >
                        <input
                          type="checkbox"
                          className="hidden"
                          checked={active}
                          disabled={lastOne}
                          onChange={(e) =>
                            setSceneBuckets((prev) =>
                              e.target.checked
                                ? [...prev, b]
                                : prev.length > 1
                                ? prev.filter((x) => x !== b)
                                : prev,
                            )
                          }
                        />
                        {b}
                      </label>
                    );
                  })}
                </div>
                <p className="mt-1 text-[11px] text-text-subtle">
                  which per-bucket lines are joined into scene.txt (composed at
                  export time)
                </p>
              </div>
            )}
          </Field>

          <Field label="Deny tags (strip from every scene line)" className="md:col-span-4">
            <div className="space-y-3">

              {/* Row 1: built-in defaults toggle + default list preview */}
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex h-8 items-center gap-2 rounded-md border border-line bg-bg-subtle px-3 text-xs">
                  <input
                    type="checkbox"
                    checked={useDefaultDeny}
                    onChange={(e) => setUseDefaultDeny(e.target.checked)}
                    className="accent-brand"
                  />
                  use built-in defaults
                  {defaultDeny.data && (
                    <span className="ml-1 font-mono text-text-subtle">
                      ({defaultDeny.data.tags.length} tags)
                    </span>
                  )}
                </label>
                <button
                  type="button"
                  className="pf-btn-ghost h-8 text-[11px]"
                  onClick={() => setShowDenyList((v) => !v)}
                >
                  {showDenyList ? "hide" : "show"} default list
                </button>
                <span className="text-[11px] text-text-subtle">
                  exact-match per tag (canonical underscored form, case-insensitive)
                </span>
              </div>

              {showDenyList && defaultDeny.data && (
                <div className="max-h-32 overflow-y-auto rounded-md border border-line bg-bg-subtle/40 p-2 text-[11px]">
                  <div className="flex flex-wrap gap-1 font-mono">
                    {defaultDeny.data.tags.map((t) => (
                      <span
                        key={t}
                        className={`pf-pill ${
                          useDefaultDeny ? "text-text-muted" : "text-text-subtle line-through"
                        }`}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Row 2: saved list picker */}
              <div className="flex flex-wrap items-center gap-2">
                <select
                  className="pf-input h-8 w-56 text-xs"
                  value={selectedDenyListId}
                  onChange={(e) => {
                    const val = e.target.value;
                    setSelectedDenyListId(val === "" ? "" : val === "new" ? "new" : Number(val));
                    if (val === "new") {
                      // Keep the textarea — "+ new list" exists to save what
                      // the user just pasted, not to wipe it.
                      setSaveAsName("");
                    } else if (val !== "") {
                      const list = denyLists.data?.find((l) => l.id === Number(val));
                      if (list) setExtraDeny(list.tags.join("\n"));
                    }
                  }}
                >
                  <option value="">— no saved list —</option>
                  <option value="new">+ new list</option>
                  {(denyLists.data ?? []).map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name} ({l.tags.length} tags)
                    </option>
                  ))}
                </select>

                {/* Save / overwrite button */}
                {selectedDenyListId !== "" && (
                  <button
                    type="button"
                    className="pf-btn h-8 gap-1 text-[11px]"
                    disabled={savePending}
                    onClick={saveDenyList}
                  >
                    <Save size={11} />
                    {savePending
                      ? "saving…"
                      : selectedDenyListId === "new"
                      ? "save as new"
                      : "overwrite"}
                  </button>
                )}

                {/* Delete button for existing lists */}
                {typeof selectedDenyListId === "number" && (
                  <ConfirmButton
                    className="pf-btn-ghost h-8 gap-1 text-[11px] text-accent-rose hover:text-accent-rose/80"
                    disabled={deleteDenyListMut.isPending}
                    confirmLabel={
                      <>
                        <Trash2 size={11} /> really delete?
                      </>
                    }
                    onConfirm={() => {
                      const list = denyLists.data?.find(
                        (l) => l.id === selectedDenyListId,
                      );
                      if (!list) return;
                      deleteDenyListMut.mutate({ id: list.id, name: list.name });
                    }}
                  >
                    <Trash2 size={11} />{" "}
                    {deleteDenyListMut.isPending ? "deleting…" : "delete"}
                  </ConfirmButton>
                )}

                {/* Name input when creating new */}
                {selectedDenyListId === "new" && (
                  <input
                    className="pf-input h-8 w-44 text-xs"
                    placeholder="list name…"
                    value={saveAsName}
                    onChange={(e) => setSaveAsName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        saveDenyList();
                      }
                    }}
                  />
                )}
              </div>

              {/* Row 3: textarea */}
              <textarea
                className="pf-input font-mono text-xs"
                rows={4}
                placeholder="comma- or newline-separated tags, e.g. tongue_out, eye_contact&#10;Paste your DENY.txt here or load a saved list above."
                value={extraDeny}
                onChange={(e) => setExtraDeny(e.target.value)}
              />
              {parsedExtraDeny.length > 0 && (
                <div className="text-[11px] text-text-subtle">
                  {parsedExtraDeny.length} extra deny tags
                </div>
              )}
            </div>
          </Field>
        </div>
        <div className="mt-4">
          <button
            type="submit"
            className="pf-btn-primary"
            disabled={runMut.isPending || exporting}
          >
            <Download size={14} />{" "}
            {runMut.isPending
              ? "Starting…"
              : exporting
              ? "Exporting…"
              : "Run export"}
          </button>
        </div>
        </form>
      </Panel>

      {result && (
        <Panel
          title="Result"
          description={`wrote ${Object.keys(result.files).length} files`}
        >
          <div className="mb-3 flex items-center gap-1 text-xs text-text-muted">
            <FolderTree size={12} className="shrink-0" />
            <span className="truncate font-mono">{result.output_dir}</span>
            <CopyButton
              text={result.output_dir}
              title="Copy output directory"
            />
          </div>
          {resultMirrorDir && (
            <div className="mb-3 flex items-center gap-1 text-xs text-text-muted">
              <FolderTree size={12} className="shrink-0" />
              <span className="truncate font-mono">
                mirrored to {resultMirrorDir}
              </span>
              <CopyButton
                text={resultMirrorDir}
                title="Copy mirror directory"
              />
            </div>
          )}
          {result.warnings && result.warnings.length > 0 && (
            <ul className="mb-3 space-y-2 text-xs text-accent-amber">
              {result.warnings.map((w) => (
                <li key={w} className="rounded border border-accent-amber/40 bg-accent-amber/10 px-3 py-2">
                  {w}
                </li>
              ))}
            </ul>
          )}
          <ul className="space-y-1 text-xs">
            {Object.entries(result.files).map(([bucket, path]) => (
              <li
                key={bucket}
                className="flex items-center justify-between gap-2 rounded border border-line/60 bg-bg-subtle/40 px-3 py-2"
              >
                <span className="flex min-w-0 items-center gap-1">
                  <span className="truncate font-mono">{path}</span>
                  <CopyButton text={path} title="Copy file path" />
                </span>
                <span className="shrink-0 font-mono text-text-muted">
                  {(result.line_counts[bucket] ?? 0).toLocaleString()} lines
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

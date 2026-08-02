import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ExternalLink, X } from "lucide-react";
import { toast } from "sonner";

import { api, type SceneRow } from "@/lib/api";
import {
  danbooruIdFrom,
  danbooruPostUrl,
  naiSpacedTags,
  tagCount,
} from "@/lib/naiTags";
import { NaiSplitPanel } from "@/components/NaiSplitPanel";
import { Panel } from "@/components/Panel";
import { BucketBadge } from "@/components/BucketBadge";
import {
  CopyButton,
  Field,
  RatingFilter,
  SegmentedControl,
  ratingPillClass,
} from "@/components/forms";
import { useDebouncedValue } from "@/lib/useDebouncedValue";

type OriginFilter = "" | "local" | "booru";

export function Scenes() {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(100);
  const [sourceId, setSourceId] = useState<string>("");
  const [origin, setOrigin] = useState<OriginFilter>("");
  const [rating, setRating] = useState("");
  const [naiModel, setNaiModel] = useState("");
  const [scoreMin, setScoreMin] = useState<number | "">("");
  const [search, setSearch] = useState("");
  const [externalId, setExternalId] = useState("");
  const [hasOutfit, setHasOutfit] = useState(false);
  const [hasBackground, setHasBackground] = useState(false);
  const [multiChar, setMultiChar] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const debouncedNaiModel = useDebouncedValue(naiModel, 250);
  const debouncedScoreMin = useDebouncedValue(scoreMin, 250);
  const debouncedSearch = useDebouncedValue(search, 250);
  const debouncedExternalId = useDebouncedValue(externalId, 250);

  useEffect(() => {
    if (selectedId === null) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setSelectedId(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId]);

  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: api.listSources,
  });

  const scenes = useQuery({
    queryKey: [
      "scenes",
      page,
      pageSize,
      sourceId,
      origin,
      rating,
      debouncedNaiModel,
      debouncedScoreMin,
      debouncedSearch,
      debouncedExternalId,
      hasOutfit,
      hasBackground,
      multiChar,
    ],
    queryFn: () =>
      api.listScenes({
        offset: page * pageSize,
        limit: pageSize,
        source_id: sourceId ? Number(sourceId) : undefined,
        origin: origin || undefined,
        rating: rating || undefined,
        nai_model: debouncedNaiModel || undefined,
        score_min: typeof debouncedScoreMin === "number" ? debouncedScoreMin : undefined,
        search: debouncedSearch || undefined,
        external_id: danbooruIdFrom(debouncedExternalId) || undefined,
        has_outfit: hasOutfit,
        has_background: hasBackground,
        multi_char: multiChar,
      }),
    placeholderData: keepPreviousData,
  });

  const total = scenes.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // Filters can shrink the result set while the pager still shows the old
  // total (debounce window) — clamp back instead of stranding the user on
  // an empty page past the end.
  useEffect(() => {
    if (page > 0 && page >= totalPages) setPage(totalPages - 1);
  }, [page, totalPages]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Scenes</h1>
        <p className="text-sm text-text-muted">
          Browse ingested images and inspect per-bucket tag groupings.
        </p>
      </div>

      <Panel
        title="Filters"
        actions={
          <div className="text-xs text-text-muted">
            {total.toLocaleString()} scenes
          </div>
        }
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Origin">
            <SegmentedControl<OriginFilter>
              options={[
                { value: "", label: "all" },
                { value: "local", label: "local" },
                { value: "booru", label: "booru" },
              ]}
              value={origin}
              onChange={(v) => {
                setOrigin(v);
                setPage(0);
              }}
            />
          </Field>
          <Field label="Source">
            <select
              className="pf-input"
              value={sourceId}
              onChange={(e) => {
                setSourceId(e.target.value);
                setPage(0);
              }}
            >
              <option value="">all</option>
              {(sources.data ?? [])
                .filter((s) => !origin || s.origin === origin)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label} · {s.kind}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Rating">
            <RatingFilter
              value={rating}
              onChange={(v) => {
                setRating(v);
                setPage(0);
              }}
            />
          </Field>
          <Field label="NAI model">
            <input
              className="pf-input font-mono"
              value={naiModel}
              onChange={(e) => {
                setNaiModel(e.target.value);
                setPage(0);
              }}
              placeholder="V4.5 …"
            />
          </Field>
          <Field label="Score min">
            <input
              type="number"
              className="pf-input"
              value={scoreMin}
              onChange={(e) => {
                setScoreMin(e.target.value === "" ? "" : Number(e.target.value));
                setPage(0);
              }}
            />
          </Field>
          <Field label="Search in prompt" className="md:col-span-2">
            <input
              className="pf-input font-mono"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="2girls, serafuku, hug …"
            />
          </Field>
          <Field label="Danbooru ID / URL">
            <input
              className="pf-input font-mono"
              value={externalId}
              onChange={(e) => {
                setExternalId(e.target.value);
                setPage(0);
              }}
              placeholder="11813281 or post URL"
            />
          </Field>
          <Field label="Has outfit line">
            <label className="flex h-9 items-center gap-2 rounded-md border border-line bg-bg-subtle px-3 text-sm">
              <input
                type="checkbox"
                checked={hasOutfit}
                onChange={(e) => {
                  setHasOutfit(e.target.checked);
                  setPage(0);
                }}
                className="accent-brand"
              />
              outfit only
            </label>
          </Field>
          <Field label="Has background line">
            <label className="flex h-9 items-center gap-2 rounded-md border border-line bg-bg-subtle px-3 text-sm">
              <input
                type="checkbox"
                checked={hasBackground}
                onChange={(e) => {
                  setHasBackground(e.target.checked);
                  setPage(0);
                }}
                className="accent-brand"
              />
              background only
            </label>
          </Field>
          <Field label="Subjects">
            <label className="flex h-9 items-center gap-2 rounded-md border border-line bg-bg-subtle px-3 text-sm">
              <input
                type="checkbox"
                checked={multiChar}
                onChange={(e) => {
                  setMultiChar(e.target.checked);
                  setPage(0);
                }}
                className="accent-brand"
              />
              multi-character only
            </label>
          </Field>
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_24rem]">
        <Panel
          title="Results"
          actions={
            <div className="flex items-center gap-2">
              <button
                className="pf-btn-ghost"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
              >
                <ChevronLeft size={14} />
              </button>
              <span className="font-mono text-xs">
                {page + 1} / {totalPages}
              </span>
              <button
                className="pf-btn-ghost"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page + 1 >= totalPages}
              >
                <ChevronRight size={14} />
              </button>
              <select
                className="pf-input ml-2 h-8 w-auto"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(0);
                }}
              >
                {[50, 100, 200, 500].map((n) => (
                  <option key={n} value={n}>
                    {n} / page
                  </option>
                ))}
              </select>
            </div>
          }
          bodyClassName="p-0"
        >
          <SceneTable
            rows={scenes.data?.items ?? []}
            onSelect={setSelectedId}
            selectedId={selectedId}
            loading={scenes.isLoading}
          />
        </Panel>

        <SceneDetailPanel
          id={selectedId}
          onClose={() => setSelectedId(null)}
        />
      </div>
    </div>
  );
}

function SceneTable({
  rows,
  onSelect,
  selectedId,
  loading,
}: {
  rows: SceneRow[];
  onSelect: (id: number) => void;
  selectedId: number | null;
  loading: boolean;
}) {
  if (loading) return <div className="p-6 text-sm text-text-muted">Loading…</div>;
  if (!rows.length)
    return (
      <div className="p-6 text-sm text-text-muted">
        No scenes match these filters.
      </div>
    );

  return (
    <div className="max-h-[68vh] overflow-y-auto">
      <table className="w-full table-fixed text-xs">
        <thead className="sticky top-0 z-10 bg-bg-panel text-text-muted">
          <tr className="border-b border-line">
            <th className="w-16 px-2 py-2 text-right">#</th>
            <th className="w-40 px-2 py-2 text-left">External</th>
            <th className="w-16 px-2 py-2 text-left">Rating</th>
            <th className="w-16 px-2 py-2 text-right">Score</th>
            <th className="w-32 px-2 py-2 text-left">Model</th>
            <th className="px-2 py-2 text-left">Bucket previews</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              className={`cursor-pointer border-b border-line/60 transition ${
                selectedId === r.id ? "bg-brand/10" : "hover:bg-bg-subtle/60"
              }`}
              tabIndex={0}
              aria-current={selectedId === r.id ? "true" : undefined}
              onClick={() => onSelect(r.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(r.id);
                }
              }}
            >
              <td className="px-2 py-2 text-right font-mono tabular-nums text-text-subtle">
                {r.id}
              </td>
              <td className="truncate px-2 py-2 font-mono">{r.external_id}</td>
              <td className="px-2 py-2 font-mono">{r.rating ?? "—"}</td>
              <td className="px-2 py-2 text-right font-mono tabular-nums">
                {r.score ?? "—"}
              </td>
              <td className="truncate px-2 py-2 font-mono">
                {r.nai_model ?? r.software ?? "—"}
              </td>
              <td className="truncate px-2 py-2">
                {Object.entries(r.buckets)
                  .filter(([b]) => b !== "scene")
                  .slice(0, 3)
                  .map(([b, txt]) => (
                    <span
                      key={b}
                      className="mr-2 inline-flex items-center gap-1"
                      title={txt}
                    >
                      <BucketBadge bucket={b} />
                      <span className="truncate text-text-muted">
                        {txt.length > 50 ? `${txt.slice(0, 50)}…` : txt}
                      </span>
                    </span>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SceneDetailPanel({
  id,
  onClose,
}: {
  id: number | null;
  onClose: () => void;
}) {
  const [showSplit, setShowSplit] = useState(false);
  useEffect(() => setShowSplit(false), [id]);
  const detail = useQuery({
    queryKey: ["scene", id],
    queryFn: () => api.getScene(id!),
    enabled: id !== null,
  });

  if (id === null) {
    return (
      <Panel title="Detail">
        <div className="p-4 text-sm text-text-muted">
          Click a row to see the per-bucket tag breakdown.
        </div>
      </Panel>
    );
  }

  const d = detail.data;
  return (
    <Panel
      title={d ? `Scene #${d.id}` : "Loading…"}
      description={d?.external_id}
      actions={
        <button
          className="pf-btn-ghost"
          onClick={onClose}
          aria-label="Close scene detail"
          title="Close"
        >
          <X size={14} />
        </button>
      }
    >
      {d ? (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            <span className={`pf-pill ${ratingPillClass(d.rating)}`}>
              rating {d.rating ?? "—"}
              {d.rating_source ? ` · ${d.rating_source}` : ""}
            </span>
            <span className="pf-pill">score {d.score ?? "—"}</span>
            {d.subjects && (
              <span
                className="pf-pill border-brand/40 font-mono text-brand"
                title="Character count tags in this image"
              >
                {d.subjects}
              </span>
            )}
            {d.nai_model && <span className="pf-pill">{d.nai_model}</span>}
            {d.software && <span className="pf-pill">{d.software}</span>}
            {danbooruPostUrl(d.external_id, d.origin) && (
              <a
                href={danbooruPostUrl(d.external_id, d.origin)!}
                target="_blank"
                rel="noopener noreferrer"
                className="pf-pill inline-flex items-center gap-1 hover:border-brand hover:text-text"
                title="Open this post on Danbooru"
              >
                <ExternalLink size={10} /> danbooru
              </a>
            )}
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="pf-section-title">
                Full tags{" "}
                <span className="font-normal normal-case text-text-subtle">
                  — {tagCount(d.raw_prompt)} tags, unfiltered
                </span>
              </h3>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="pf-btn h-7 px-2 text-xs"
                  title="Copy the entire tag list with spaces (NovelAI-ready)"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(
                        naiSpacedTags(d.raw_prompt),
                      );
                      toast.success("Full tag list copied (NAI format)");
                    } catch {
                      toast.error("Clipboard unavailable");
                    }
                  }}
                >
                  Copy for NAI
                </button>
                <button
                  type="button"
                  className="pf-btn h-7 px-2 text-xs"
                  title="Split into NAI base + character prompts with the local LLM"
                  onClick={() => setShowSplit((v) => !v)}
                >
                  {showSplit ? "Hide splitter" : "Split for NAI"}
                </button>
                <CopyButton
                  text={d.raw_prompt}
                  title="Copy raw (underscore form)"
                />
              </div>
            </div>
            {showSplit && (
              <div className="mb-3 rounded border border-line bg-bg-subtle/40 p-2">
                <NaiSplitPanel
                  resolveInput={() => ({
                    tags: naiSpacedTags(d.raw_prompt),
                    label: `scene #${d.id} full tags`,
                  })}
                />
              </div>
            )}
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-line bg-bg p-2 font-mono text-[11px] text-text-muted">
              {naiSpacedTags(d.raw_prompt)}
            </pre>
            {d.raw_negative ? (
              <div className="mt-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="pf-label">negative prompt</span>
                  <CopyButton text={d.raw_negative} title="Copy negative prompt" />
                </div>
                <pre className="max-h-24 overflow-auto whitespace-pre-wrap rounded border border-line bg-bg p-2 font-mono text-[11px] text-text-subtle">
                  {d.raw_negative}
                </pre>
              </div>
            ) : null}
          </div>
          {d.rating_evidence?.length ? (
            <div className="text-xs text-text-muted">
              <span className="pf-section-title mr-2">rating evidence</span>
              <span className="font-mono">
                {d.rating_evidence.map((e) => e.replace(/_/g, " ")).join(", ")}
              </span>
            </div>
          ) : null}
          <div>
            <h3 className="pf-section-title mb-2">Buckets</h3>
            <ul className="space-y-2">
              {Object.entries(d.buckets).map(([b, txt]) => (
                <li
                  key={b}
                  className="rounded border border-line bg-bg-subtle/60 p-2"
                >
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <BucketBadge bucket={b} />
                    <CopyButton text={txt} title={`Copy ${b} tags`} />
                  </div>
                  <div className="font-mono text-xs text-text">{txt}</div>
                </li>
              ))}
              {!Object.keys(d.buckets).length && (
                <li className="text-text-muted">
                  No bucket lines were derived — all tags fell into "other".
                </li>
              )}
            </ul>
          </div>
        </div>
      ) : (
        <div className="text-sm text-text-muted">Loading…</div>
      )}
    </Panel>
  );
}

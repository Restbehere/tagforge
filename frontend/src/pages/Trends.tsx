import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";
import { ClipboardList, Download, Heart, Pencil, Check, Share2, X } from "lucide-react";
import { toast } from "sonner";

import { api, SourceRow } from "@/lib/api";
import { ASSIGN_BUCKETS } from "@/lib/buckets";
import { invalidateTagDerived } from "@/lib/invalidate";
import { Panel } from "@/components/Panel";
import { BucketBadge } from "@/components/BucketBadge";
import { useChartColors } from "@/lib/useChartColors";

const BUCKETS = [
  { value: "character", label: "character (popular now)" },
  { value: "outfit",    label: "outfit" },
  { value: "pose",      label: "pose" },
  { value: "expression",label: "expression" },
  { value: "background",label: "background" },
  { value: "composition",label:"composition" },
  { value: "accessory", label: "accessory" },
  { value: "extras",    label: "extras" },
  { value: "",          label: "all buckets" },
];

const EDIT_BUCKETS = ASSIGN_BUCKETS;

const COMPARE_PRESETS = [
  { value: "0",      label: "previous period" },
  { value: "7",      label: "1 week earlier" },
  { value: "30",     label: "1 month earlier" },
  { value: "custom", label: "custom…" },
];

export function Trends() {
  const qc = useQueryClient();
  const chart = useChartColors();
  const [recentDays, setRecentDays]   = useState(7);
  const [baselineDays, setBaselineDays] = useState(30);
  const [bucket, setBucket]           = useState("character");
  const [compareOffset, setCompareOffset] = useState(0);
  const [comparePreset, setComparePreset] = useState("0");
  const [booruOnly, setBooruOnly]     = useState(true);
  const [sourceId, setSourceId]       = useState<number | "">("");
  const [exportTop, setExportTop]     = useState(100);
  const [editingTag, setEditingTag]   = useState<string | null>(null);
  const [editBucket, setEditBucket]   = useState("");
  const [editSaving, setEditSaving]   = useState(false);
  const [editError, setEditError]     = useState<string | null>(null);

  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => api.listSources(),
  });

  const trends = useQuery({
    queryKey: ["trends", recentDays, baselineDays, bucket, booruOnly, sourceId, compareOffset],
    queryFn: () =>
      api.trendDelta({
        recent_days: recentDays,
        baseline_days: baselineDays,
        bucket: bucket || undefined,
        booru_only: booruOnly,
        source_id: sourceId || undefined,
        compare_offset_days: compareOffset || undefined,
      }),
  });

  const featureLog = useQuery({
    queryKey: ["feature-log"],
    queryFn: () => api.listFeatureLog(),
    enabled: bucket === "character",
  });

  /* Latest feature per character — rows arrive newest-first, so the first
   * occurrence wins. */
  const latestFeature = useMemo(() => {
    const m = new Map<string, { channel: "x" | "patreon"; at: string }>();
    for (const row of featureLog.data ?? []) {
      if (!m.has(row.character)) m.set(row.character, { channel: row.channel, at: row.at });
    }
    return m;
  }, [featureLog.data]);

  const logMut = useMutation({
    mutationFn: ({ character, channel }: { character: string; channel: "x" | "patreon" }) =>
      api.logFeature(character, channel),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["feature-log"] });
      toast.success(`marked ${vars.character} featured on ${vars.channel}`);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to log feature");
    },
  });

  const chartData =
    trends.data?.items.slice(0, 25).map((i) => ({
      name: i.name,
      ratio: Number(i.ratio.toFixed(2)),
      recent: i.recent,
      baseline: i.baseline,
    })) ?? [];

  function handleExport() {
    const url = api.trendExportUrl({
      recent_days: recentDays,
      baseline_days: baselineDays,
      bucket: bucket || undefined,
      booru_only: booruOnly,
      source_id: sourceId || undefined,
      compare_offset_days: compareOffset || undefined,
      top: exportTop,
    });
    const a = document.createElement("a");
    a.href = url;
    a.download = `trends_${bucket || "all"}_top${exportTop}.txt`;
    a.click();
  }

  async function handleCopyPoll() {
    const rows = trends.data?.items ?? [];
    const n = Math.min(exportTop, rows.length);
    const text = rows
      .slice(0, n)
      .map((r, idx) => `${idx + 1}. ${r.name}`)
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`copied ${n}-character poll list`);
    } catch {
      toast.error("Clipboard unavailable");
    }
  }

  function startEdit(tagName: string, currentBucket: string) {
    setEditingTag(tagName);
    setEditBucket(currentBucket || EDIT_BUCKETS[0]);
    setEditError(null);
  }

  async function confirmEdit(tagName: string) {
    setEditSaving(true);
    setEditError(null);
    try {
      await api.setTagBucket(tagName, editBucket);
      invalidateTagDerived(qc);
      setEditingTag(null);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setEditSaving(false);
    }
  }

  function cancelEdit() {
    setEditingTag(null);
    setEditError(null);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Trends</h1>
        <p className="text-sm text-text-muted">
          Per-tag frequency delta in the recent window vs the prior baseline window.
        </p>
      </div>

      <Panel title="Window">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <Field label="Recent (days)">
            <input
              type="number"
              className="pf-input"
              value={recentDays}
              min={1}
              onChange={(e) => setRecentDays(Number(e.target.value))}
            />
          </Field>
          <Field label="Baseline (days)">
            <input
              type="number"
              className="pf-input disabled:opacity-40"
              value={baselineDays}
              min={1}
              disabled={compareOffset > 0}
              title={
                compareOffset > 0
                  ? "Unused while an offset compare is active — the baseline is the same-length window shifted back instead."
                  : undefined
              }
              onChange={(e) => setBaselineDays(Number(e.target.value))}
            />
          </Field>
          <Field label="Compare vs">
            <div className="flex items-center gap-1">
              <select
                className="pf-input min-w-0 flex-1"
                value={comparePreset}
                onChange={(e) => {
                  const v = e.target.value;
                  setComparePreset(v);
                  if (v !== "custom") setCompareOffset(Number(v));
                }}
              >
                {COMPARE_PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              {comparePreset === "custom" && (
                <input
                  type="number"
                  className="pf-input w-16 shrink-0"
                  value={compareOffset}
                  min={0}
                  max={365}
                  title="Offset in days — the baseline becomes the same-length window ending this many days earlier"
                  onChange={(e) =>
                    setCompareOffset(
                      Math.min(365, Math.max(0, Number(e.target.value))),
                    )
                  }
                />
              )}
            </div>
          </Field>
          <Field label="Bucket">
            <select
              className="pf-input"
              value={bucket}
              onChange={(e) => setBucket(e.target.value)}
            >
              {BUCKETS.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Source">
            <select
              className="pf-input"
              value={sourceId}
              onChange={(e) =>
                setSourceId(e.target.value === "" ? "" : Number(e.target.value))
              }
            >
              <option value="">All sources</option>
              {(sources.data ?? []).map((src: SourceRow) => (
                <option key={src.id} value={src.id}>
                  {src.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="">
            <label className="flex h-10 cursor-pointer items-center gap-2 rounded-md border border-line bg-bg-subtle px-3 text-xs">
              <input
                type="checkbox"
                checked={booruOnly}
                onChange={(e) => setBooruOnly(e.target.checked)}
                className="accent-brand"
              />
              Danbooru only
            </label>
          </Field>
        </div>
        <p className="mt-2 text-[11px] text-text-muted">
          <span className="font-medium">Recent</span> = last {recentDays} day{recentDays !== 1 ? "s" : ""}.&nbsp;
          <span className="font-medium">Baseline</span> ={" "}
          {compareOffset > 0
            ? `the same ${recentDays}-day window ending ${compareOffset} days earlier.`
            : `the ${baselineDays} days before that.`}&nbsp;
          Ratio = (recent + 1) / (baseline + 1) — higher means rising trend.
          Windows use the Danbooru post date for newly ingested data; older rows fall back to ingest time.
        </p>
      </Panel>

      <Panel title={`Top ${chartData.length} tags by recent/baseline ratio`}>
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} />
              <XAxis
                dataKey="name"
                tick={{ fill: chart.tick, fontSize: 10 }}
                interval={0}
                angle={-30}
                textAnchor="end"
                height={60}
              />
              <YAxis tick={{ fill: chart.tick, fontSize: 11 }} />
              <Tooltip
                cursor={{ fill: chart.cursor }}
                contentStyle={{
                  backgroundColor: chart.tooltipBg,
                  border: `1px solid ${chart.tooltipBorder}`,
                  borderRadius: 8,
                  fontSize: 12,
                }}
              />
              <Bar dataKey="ratio" fill={chart.bar} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel
        title="Table"
        bodyClassName="p-0"
        actions={
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted">Export top</span>
            <input
              type="number"
              className="pf-input h-7 w-20 text-xs"
              value={exportTop}
              min={1}
              max={1000}
              onChange={(e) => setExportTop(Math.max(1, Number(e.target.value)))}
            />
            <button
              className="pf-btn h-7 gap-1 text-[11px]"
              onClick={handleExport}
              title={`Download top ${exportTop} trending ${bucket || "tags"} as plain text (one per line)`}
            >
              <Download size={11} /> .txt
            </button>
            {bucket === "character" && (
              <button
                className="pf-btn h-7 gap-1 text-[11px]"
                onClick={handleCopyPoll}
                title={`Copy top ${exportTop} characters as a numbered poll list (for Patreon)`}
              >
                <ClipboardList size={11} /> Copy poll
              </button>
            )}
          </div>
        }
      >
        <div className="max-h-[60vh] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-bg-panel text-text-muted">
              <tr className="border-b border-line">
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">Tag</th>
                <th className="px-3 py-2 text-left">Bucket</th>
                <th className="px-3 py-2 text-right">Recent</th>
                <th className="px-3 py-2 text-right">Baseline</th>
                <th className="px-3 py-2 text-right">Ratio</th>
                {bucket === "character" && (
                  <th className="px-3 py-2 text-left">Featured</th>
                )}
                <th className="px-2 py-2 text-center w-8"></th>
              </tr>
            </thead>
            <tbody>
              {(trends.data?.items ?? []).map((i, idx) => (
                <tr key={`${i.tag_id}-${i.name}`} className="border-b border-line/60">
                  <td className="px-3 py-2 font-mono text-text-subtle">{idx + 1}</td>
                  <td className="px-3 py-2 font-mono">
                    {editingTag === i.name ? (
                      <div className="flex flex-col gap-0.5">
                        <div className="flex items-center gap-1">
                          <select
                            className="pf-input h-6 py-0 text-xs"
                            value={editBucket}
                            onChange={(e) => setEditBucket(e.target.value)}
                            disabled={editSaving}
                            autoFocus
                          >
                            {EDIT_BUCKETS.map((b) => (
                              <option key={b} value={b}>{b}</option>
                            ))}
                          </select>
                          <button
                            className="rounded p-1 text-accent-green hover:bg-bg-subtle disabled:opacity-40"
                            onClick={() => confirmEdit(i.name)}
                            disabled={editSaving}
                            title="Save"
                          >
                            <Check size={12} />
                          </button>
                          <button
                            className="rounded p-1 text-text-muted hover:bg-bg-subtle disabled:opacity-40"
                            onClick={cancelEdit}
                            disabled={editSaving}
                            title="Cancel"
                          >
                            <X size={12} />
                          </button>
                          {editSaving && (
                            <span className="text-[10px] text-text-muted">saving…</span>
                          )}
                        </div>
                        {editError && (
                          <span className="text-[10px] text-accent-rose">{editError}</span>
                        )}
                      </div>
                    ) : (
                      <span className="inline-flex items-center gap-1.5">
                        {i.name}
                        {bucket === "character" && i.baseline === 0 && i.recent >= 3 && (
                          <span className="rounded bg-accent-green/15 px-1 py-0.5 text-[9px] font-semibold uppercase text-accent-green">
                            new
                          </span>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <BucketBadge bucket={i.bucket} />
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{i.recent}</td>
                  <td className="px-3 py-2 text-right font-mono">{i.baseline}</td>
                  <td className="px-3 py-2 text-right font-mono">{i.ratio.toFixed(2)}</td>
                  {bucket === "character" && (
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex items-center gap-1">
                        <span className="text-text-subtle">
                          {(() => {
                            const feat = latestFeature.get(i.name);
                            return feat ? `${feat.channel} · ${relativeTime(feat.at)}` : "—";
                          })()}
                        </span>
                        <button
                          className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-bg-subtle hover:text-text disabled:opacity-40"
                          onClick={() => logMut.mutate({ character: i.name, channel: "x" })}
                          disabled={logMut.isPending}
                          title="Mark featured on X"
                        >
                          <Share2 size={11} />
                        </button>
                        <button
                          className="flex h-6 w-6 items-center justify-center rounded text-text-muted hover:bg-bg-subtle hover:text-text disabled:opacity-40"
                          onClick={() => logMut.mutate({ character: i.name, channel: "patreon" })}
                          disabled={logMut.isPending}
                          title="Mark featured on Patreon"
                        >
                          <Heart size={11} />
                        </button>
                      </div>
                    </td>
                  )}
                  <td className="px-2 py-2 text-center">
                    {editingTag !== i.name && (
                      <button
                        className="rounded p-1 text-text-muted hover:text-text hover:bg-bg-subtle"
                        onClick={() => startEdit(i.name, i.bucket)}
                        title={`Move "${i.name}" to a different bucket`}
                      >
                        <Pencil size={11} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!trends.data?.items?.length && (
                <tr>
                  <td
                    colSpan={bucket === "character" ? 8 : 7}
                    className="px-3 py-6 text-center text-text-muted"
                  >
                    Not enough data yet — ingest more dated records to populate the window.
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

/** Compact relative timestamp: "5m ago" / "3h ago" / "3d ago" / "2w ago". */
function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return `${Math.floor(days / 7)}w ago`;
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="pf-label">{label || " "}</label>
      <div className="mt-1">{children}</div>
    </div>
  );
}

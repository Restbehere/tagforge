import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";

import { api } from "@/lib/api";
import { Panel, Stat } from "@/components/Panel";
import { BucketBadge } from "@/components/BucketBadge";
import { CountUp } from "@/components/CountUp";

export function Dashboard() {
  const summary = useQuery({
    queryKey: ["dashboard"],
    queryFn: api.dashboard,
    refetchInterval: 5_000,
  });

  const d = summary.data;
  const pending = summary.isPending;

  /* Honest loading: skeleton while pending, em-dash if the query failed —
   * never format undefined into a fake "0". */
  function num(n: number | undefined): ReactNode {
    if (n !== undefined) return <CountUp value={n} />;
    if (pending) {
      return <div className="h-7 w-16 animate-pulse rounded bg-bg-subtle" />;
    }
    return "—";
  }

  /* Non-mutating descending sort for both bucket lists (sorting the cached
   * array in place would mutate TanStack Query's cache). */
  const sceneByBucket = [...(d?.scene_by_bucket ?? [])].sort(
    (a, b) => b.count - a.count,
  );
  const byBucket = [...(d?.by_bucket ?? [])].sort((a, b) => b.count - a.count);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="text-sm text-text-muted">
          Overview of ingested images, classified tags, and recent jobs.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Images"
          value={num(d?.image_count)}
          hint="per-image scene rows in DB"
        />
        <Stat
          label="Tags"
          value={num(d?.tag_count)}
          hint={
            d
              ? `coverage ${(d.classifier_coverage * 100).toFixed(1)}%`
              : "coverage —"
          }
        />
        <Stat
          label="Scene lines"
          value={num(d?.scene_count)}
          hint="across all buckets"
        />
        <Stat
          label="Sources"
          value={num(d?.source_count)}
          hint="metadata files + booru fetches"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel
          title="Scene lines by bucket"
          description="One row per image × bucket — these become wildcard file lines on export."
        >
          <ul className="space-y-2">
            {pending && <BucketListSkeleton />}
            {sceneByBucket.map((row) => (
              <BucketRow
                key={row.bucket}
                bucket={row.bucket}
                count={row.count}
                max={sceneByBucket[0]?.count ?? 0}
              />
            ))}
            {d && !sceneByBucket.length && (
              <li className="text-sm text-text-muted">
                No scenes yet — run an ingest from the{" "}
                <Link className="text-brand hover:underline" href="/ingest">
                  Ingest tab
                </Link>
                .
              </li>
            )}
            {summary.isError && !d && (
              <li className="text-sm text-text-muted">
                Couldn't reach the backend — retrying…
              </li>
            )}
          </ul>
        </Panel>

        <Panel
          title="Tags by bucket"
          description="Distinct canonical tags assigned to each bucket."
        >
          <ul className="space-y-2">
            {pending && <BucketListSkeleton />}
            {byBucket.map((row) => (
              <BucketRow
                key={row.bucket}
                bucket={row.bucket}
                count={row.count}
                max={byBucket[0]?.count ?? 0}
              />
            ))}
            {d && !byBucket.length && (
              <li className="text-sm text-text-muted">No tags ingested yet.</li>
            )}
            {summary.isError && !d && (
              <li className="text-sm text-text-muted">
                Couldn't reach the backend — retrying…
              </li>
            )}
          </ul>
        </Panel>
      </div>

      <Panel
        title="Recent jobs"
        description="Background ingest, scrape and export runs."
      >
        <ul className="divide-y divide-line">
          {(d?.recent_jobs ?? []).map((job) => (
            <li
              key={job.id}
              className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <div className="truncate text-sm">
                  <span className="font-medium">{job.label}</span>
                  <span className="ml-2 text-xs text-text-muted">{job.kind}</span>
                </div>
                <div className="font-mono text-xs text-text-subtle">
                  {job.message || job.status}
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs text-text-muted">
                <StatusBadge status={job.status} />
                {job.status !== "done" && job.status !== "error" && (
                  <span className="font-mono">
                    {Math.round((job.progress ?? 0) * 100)}%
                  </span>
                )}
              </div>
            </li>
          ))}
          {d && !d.recent_jobs.length && (
            <li className="py-2 text-sm text-text-muted">No jobs yet.</li>
          )}
          {summary.isError && !d && (
            <li className="py-2 text-sm text-text-muted">
              Couldn't reach the backend — retrying…
            </li>
          )}
        </ul>
      </Panel>

      <Panel
        title="Environment"
        description="Auto-detected paths used as defaults."
      >
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="pf-label">Default metadata file</dt>
            <dd className="break-all font-mono text-xs">
              {d?.default_metadata_path ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="pf-label">Kohaku-NAI wildcards dir</dt>
            <dd className="break-all font-mono text-xs">
              {d?.default_wildcards_dir ?? "—"}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="pf-label">Kohaku tags.jsonl present</dt>
            <dd className="text-xs">
              {!d ? (
                <span className="text-text-subtle">—</span>
              ) : d.kohaku_tags_jsonl_exists ? (
                <span className="text-accent-green">yes — used for category lookups</span>
              ) : (
                <span className="text-text-muted">
                  no — Stage 1 will fall back to tag_tree.json only
                </span>
              )}
            </dd>
          </div>
        </dl>
      </Panel>
    </div>
  );
}

/** Bucket list row: badge + thin proportional bar + count, so the
 * distribution is scannable at a glance. */
function BucketRow({
  bucket,
  count,
  max,
}: {
  bucket: string;
  count: number;
  max: number;
}) {
  const pct = max > 0 ? (count / max) * 100 : 0;
  return (
    <li className="flex items-center gap-3">
      <BucketBadge bucket={bucket} />
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg-subtle">
        <div
          className="h-full rounded-full bg-brand/20"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono text-sm">{count.toLocaleString()}</span>
    </li>
  );
}

function BucketListSkeleton() {
  return (
    <>
      {Array.from({ length: 4 }, (_, i) => (
        <li key={i} className="h-6 animate-pulse rounded bg-bg-subtle" />
      ))}
    </>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    pending: "bg-line text-text-muted",
    running: "bg-brand/15 text-brand",
    done: "bg-accent-green/15 text-accent-green",
    error: "bg-accent-rose/15 text-accent-rose",
    cancelled: "bg-bg-subtle text-text-subtle",
  };
  return (
    <span className={`pf-pill ${map[status] ?? "bg-line"} uppercase`}>
      {status}
    </span>
  );
}

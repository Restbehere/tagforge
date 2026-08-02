import { useEffect, useRef, useState } from "react";
import { CheckCircle2, AlertCircle, Loader2, X } from "lucide-react";

import { subscribeJobStream, type JobSummary } from "@/lib/api";
import { cn } from "@/lib/cn";

export function JobProgress({
  jobId,
  onDone,
  onDismiss,
}: {
  jobId: number;
  onDone?: (job: JobSummary) => void;
  onDismiss?: () => void;
}) {
  const [job, setJob] = useState<JobSummary | null>(null);

  // Keep callbacks in refs so the SSE effect depends only on jobId —
  // parent re-renders (route changes, theme toggles) must not tear down
  // and reopen the stream.
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    let notified = false;
    const es = subscribeJobStream(jobId, (event) => {
      setJob(event);
      if (event.status === "done" || event.status === "error") {
        // Close before notifying: the server ends the stream after a
        // terminal event and the browser would otherwise auto-reconnect,
        // replaying the done snapshot (and re-firing onDone) forever.
        es.close();
        if (!notified) {
          notified = true;
          onDoneRef.current?.(event);
        }
      }
    });
    return () => es.close();
  }, [jobId]);

  if (!job) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-md border border-line bg-bg-subtle/70 p-3 text-sm text-text-muted">
        <span className="flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" />
          connecting to job #{jobId}…
        </span>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-text-subtle transition hover:text-text"
            aria-label="dismiss"
            title="Dismiss (job may no longer exist)"
          >
            <X size={14} />
          </button>
        )}
      </div>
    );
  }

  const pct = Math.round((job.progress ?? 0) * 100);
  return (
    <div className="space-y-2 rounded-md border border-line bg-bg-subtle/70 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            {job.status === "done" && (
              <CheckCircle2 size={14} className="text-accent-green" />
            )}
            {job.status === "error" && (
              <AlertCircle size={14} className="text-accent-rose" />
            )}
            {job.status === "running" && (
              <Loader2 size={14} className="animate-spin text-brand" />
            )}
            <span className="truncate">{job.label}</span>
          </div>
          <div className="mt-1 font-mono text-xs text-text-muted">
            {job.message || job.status}
          </div>
          {job.error && (
            <div className="mt-1 text-xs text-accent-rose">{job.error}</div>
          )}
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-text-subtle transition hover:text-text"
            aria-label="dismiss"
            title={
              job.status === "done" || job.status === "error"
                ? "Dismiss"
                : "Hide — the job keeps running in the background"
            }
          >
            <X size={14} />
          </button>
        )}
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-bg">
        <div
          className={cn(
            "h-full transition-all",
            job.status === "error" ? "bg-accent-rose" : "bg-brand",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex justify-end font-mono text-[10px] text-text-subtle">
        {pct}%
      </div>
    </div>
  );
}

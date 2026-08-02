/* Always-visible stack of running/finished background jobs. Rendered once
 * in Layout so job progress survives page navigation. */

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { JobProgress } from "@/components/JobProgress";
import { jobStore, useActiveJobs } from "@/lib/jobStore";

export function GlobalJobTray() {
  const jobs = useActiveJobs();
  const qc = useQueryClient();

  // Stable identity — Layout re-renders on every navigation and must not
  // churn JobProgress's SSE effect through a fresh callback prop.
  const handleDone = useCallback(() => {
    // A finished ingest/classify job changes counts everywhere.
    qc.invalidateQueries();
  }, [qc]);

  if (!jobs.length) return null;

  return (
    <div className="fixed bottom-4 left-4 z-40 w-80 space-y-2">
      {jobs.map((id) => (
        <div key={id} className="rounded-md bg-bg-panel shadow-panel">
          <JobProgress
            jobId={id}
            onDone={handleDone}
            onDismiss={() => jobStore.remove(id)}
          />
        </div>
      ))}
    </div>
  );
}

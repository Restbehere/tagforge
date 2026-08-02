/* Global registry of running background jobs (ingest, classify, ...).
 * Lifted out of page-local state so navigating away doesn't orphan the
 * progress UI. Persisted so a reload re-attaches to still-running jobs. */

import { useSyncExternalStore } from "react";

const KEY = "tagforge.activeJobs";

function load(): number[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "number") : [];
  } catch {
    return [];
  }
}

let jobs: number[] = load();
const listeners = new Set<() => void>();

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify(jobs));
  } catch {
    /* private mode */
  }
}

function emit() {
  listeners.forEach((l) => l());
}

export const jobStore = {
  add(id: number) {
    if (jobs.includes(id)) return;
    jobs = [id, ...jobs];
    persist();
    emit();
  },
  remove(id: number) {
    if (!jobs.includes(id)) return;
    jobs = jobs.filter((j) => j !== id);
    persist();
    emit();
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot(): number[] {
    return jobs;
  },
};

export function useActiveJobs(): number[] {
  return useSyncExternalStore(jobStore.subscribe, jobStore.getSnapshot);
}

import type { QueryClient } from "@tanstack/react-query";

/** Invalidate every query whose data depends on a tag's bucket.
 *
 * A tag edit made on one page changes what the others show; with the
 * global 30s staleTime, invalidating only the calling page's key leaves
 * the rest displaying the pre-edit bucket until the timer expires. */
export function invalidateTagDerived(qc: QueryClient): void {
  for (const key of [
    ["tags"],
    ["tags-review"],
    ["tag-history"],
    // The history STATS panel ("Top from -> to flows") sits next to the
    // history list; without this it kept showing pre-edit flows.
    ["tag-history-stats"],
    ["classify-queue"],
    ["trends"],
    ["dashboard"],
  ]) {
    void qc.invalidateQueries({ queryKey: key });
  }
}

/* Single source of truth for bucket names and per-bucket colors.
 *
 * IMPORTANT: values must stay full literal class strings — Tailwind's
 * scanner cannot see dynamically-composed class names, and these classes
 * exist nowhere else in the codebase. */

export interface BucketStyle {
  badge: string;
  button: string;
}

export const BUCKET_STYLES: Record<string, BucketStyle> = {
  outfit: {
    badge: "bg-accent-cyan/15 text-accent-cyan",
    button: "bg-accent-cyan/15 text-accent-cyan hover:bg-accent-cyan/30",
  },
  pose: {
    badge: "bg-accent-green/15 text-accent-green",
    button: "bg-accent-green/15 text-accent-green hover:bg-accent-green/30",
  },
  expression: {
    badge: "bg-accent-amber/15 text-accent-amber",
    button: "bg-accent-amber/15 text-accent-amber hover:bg-accent-amber/30",
  },
  background: {
    badge: "bg-brand/15 text-brand",
    button: "bg-brand/15 text-brand hover:bg-brand/30",
  },
  composition: {
    badge: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
    button:
      "bg-purple-500/15 text-purple-700 dark:text-purple-400 hover:bg-purple-500/30",
  },
  accessory: {
    badge: "bg-pink-500/15 text-pink-700 dark:text-pink-400",
    button:
      "bg-pink-500/15 text-pink-700 dark:text-pink-400 hover:bg-pink-500/30",
  },
  extras: {
    badge: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
    button:
      "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-500/30",
  },
  character: {
    badge: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
    button:
      "bg-blue-500/15 text-blue-700 dark:text-blue-400 hover:bg-blue-500/30",
  },
  artist: {
    badge: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
    button:
      "bg-orange-500/15 text-orange-700 dark:text-orange-400 hover:bg-orange-500/30",
  },
  quality_meta: {
    badge: "bg-rose-500/15 text-rose-700 dark:text-rose-400",
    button:
      "bg-rose-500/15 text-rose-700 dark:text-rose-400 hover:bg-rose-500/30",
  },
  scene: {
    badge: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
    button:
      "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-500/30",
  },
  other: {
    badge: "bg-line text-text-muted",
    button: "bg-bg-subtle text-text-muted hover:bg-bg-hover",
  },
};

/** Neutral style for unknown keys (e.g. bucket_source values). */
export const FALLBACK_BUCKET_STYLE: BucketStyle = BUCKET_STYLES.other;

export function bucketBadgeClass(bucket: string): string {
  return (BUCKET_STYLES[bucket] ?? FALLBACK_BUCKET_STYLE).badge;
}

export function bucketButtonClass(bucket: string): string {
  return (BUCKET_STYLES[bucket] ?? FALLBACK_BUCKET_STYLE).button;
}

/** Buckets a tag can be manually assigned to (Tag Review, Trends edit). */
export const ASSIGN_BUCKETS = [
  "outfit",
  "pose",
  "expression",
  "background",
  "composition",
  "accessory",
  "extras",
  "character",
  "other",
] as const;

/** Buckets that produce wildcard export files. */
export const EXPORT_BUCKETS = [
  "outfit",
  "pose",
  "expression",
  "background",
  "composition",
  "accessory",
  "extras",
  "character",
  "scene",
] as const;

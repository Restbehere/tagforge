/* Helpers for turning raw booru tag lists into NovelAI-paste-ready text. */

/** Emoticon tags (^_^, <o>_<o>, 6_9, t_t, (o)_(o) …) keep their underscores.
 * Rule instead of a whitelist: short tags with no multi-letter runs are
 * symbolic, so their underscores are load-bearing; word tags (v_arms,
 * white_background) always contain a 2+ letter run and get spaced. */
function isEmoticonTag(t: string): boolean {
  return t.length <= 9 && t.includes("_") && !/[a-z]{2,}/i.test(t);
}

/** Convert an underscore-form comma-separated tag list to space form
 * (the convention NovelAI's UI uses), preserving emoticon tags. */
export function naiSpacedTags(raw: string): string {
  return raw
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => (isEmoticonTag(t) ? t : t.replace(/_/g, " ")))
    .join(", ");
}

/** Count comma-separated tags in a raw list. */
export function tagCount(raw: string): number {
  return raw.split(",").filter((t) => t.trim()).length;
}

/** Extract a Danbooru post id from a pasted id or URL (modern /posts/123
 * and legacy /post/show/123 forms). URLs without a recognizable id yield ""
 * (no filter) rather than silently matching nothing; plain non-URL strings
 * pass through so local external ids (filenames) stay searchable. */
export function danbooruIdFrom(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) return "";
  if (/^\d+$/.test(trimmed)) return trimmed;
  const m = trimmed.match(/post(?:s\/|\/show\/)(\d+)/);
  if (m) return m[1];
  return /[/:]/.test(trimmed) ? "" : trimmed;
}

/** Web URL for a Danbooru post. Requires booru origin — local files can
 * have purely numeric ids (filename stems) that would link to unrelated
 * posts. */
export function danbooruPostUrl(
  externalId: string,
  origin?: string | null,
): string | null {
  return origin === "booru" && /^\d+$/.test(externalId)
    ? `https://danbooru.donmai.us/posts/${externalId}`
    : null;
}

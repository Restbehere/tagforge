"""Wildcard file exporter.

Reads ``scene_line`` rows filtered by user-supplied criteria and writes them
into one ``.txt`` file per bucket -- one line per image-bucket combo. The
output format matches Kohaku-NAI's existing wildcard files exactly so they're
drop-in replacements.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlmodel import select

from .. import db, settings
from ..models import ExportSet, Image, SceneLine, Source
from .scene_exclude import default_deny_set, filter_scene_line_text
from .tag_ratings import SEVERITY, rating_allows


DEFAULT_BUCKETS: tuple[str, ...] = (
    "outfit",
    "pose",
    "expression",
    "background",
    "composition",
    "accessory",
    "extras",
    "character",
    "scene",
)

# The classic combined-scene mix (matches _combined_scene_tag_names in
# runner.py). When the export recipe equals this, the stored ``scene`` rows
# are used verbatim; any other recipe is composed at export time from the
# per-bucket rows.
DEFAULT_SCENE_RECIPE: tuple[str, ...] = ("outfit", "pose", "expression", "background")

# Buckets allowed in a custom scene recipe (character/artist/quality stay out).
SCENE_RECIPE_BUCKETS: frozenset[str] = frozenset(
    {"outfit", "pose", "expression", "background", "composition", "accessory", "extras"}
)

# Buckets where a single tag per image is meaningful. Default
# ``min_tag_count`` is 2 to suppress nearly-empty multi-tag lines, but a
# solo character (1girl portrait → ``hatsune_miku`` only) is a perfectly
# valid character.txt line, so we relax the floor for these buckets.
_SOLO_OK_BUCKETS: frozenset[str] = frozenset({"character"})


def _effective_min_tag_count(bucket: str, user_min: int) -> int:
    if bucket in _SOLO_OK_BUCKETS:
        return min(user_min, 1)
    return user_min


# Back-compat alias for routes that expose the deny list to the UI.
_DEFAULT_DENY: frozenset[str] = default_deny_set()


def _canonicalize_deny_tags(extra: list[str] | None) -> frozenset[str]:
    """Normalize user-supplied deny tags to canonical underscored lower-case.

    Accepts strings with spaces, commas, leading ``-``/``!``, or surrounding
    whitespace. Empty entries are dropped.
    """
    if not extra:
        return frozenset()
    out: set[str] = set()
    for raw in extra:
        if raw is None:
            continue
        for piece in str(raw).replace(",", "\n").splitlines():
            cleaned = piece.strip().lstrip("-!").strip().lower().replace(" ", "_")
            if cleaned:
                out.add(cleaned)
    return frozenset(out)


def _filter_line_by_deny(
    line: str,
    deny: frozenset[str],
    *,
    use_scene_heuristics: bool,
) -> str | None:
    """Strip eye colors (always) plus optional deny / anatomy heuristics."""
    return filter_scene_line_text(
        line,
        extra_deny=deny,
        use_scene_heuristics=use_scene_heuristics,
    )


def _filter_line_by_rating(line: str, cap: str) -> str | None:
    """Strip individual tags above ``cap`` from a comma-joined scene line.

    Returns ``None`` if every tag was dropped.

    ``line`` arrives in the same display form scene_line stores: ``tag with
    spaces, another tag``. We canonicalise back to ``tag_with_spaces`` for the
    rating lookup so it matches the curated dicts in :mod:`tag_ratings`.
    """
    pieces = [p.strip() for p in line.split(",") if p.strip()]
    if not pieces:
        return None
    keep: list[str] = []
    for piece in pieces:
        canonical = piece.replace(" ", "_")
        if rating_allows(canonical, cap):
            keep.append(piece)
    if not keep:
        return None
    return ", ".join(keep)


def build_export(
    *,
    name: str,
    output_dir: Path,
    buckets: list[str] | None = None,
    source_ids: list[int] | None = None,
    origin: Optional[str] = None,
    ratings: list[str] | None = None,
    nai_models: list[str] | None = None,
    score_min: Optional[int] = None,
    max_rating: Optional[str] = None,
    min_tag_count: int = 2,
    deduplicate: bool = True,
    dedupe_ignore_order: bool = True,
    file_prefix: str = "",
    extra_deny_tags: list[str] | None = None,
    use_default_deny: bool = True,
    scene_buckets: list[str] | None = None,
    cap_tags: list[str] | None = None,
    cap_percent: int = 100,
    mirror_dir: Optional[Path] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> dict[str, Any]:
    """Write one ``.txt`` per bucket containing per-image-grouped wildcards.

    ``origin`` restricts to local vs booru sources via :data:`settings.ORIGIN_LOCAL`
    / :data:`settings.ORIGIN_BOORU`. ``max_rating`` (one of ``g/s/q/e``) enables
    per-tag strip-mode: tags above the cap are removed from every line and
    lines that drop below ``min_tag_count`` afterwards are skipped.

    ``extra_deny_tags`` are stripped per-line on top of :data:`_DEFAULT_DENY`
    (toggleable via ``use_default_deny``). Use this to keep tags that the
    user already supplies via their main prompt — ``1girl``, ``solo``,
    male-coded subjects, etc. — out of the wildcards.
    """
    buckets = list(buckets or DEFAULT_BUCKETS)
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Mirror writes are best-effort: a dead second drive must never fail the
    # primary export (same contract as backup mirrors in routes/admin.py).
    mirror_error: str | None = None
    requested_mirror: str | None = None
    if mirror_dir is not None:
        mirror_dir = Path(mirror_dir).expanduser()
        requested_mirror = str(mirror_dir)
        try:
            mirror_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            mirror_error = f"mirror dir unavailable, skipped: {exc}"
            mirror_dir = None

    def _progress(frac: float, msg: str) -> None:
        if on_progress is not None:
            on_progress(frac, msg)

    def _atomic_write(path: Path, text: str) -> None:
        """Temp file + os.replace so a mid-export crash never leaves a
        truncated wildcard file where the generator could sample it."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _mirror_write(filename: str, text: str) -> None:
        nonlocal mirror_dir, mirror_error
        if mirror_dir is None:
            return
        try:
            _atomic_write(mirror_dir / filename, text)
        except OSError as exc:
            mirror_error = (
                f"mirror write failed ({filename}), remaining mirror "
                f"writes skipped: {exc}"
            )
            mirror_dir = None

    ratings = [r.strip().lower() for r in (ratings or []) if r and str(r).strip()]

    origin_kinds = settings.origin_kinds(origin)
    cap = max_rating.lower() if max_rating else None
    if cap and cap not in SEVERITY:
        cap = None

    # Scene recipe: which per-bucket lines compose scene.txt. Invalid entries
    # are dropped; empty/unchanged recipes fall back to the stored fast path.
    recipe = [
        b for b in (scene_buckets or DEFAULT_SCENE_RECIPE) if b in SCENE_RECIPE_BUCKETS
    ] or list(DEFAULT_SCENE_RECIPE)
    custom_recipe = set(recipe) != set(DEFAULT_SCENE_RECIPE)

    # Tag share cap: display-form tokens whose lines get down-sampled so
    # they stay under cap_percent of each bucket file (e.g. white background).
    cap_percent = max(1, min(100, int(cap_percent)))
    cap_tokens: frozenset[str] = frozenset(
        t.replace("_", " ")
        for t in _canonicalize_deny_tags(cap_tags)  # reuse the tag normalizer
    )
    share_cap_active = bool(cap_tokens) and cap_percent < 100

    deny_set: frozenset[str] = (
        default_deny_set() if use_default_deny else frozenset()
    ) | _canonicalize_deny_tags(extra_deny_tags)

    counts: dict[str, int] = {b: 0 for b in buckets}
    files: dict[str, str] = {}
    total_lines = 0

    with db.session_scope() as s:
        # Persist the export-set row up front so reruns are auditable.
        filters_record = {
            "source_ids": source_ids or [],
            "origin": origin,
            "ratings": ratings or [],
            "nai_models": nai_models or [],
            "score_min": score_min,
            "max_rating": cap,
            "min_tag_count": min_tag_count,
            "deduplicate": deduplicate,
            "dedupe_ignore_order": dedupe_ignore_order,
            "file_prefix": file_prefix,
            "buckets": buckets,
            "scene_buckets": recipe,
            "cap_tags": sorted(cap_tokens),
            "cap_percent": cap_percent if share_cap_active else 100,
            "use_default_deny": use_default_deny,
            "extra_deny_tags": sorted(_canonicalize_deny_tags(extra_deny_tags)),
            "deny_tag_count": len(deny_set),
        }
        es = ExportSet(
            name=name,
            filters_json=json.dumps(filters_record),
            output_dir=str(output_dir),
        )
        s.add(es)
        s.flush()
        # Commit NOW to release SQLite's write lock: the bucket loop below
        # fires on_progress -> jobs.update_job, which writes the job row on
        # a second connection and would deadlock against our open INSERT
        # transaction (WAL allows one writer). The loop itself is read-only.
        s.commit()

        cap_dropped: dict[str, int] = {}
        for bucket in buckets:
            effective_min = _effective_min_tag_count(bucket, min_tag_count)
            # ``character.txt`` is always sourced from booru ingests only — local
            # metadata prompts do not carry Danbooru category-4 character tags.
            bucket_origin = (
                settings.origin_kinds("booru")
                if bucket == "character"
                else origin_kinds
            )

            def _apply_image_filters(query):
                if bucket_origin is not None:
                    query = query.join(Source, Source.id == Image.source_id).where(
                        Source.kind.in_(bucket_origin)  # type: ignore[arg-type]
                    )
                if source_ids:
                    query = query.where(Image.source_id.in_(source_ids))  # type: ignore[arg-type]
                if ratings:
                    query = query.where(Image.rating.in_(ratings))  # type: ignore[arg-type]
                if nai_models:
                    query = query.where(Image.nai_model.in_(nai_models))  # type: ignore[arg-type]
                if score_min is not None:
                    query = query.where(Image.score >= score_min)
                return query

            if bucket == "scene" and custom_recipe:
                # Compose scene lines at export time from the per-bucket rows
                # so the recipe never requires a scene_line rebuild.
                q = _apply_image_filters(
                    select(SceneLine.image_id, SceneLine.bucket, SceneLine.tag_text)
                    .join(Image, Image.id == SceneLine.image_id)
                    .where(SceneLine.bucket.in_(recipe))  # type: ignore[arg-type]
                )
                per_image: dict[int, dict[str, str]] = {}
                for row in s.exec(q).all():
                    image_id, sl_bucket, text = row[0], row[1], row[2]
                    if text:
                        per_image.setdefault(image_id, {})[sl_bucket] = text
                rows = [
                    ", ".join(parts[b] for b in recipe if parts.get(b))
                    for parts in per_image.values()
                ]
                # The SQL tag_count floor doesn't apply to composed lines.
                rows = [r for r in rows if r.count(",") + 1 >= effective_min]
            else:
                q = _apply_image_filters(
                    select(SceneLine.tag_text)
                    .join(Image, Image.id == SceneLine.image_id)
                    .where(SceneLine.bucket == bucket)
                    .where(SceneLine.tag_count >= effective_min)
                )
                rows = list(s.exec(q).all())

            lines: list[str] = []
            for r in rows:
                if not r:
                    continue
                line: str | None = r
                if cap:
                    line = _filter_line_by_rating(line, cap)
                    if line is None:
                        continue
                # Always strip eye-color tags; optionally apply deny list / anatomy.
                line = _filter_line_by_deny(
                    line,
                    deny_set,
                    use_scene_heuristics=use_default_deny,
                )
                if line is None:
                    continue
                # Re-check min-tag-count after any stripping took place.
                if (cap or deny_set or use_default_deny) and line.count(",") + 1 < effective_min:
                    continue
                lines.append(line)

            if deduplicate:
                seen: set[str] | set[tuple[str, ...]] = set()
                deduped: list[str] = []
                for ln in lines:
                    if dedupe_ignore_order:
                        key: Any = tuple(
                            sorted(
                                t.strip() for t in ln.lower().split(",") if t.strip()
                            )
                        )
                    else:
                        key = ln.strip().lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(ln)
                lines = deduped

            if share_cap_active and lines:
                # Down-sample lines containing a capped tag so their share of
                # the file (= their pick probability in the wildcard) stays
                # at or below cap_percent.
                def _has_cap_tag(ln: str) -> bool:
                    tokens = {t.strip().lower() for t in ln.split(",")}
                    return not cap_tokens.isdisjoint(tokens)

                matching = [i for i, ln in enumerate(lines) if _has_cap_tag(ln)]
                matching_set = set(matching)
                others = len(lines) - len(matching)
                if matching and others > 0:
                    # max m with m / (m + others) <= cap%:
                    allowed = int(cap_percent / 100 * others / (1 - cap_percent / 100))
                    if len(matching) > allowed:
                        keep = set(random.sample(matching, allowed)) if allowed else set()
                        cap_dropped[bucket] = len(matching) - allowed
                        lines = [
                            ln
                            for i, ln in enumerate(lines)
                            if i not in matching_set or i in keep
                        ]

            filename = f"{file_prefix}{bucket}.txt" if file_prefix else f"{bucket}.txt"
            content = "\n".join(lines) + ("\n" if lines else "")
            file_path = output_dir / filename
            _atomic_write(file_path, content)
            _mirror_write(filename, content)
            counts[bucket] = len(lines)
            files[bucket] = str(file_path)
            total_lines += len(lines)
            _progress(
                (buckets.index(bucket) + 1) / len(buckets),
                f"{filename}: {len(lines):,} lines",
            )

        es.file_count = len(files)
        es.line_count = total_lines
        s.add(es)

    warnings: list[str] = []
    if mirror_error:
        warnings.append(mirror_error)
    for bucket_name, dropped in cap_dropped.items():
        warnings.append(
            f"{bucket_name}.txt: dropped {dropped} lines containing "
            f"{', '.join(sorted(cap_tokens))} to keep their share <= {cap_percent}%."
        )
    if "character" in buckets and counts.get("character", 0) == 0:
        warnings.append(
            "character.txt is empty: this bucket only comes from Danbooru/AIBooru "
            "ingests (category-4 character tags on posts). Local metadata.txt does "
            "not populate it — export always uses booru sources for this file even "
            "if Origin is All. Check Scene rating filter uses g,s,q without spaces "
            "(e.g. g,s not g, s). If you ran Rebuild scene_line on an older build, "
            "click Tags → Fix booru character lines, then export again."
        )

    manifest = {
        "name": name,
        "created_at": datetime.utcnow().isoformat(),
        "output_dir": str(output_dir),
        # Report the mirror only if it stayed healthy the whole way through.
        "mirror_dir": requested_mirror if mirror_dir is not None else None,
        "mirror_error": mirror_error,
        "files": files,
        "line_counts": counts,
        "filters": filters_record,
        "warnings": warnings,
    }
    manifest_text = json.dumps(manifest, indent=2)
    _atomic_write(output_dir / "manifest.json", manifest_text)
    _mirror_write("manifest.json", manifest_text)
    return manifest

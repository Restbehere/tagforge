"""Background runners for ingest + booru-fetch jobs.

These functions are designed to be passed to :class:`fastapi.BackgroundTasks`.
They run synchronously inside the FastAPI worker and stream progress back to
the UI via the ``jobs`` module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from .. import db, jobs, settings
from ..models import Image, ImageTag, SceneLine, Source, Tag, TagBucketOverride
from .danbooru_client import DanbooruClient, Post
from .image_extract import count_images, iter_image_records
from .metadata_parser import MetadataRecord, count_records, iter_metadata_records
from .prompt_cleaner import clean_prompt
from .scene_exclude import filter_tag_names_for_scene, is_scene_excluded
from .tag_categorizer import BUCKETS, SCENE_BUCKETS, classify_tag
from .tag_ratings import infer_image_rating


def _parse_booru_date(s: str) -> Optional[datetime]:
    """Parse a Danbooru ISO-8601 date string to a naive UTC datetime."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # Strip tzinfo so it matches the rest of our naive-UTC datetimes in SQLite.
        if dt.tzinfo is not None:
            from datetime import timezone as _tz
            dt = dt.astimezone(_tz.utc).replace(tzinfo=None)
        return dt
    except (ValueError, AttributeError):
        return None


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source dedup helpers
#
# Both ingest paths (local metadata + booru fetch) compute a stable key that
# identifies "the same logical fetch", and ``_get_or_create_source`` reuses an
# existing row instead of creating a duplicate. Per-image dedup inside the
# source then naturally skips already-ingested rows on a re-run.
# ---------------------------------------------------------------------------


# Booru request fields that DO NOT affect what's actually fetched and so
# should be excluded from the dedup key. ``pages`` / ``limit`` decide how
# much, not what — leaving them out lets you incrementally extend a previous
# fetch by re-running it with more pages.
_BOORU_NON_IDENTIFYING_PARAMS: frozenset[str] = frozenset(
    {"pages", "limit", "login", "api_key", "label"}
)


def local_dedup_key(path: Path) -> str:
    """Stable key for a local metadata_file source — based on absolute path."""
    canon = str(path.resolve()).lower()
    return "local:" + hashlib.sha1(canon.encode("utf-8")).hexdigest()


def booru_dedup_key(site: str, params: dict[str, Any]) -> str:
    """Stable key for a booru fetch — site + identifying params, hashed."""
    canon = {
        k: v
        for k, v in params.items()
        if k not in _BOORU_NON_IDENTIFYING_PARAMS and v not in (None, "", [], {})
    }
    payload = json.dumps(canon, sort_keys=True, default=str)
    return f"{site}:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _get_or_create_source(
    s: Session,
    *,
    kind: str,
    label: str,
    dedup_key: str,
    note: str | None = None,
    filters_json: str | None = None,
) -> tuple[int, bool]:
    """Look up a source by dedup_key; create it if missing.

    Returns ``(source_id, created)``. When ``created`` is False the caller
    is re-running an existing ingest and should expect per-image dedup to
    skip most rows.
    """
    existing = s.exec(select(Source).where(Source.dedup_key == dedup_key)).first()
    if existing is not None:
        # Refresh metadata (label/filters/note can legitimately change between
        # runs — e.g. user renames the label or toggles a drop flag) but keep
        # the original row and its image_count baseline.
        existing.label = label
        existing.note = note
        existing.filters_json = filters_json
        existing.fetched_at = datetime.utcnow()
        s.add(existing)
        s.flush()
        return existing.id, False  # type: ignore[return-value]

    src = Source(
        kind=kind,
        label=label,
        note=note,
        filters_json=filters_json,
        dedup_key=dedup_key,
    )
    s.add(src)
    s.flush()
    return src.id, True  # type: ignore[return-value]


def _refresh_source_image_count(source_id: int) -> int:
    """Recompute and persist Source.image_count from actual image rows."""
    with db.session_scope() as s:
        n = s.exec(
            select(func.count(Image.id)).where(Image.source_id == source_id)
        ).one()
        src = s.get(Source, source_id)
        if src is not None:
            src.image_count = int(n)
            s.add(src)
        return int(n)


# ---------------------------------------------------------------------------
# Tag persistence helpers (shared between metadata + booru ingest)
# ---------------------------------------------------------------------------


def _resolve_tag(s: Session, name: str, tag_cache: dict[str, int]) -> int:
    """Get-or-create a Tag row, returning its id. Updates classification once."""
    if name in tag_cache:
        return tag_cache[name]

    existing = s.exec(select(Tag).where(Tag.name == name)).first()
    if existing is not None:
        tag_cache[name] = existing.id  # type: ignore[assignment]
        return existing.id  # type: ignore[return-value]

    assign = classify_tag(name)
    tag = Tag(
        name=name,
        display=name.replace("_", " "),
        bucket=assign.bucket,
        bucket_source=assign.bucket_source,
        confidence=assign.confidence,
        category=assign.category,
    )

    # Apply any prior manual override
    override = s.exec(
        select(TagBucketOverride).where(TagBucketOverride.tag_name == name)
    ).first()
    if override:
        tag.bucket = override.bucket
        tag.bucket_source = "manual"
        tag.confidence = 1.0
        tag.locked = True

    s.add(tag)
    s.flush()
    tag_cache[name] = tag.id  # type: ignore[assignment]
    return tag.id  # type: ignore[return-value]


def _load_tag_bucket_map(s: Session) -> dict[str, str]:
    """One query for the whole tag->bucket map. Rebuild jobs pass this into
    _bucket_groups so a 250k-image rebuild doesn't issue millions of
    per-tag point queries."""
    out: dict[str, str] = {}
    for row in s.exec(select(Tag.name, Tag.bucket)).all():
        out[row[0]] = row[1]
    return out


def _bucket_groups(
    s: Session,
    canonical_tags: list[str],
    drop_character_tags: bool,
    tag_map: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Return ``{bucket: [tag_names...]}`` preserving the prompt order."""
    groups: dict[str, list[str]] = {b: [] for b in BUCKETS}
    for name in canonical_tags:
        if is_scene_excluded(name):
            continue
        if tag_map is not None:
            bucket = tag_map.get(name)
        else:
            tag = s.exec(select(Tag).where(Tag.name == name)).first()
            bucket = tag.bucket if tag is not None else None
        if bucket is None:
            continue
        if bucket == "artist":
            continue
        if drop_character_tags and bucket == "character":
            continue
        if bucket == "quality_meta":
            continue
        groups.setdefault(bucket, []).append(name)
    return groups


def _combined_scene_tag_names(
    s: Session,
    groups: dict[str, list[str]],
    tag_map: dict[str, str] | None = None,
) -> list[str]:
    """Tags for the ``scene`` bucket line — excludes character + scene_exclude."""
    names: list[str] = []
    for bucket in ("outfit", "pose", "expression", "background"):
        for name in groups.get(bucket, []):
            if is_scene_excluded(name):
                continue
            if tag_map is not None:
                if tag_map.get(name) == "character":
                    continue
            else:
                tag = s.exec(select(Tag).where(Tag.name == name)).first()
                if tag is not None and tag.bucket == "character":
                    continue
            names.append(name)
    return filter_tag_names_for_scene(names)


def _persist_scene_lines(
    s: Session,
    image_id: int,
    groups: dict[str, list[str]],
    tag_map: dict[str, str] | None = None,
) -> None:
    # Delete previous lines (idempotent re-ingest). Flush so the unique
    # constraint on (image_id, bucket) doesn't trip on the inserts below.
    for old in s.exec(select(SceneLine).where(SceneLine.image_id == image_id)).all():
        s.delete(old)
    s.flush()

    for bucket in SCENE_BUCKETS:
        tags = filter_tag_names_for_scene(groups.get(bucket, []))
        if not tags:
            continue
        s.add(
            SceneLine(
                image_id=image_id,
                bucket=bucket,
                tag_text=", ".join(t.replace("_", " ") for t in tags),
                tag_count=len(tags),
            )
        )

    # Combined ``scene`` line: outfit/pose/expression/background only — never
    # character names (user supplies those in the main prompt).
    scene_tags = _combined_scene_tag_names(s, groups, tag_map)
    if scene_tags:
        s.add(
            SceneLine(
                image_id=image_id,
                bucket="scene",
                tag_text=", ".join(t.replace("_", " ") for t in scene_tags),
                tag_count=len(scene_tags),
            )
        )


# ---------------------------------------------------------------------------
# Metadata-file ingest
# ---------------------------------------------------------------------------


def _classify_after_ingest(
    job_id: int,
    *,
    created_after: datetime | None = None,
    drop_character_tags: bool = False,
) -> str:
    """Chain Stage-3 GPT classification + delta scene rebuild after an ingest.

    ``created_after`` scopes the LLM pass to tags this ingest actually
    created (matching the checkbox label) instead of the global residual
    backlog; ``drop_character_tags`` must mirror the ingest's own flag so
    the rebuild doesn't resurrect character tags the ingest excluded.
    Failures (missing key, network) degrade to a message suffix instead of
    failing the whole ingest job. Returns a suffix for the done-message.
    """
    try:
        from .stage3_llm import reclassify_residuals  # optional [llm] extra

        jobs.update_job(
            job_id,
            progress=0.75,
            message="classifying new tags (gpt-4o-mini)…",
        )
        result = reclassify_residuals(
            provider="openai",
            model="gpt-4o-mini",
            concurrency=6,
            job_id=job_id,
            created_after=created_after,
        )
        suffix = f" · classified: {result.relabelled} new tags relabelled"
        if result.failed_batches:
            suffix += f" ({result.failed_batches} LLM batches failed)"
    except Exception as exc:
        logger.warning("post-ingest classification skipped: %s", exc)
        return f" · classification skipped: {exc}"

    # Rebuild failure must not masquerade as "classification skipped" —
    # the relabels are already committed at this point.
    try:
        jobs.update_job(
            job_id,
            progress=0.9,
            message="rebuilding scene lines for relabelled tags…",
        )
        _rebuild_scene_lines(
            changed_tag_ids=result.changed_tag_ids,
            drop_character_tags=drop_character_tags,
            job_id=job_id,
            progress_range=(0.9, 0.99),
        )
    except Exception as exc:
        logger.warning("post-classify scene rebuild failed: %s", exc)
        suffix += (
            f" — scene rebuild failed ({exc}); run Rebuild scene lines "
            f"from the Tags page"
        )
    return suffix


def run_metadata_ingest(
    *,
    job_id: int,
    path: Path,
    label: str,
    drop_artist_tags: bool = True,
    drop_quality_tags: bool = True,
    drop_character_tags: bool = False,
    batch_size: int = 250,
    classify_after: bool = False,
) -> None:
    """Parse ``metadata.txt`` and insert images / tags / scene_lines."""
    jobs.update_job(job_id, status="running", progress=0.0, message="counting records")
    try:
        total = count_records(path)
    except Exception as exc:
        jobs.update_job(job_id, status="error", error=f"count failed: {exc}", finished=True)
        return

    jobs.update_job(
        job_id,
        status="running",
        progress=0.0,
        message=f"parsing {total:,} records",
        detail={"total": total, "processed": 0, "path": str(path)},
    )

    source_id: int | None = None
    source_reused = False
    inserted = 0
    skipped = 0
    started = time.time()
    # Naive UTC to match models._utcnow (Tag.created_at default) — scopes
    # the optional post-ingest classify to tags this run creates.
    ingest_started_at = datetime.utcnow()
    tag_cache: dict[str, int] = {}

    try:
        dedup_key = local_dedup_key(path)
        filters_json = json.dumps(
            {
                "drop_artist_tags": drop_artist_tags,
                "drop_quality_tags": drop_quality_tags,
                "drop_character_tags": drop_character_tags,
            }
        )
        with db.session_scope() as s:
            source_id, created = _get_or_create_source(
                s,
                kind="metadata_file",
                label=label,
                dedup_key=dedup_key,
                note=str(path),
                filters_json=filters_json,
            )
            source_reused = not created
        if source_reused:
            logger.info(
                "reusing existing source #%s for path=%s (re-ingest)",
                source_id,
                path,
            )

        engine = db.get_engine()
        batch: list[tuple[MetadataRecord, list[str]]] = []

        for idx, rec in enumerate(iter_metadata_records(path), start=1):
            cleaned = clean_prompt(
                rec.prompt,
                drop_artist_tags=drop_artist_tags,
                drop_quality_tags=drop_quality_tags,
            )
            if not cleaned.tags:
                # Counted as skipped — must not also be inserted, or the row
                # lands tagless AND is double-counted in the summary math.
                skipped += 1
                continue
            batch.append((rec, cleaned.tags))

            if len(batch) >= batch_size:
                inserted += _flush_metadata_batch(
                    batch, source_id, drop_character_tags, tag_cache
                )
                batch.clear()
                _emit_progress(job_id, idx, total, inserted, skipped, started)

        if batch:
            inserted += _flush_metadata_batch(
                batch, source_id, drop_character_tags, tag_cache
            )
            _emit_progress(job_id, total, total, inserted, skipped, started)

        # Recompute the true row count (re-ingest updates existing rows so
        # ``inserted`` only tracks newly-created ones; we still want the
        # Source.image_count to reflect the full corpus).
        total_in_source = _refresh_source_image_count(source_id) if source_id else 0
        updated = max(0, (total - skipped) - inserted)

        classify_suffix = (
            _classify_after_ingest(
                job_id,
                created_after=ingest_started_at,
                drop_character_tags=drop_character_tags,
            )
            if classify_after
            else ""
        )

        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=(
                f"ingested {inserted:,} new + {updated:,} updated"
                f" (skipped {skipped:,}, source total: {total_in_source:,})"
                + (" — reused existing source" if source_reused else "")
                + classify_suffix
            ),
            detail={
                "total": total,
                "processed": total,
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped,
                "source_id": source_id,
                "source_reused": source_reused,
                "source_total_images": total_in_source,
                "elapsed_sec": round(time.time() - started, 2),
            },
            finished=True,
        )
    except Exception as exc:
        logger.exception("metadata ingest failed")
        jobs.update_job(job_id, status="error", error=str(exc), finished=True)


def run_image_folder_ingest(
    job_id: int,
    folder: Path,
    label: str,
    recursive: bool = True,
    drop_artist_tags: bool = True,
    drop_quality_tags: bool = True,
    drop_character_tags: bool = False,
    classify_after: bool = False,
    batch_size: int = 500,
) -> None:
    """Read generation metadata straight out of a folder of images.

    Same destination as :func:`run_metadata_ingest` — it just gets its
    records from the image files themselves instead of a metadata.txt
    dump, so no separate extractor tool is needed.
    """
    jobs.update_job(job_id, status="running", progress=0.0, message="scanning folder")
    try:
        total = count_images(folder, recursive=recursive)
    except Exception as exc:
        jobs.update_job(job_id, status="error", error=f"scan failed: {exc}", finished=True)
        return
    if not total:
        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message="no images found in that folder",
            finished=True,
        )
        return

    jobs.update_job(
        job_id,
        status="running",
        message=f"reading {total:,} images",
        detail={"total": total, "processed": 0, "path": str(folder)},
    )

    source_id: int | None = None
    source_reused = False
    inserted = skipped = no_metadata = 0
    started = time.time()
    ingest_started_at = datetime.utcnow()
    tag_cache: dict[str, int] = {}

    try:
        filters_json = json.dumps(
            {
                "drop_artist_tags": drop_artist_tags,
                "drop_quality_tags": drop_quality_tags,
                "drop_character_tags": drop_character_tags,
                "recursive": recursive,
            }
        )
        with db.session_scope() as s:
            source_id, created = _get_or_create_source(
                s,
                kind="metadata_file",
                label=label,
                dedup_key=local_dedup_key(folder),
                note=str(folder),
                filters_json=filters_json,
            )
            source_reused = not created

        batch: list[tuple[MetadataRecord, list[str]]] = []
        for idx, (path, rec) in enumerate(
            iter_image_records(folder, recursive=recursive), start=1
        ):
            if rec is None:
                # A photo, a screenshot, or a stripped export — expected, not
                # an error. Counted separately from tagless prompts.
                no_metadata += 1
            else:
                cleaned = clean_prompt(
                    rec.prompt,
                    drop_artist_tags=drop_artist_tags,
                    drop_quality_tags=drop_quality_tags,
                )
                if cleaned.tags:
                    batch.append((rec, cleaned.tags))
                else:
                    skipped += 1

            if len(batch) >= batch_size:
                inserted += _flush_metadata_batch(
                    batch, source_id, drop_character_tags, tag_cache
                )
                batch.clear()
                _emit_progress(job_id, idx, total, inserted, skipped, started)

        if batch:
            inserted += _flush_metadata_batch(
                batch, source_id, drop_character_tags, tag_cache
            )
        _emit_progress(job_id, total, total, inserted, skipped, started)

        total_in_source = _refresh_source_image_count(source_id) if source_id else 0
        read_ok = total - no_metadata
        updated = max(0, (read_ok - skipped) - inserted)

        classify_suffix = (
            _classify_after_ingest(
                job_id,
                created_after=ingest_started_at,
                drop_character_tags=drop_character_tags,
            )
            if classify_after
            else ""
        )

        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=(
                f"read {read_ok:,}/{total:,} images — {inserted:,} new + "
                f"{updated:,} updated (no metadata: {no_metadata:,}, "
                f"no usable tags: {skipped:,}, source total: {total_in_source:,})"
                + (" — reused existing source" if source_reused else "")
                + classify_suffix
            ),
            detail={
                "total": total,
                "processed": total,
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped,
                "no_metadata": no_metadata,
                "source_id": source_id,
                "source_reused": source_reused,
                "source_total_images": total_in_source,
                "elapsed_sec": round(time.time() - started, 2),
            },
            finished=True,
        )
    except Exception as exc:
        logger.exception("image folder ingest failed")
        jobs.update_job(job_id, status="error", error=str(exc), finished=True)


def _flush_metadata_batch(
    batch: list[tuple[MetadataRecord, list[str]]],
    source_id: int | None,
    drop_character_tags: bool,
    tag_cache: dict[str, int],
) -> int:
    inserted = 0
    with db.session_scope() as s:
        for rec, canonical in batch:
            if source_id is None:
                continue
            # upsert image by (source_id, external_id)
            external = rec.external_id()
            existing = s.exec(
                select(Image)
                .where(Image.source_id == source_id)
                .where(Image.external_id == external)
            ).first()
            inferred_rating, evidence = infer_image_rating(canonical)

            if existing is not None:
                img = existing
                # idempotent: clear old image_tag rows
                for it in s.exec(select(ImageTag).where(ImageTag.image_id == img.id)).all():
                    s.delete(it)
                if img.rating_source != "provided":
                    img.rating = inferred_rating
                    img.rating_source = "inferred"
                    img.rating_evidence = ",".join(evidence[:16]) or None
                    s.add(img)
            else:
                img = Image(
                    source_id=source_id,
                    external_id=external,
                    rating=inferred_rating,
                    rating_source="inferred",
                    rating_evidence=",".join(evidence[:16]) or None,
                    nai_model=rec.nai_model,
                    software=rec.software,
                    width=rec.width,
                    height=rec.height,
                    raw_prompt=rec.prompt or "",
                    raw_negative=rec.negative,
                )
                s.add(img)
                s.flush()
                inserted += 1

            for order_idx, name in enumerate(canonical):
                tag_id = _resolve_tag(s, name, tag_cache)
                s.add(ImageTag(image_id=img.id, tag_id=tag_id, order_idx=order_idx))

            groups = _bucket_groups(s, canonical, drop_character_tags)
            _persist_scene_lines(s, img.id, groups)

    return inserted


def _emit_progress(
    job_id: int,
    processed: int,
    total: int,
    inserted: int,
    skipped: int,
    started: float,
) -> None:
    pct = processed / total if total else 0.0
    elapsed = time.time() - started
    rate = processed / elapsed if elapsed > 0 else 0.0
    eta = ((total - processed) / rate) if rate > 0 else 0.0
    jobs.update_job(
        job_id,
        progress=pct,
        message=(
            f"{processed:,}/{total:,} records "
            f"({inserted:,} new, {skipped:,} empty) "
            f"~{rate:.0f}/s, ETA {int(eta)}s"
        ),
        detail={
            "total": total,
            "processed": processed,
            "inserted": inserted,
            "skipped": skipped,
            "elapsed_sec": round(elapsed, 1),
        },
    )


# ---------------------------------------------------------------------------
# Booru fetch
# ---------------------------------------------------------------------------


def run_booru_fetch(*, job_id: int, params: dict[str, Any]) -> None:
    """Run a booru fetch in a fresh asyncio loop (FastAPI BackgroundTasks runs sync)."""
    try:
        asyncio.run(_run_booru_fetch_async(job_id, params))
    except Exception as exc:
        logger.exception("booru fetch failed")
        jobs.update_job(job_id, status="error", error=str(exc), finished=True)


async def _run_booru_fetch_async(job_id: int, params: dict[str, Any]) -> None:
    site = params.get("site", "danbooru")
    mode = params.get("mode", "popular")
    rating = params.get("rating")
    pages = int(params.get("pages", 1))
    limit = int(params.get("limit", 200))
    # Naive UTC to match models._utcnow — scopes post-fetch classification
    # to tags this fetch creates.
    fetch_started_at = datetime.utcnow()

    jobs.update_job(
        job_id,
        status="running",
        progress=0.05,
        message=f"connecting to {site} ({mode})",
    )

    # Optional multi-day backfill for popular/rank: when both bounds are
    # set, fetch each day in the range so missed evenings don't leave gaps
    # in the trends data.
    def _day_range() -> list[str] | None:
        d_min, d_max = params.get("date_min"), params.get("date_max")
        if mode == "popular":
            # explore/popular takes a single date, so a lone bound can't be
            # expressed as a query filter like the other modes — complete
            # the range instead of silently ignoring it.
            if d_min and not d_max:
                d_max = datetime.utcnow().date().isoformat()
            elif d_max and not d_min:
                d_min = d_max
        if mode not in {"popular", "rank"} or not d_min or not d_max:
            return None
        try:
            lo = date.fromisoformat(str(d_min))
            hi = date.fromisoformat(str(d_max))
        except ValueError:
            return None
        if hi < lo:
            lo, hi = hi, lo
        # Hard sanity cap: a year of daily fetches max.
        days = [(lo + timedelta(days=i)).isoformat() for i in range((hi - lo).days + 1)]
        return days[:366]

    day_span = _day_range()

    async with DanbooruClient(
        site=site,
        login=params.get("login") or None,
        api_key=params.get("api_key") or None,
    ) as client:
        if mode == "popular":
            if day_span:
                scale = params.get("scale", "day")

                def _window_key(day_str: str) -> str:
                    # week/month popular windows contain many days — fetch
                    # each distinct window once instead of once per day.
                    d = date.fromisoformat(day_str)
                    if scale == "week":
                        iso = d.isocalendar()
                        return f"{iso[0]}-W{iso[1]:02d}"
                    if scale == "month":
                        return f"{d.year}-{d.month:02d}"
                    return day_str

                posts = []
                seen_ids: set[int] = set()
                seen_windows: set[str] = set()
                for i, day in enumerate(day_span):
                    window = _window_key(day)
                    if window in seen_windows:
                        continue
                    seen_windows.add(window)
                    day_posts = await client.popular(
                        date=day,
                        scale=scale,
                        limit=limit,
                    )
                    for p in day_posts:
                        if p.id not in seen_ids:
                            seen_ids.add(p.id)
                            posts.append(p)
                    jobs.update_job(
                        job_id,
                        progress=0.05 + 0.5 * ((i + 1) / len(day_span)),
                        message=(
                            f"popular {window} — {len(posts):,} unique posts "
                            f"({i + 1}/{len(day_span)} days)"
                        ),
                    )
            else:
                posts = await client.popular(
                    date=params.get("date") or datetime.utcnow().date().isoformat(),
                    scale=params.get("scale", "day"),
                    limit=limit,
                )
        elif mode == "rank":
            if day_span:
                posts = []
                seen_ids = set()
                for i, day in enumerate(day_span):
                    day_posts = await client.top_by_rank(
                        date_min=day,
                        date_max=day,
                        rating=rating,
                        score_min=params.get("score_min"),
                        limit=limit,
                        pages=pages,
                    )
                    for p in day_posts:
                        if p.id not in seen_ids:
                            seen_ids.add(p.id)
                            posts.append(p)
                    jobs.update_job(
                        job_id,
                        progress=0.05 + 0.5 * ((i + 1) / len(day_span)),
                        message=(
                            f"rank {day} — {len(posts):,} unique posts "
                            f"({i + 1}/{len(day_span)} days)"
                        ),
                    )
            else:
                posts = await client.top_by_rank(
                    date_min=params.get("date_min"),
                    date_max=params.get("date_max"),
                    rating=rating,
                    score_min=params.get("score_min"),
                    limit=limit,
                    pages=pages,
                )
        elif mode == "score":
            posts = await client.top_by_score(
                date_min=params.get("date_min"),
                date_max=params.get("date_max"),
                rating=rating,
                score_min=params.get("score_min"),
                limit=limit,
                pages=pages,
            )
        elif mode == "tag_search":
            posts = await client.tag_search(
                tags=params.get("tags", []),
                rating=rating,
                order=None,
                date_min=params.get("date_min"),
                date_max=params.get("date_max"),
                score_min=params.get("score_min"),
                limit=limit,
                pages=pages,
            )
        elif mode == "trending":
            delta = await client.trending_delta(
                recent_days=int(params.get("recent_days", 7)),
                baseline_days=int(params.get("baseline_days", 30)),
                rating=rating,
                pages=pages,
            )
            jobs.update_job(
                job_id,
                progress=1.0,
                status="done",
                message=(
                    f"trending: {delta['recent_posts']} recent / "
                    f"{delta['baseline_posts']} baseline posts"
                ),
                detail=delta,
                finished=True,
            )
            return
        else:
            jobs.update_job(
                job_id,
                status="error",
                error=f"unknown mode: {mode}",
                finished=True,
            )
            return

    jobs.update_job(
        job_id,
        progress=0.6,
        message=f"fetched {len(posts):,} posts, persisting…",
    )

    label = params.get("label") or f"{site}:{mode}"
    tag_cache: dict[str, int] = {}
    inserted, source_id, source_reused = _persist_booru_posts(
        site, label, params, posts, tag_cache
    )
    total_in_source = (
        _refresh_source_image_count(source_id) if source_id else inserted
    )
    skipped_dupes = len(posts) - inserted

    classify_suffix = (
        _classify_after_ingest(job_id, created_after=fetch_started_at)
        if params.get("classify_after")
        else ""
    )

    jobs.update_job(
        job_id,
        progress=1.0,
        status="done",
        message=(
            f"fetched {len(posts):,} posts · "
            f"{inserted:,} new, {skipped_dupes:,} already present "
            f"(source total: {total_in_source:,})"
            + (" — reused existing source" if source_reused else "")
            + classify_suffix
        ),
        detail={
            "site": site,
            "mode": mode,
            "fetched": len(posts),
            "inserted": inserted,
            "duplicates_skipped": skipped_dupes,
            "source_id": source_id,
            "source_reused": source_reused,
            "source_total_images": total_in_source,
        },
        finished=True,
    )


def _persist_booru_posts(
    site: str,
    label: str,
    params: dict[str, Any],
    posts: list[Post],
    tag_cache: dict[str, int],
) -> tuple[int, int, bool]:
    """Persist a batch of booru posts. Returns (inserted, source_id, reused)."""
    inserted = 0
    dedup_key = booru_dedup_key(site, params)
    # Strip secrets before storing the filter payload.
    filters_to_store = {
        k: v
        for k, v in params.items()
        if k not in {"login", "api_key"}
    }
    with db.session_scope() as s:
        source_id, created = _get_or_create_source(
            s,
            kind=site,
            label=label,
            dedup_key=dedup_key,
            note=f"mode={params.get('mode')}",
            filters_json=json.dumps(filters_to_store),
        )
        source_reused = not created
        if source_reused:
            logger.info(
                "reusing existing booru source #%s key=%s",
                source_id,
                dedup_key,
            )

        for post in posts:
            # Global dedup across every source from the same site so that
            # overlapping fetches (different score_min, date ranges, etc.)
            # never store the same post twice.
            existing = s.exec(
                select(Image)
                .join(Source, Source.id == Image.source_id)
                .where(Source.kind == site)
                .where(Image.external_id == str(post.id))
            ).first()
            if existing is not None:
                # Backfill fields that may be missing or stale without
                # touching tags, scene lines, or any other data.
                changed = False
                if existing.external_created_at is None and post.created_at:
                    existing.external_created_at = _parse_booru_date(post.created_at)
                    changed = True
                if post.score is not None and existing.score != post.score:
                    existing.score = post.score
                    changed = True
                if post.fav_count is not None and existing.fav_count != post.fav_count:
                    existing.fav_count = post.fav_count
                    changed = True
                if changed:
                    s.add(existing)
                continue

            tags_general = post.all_general_tags
            tags_meta = post.all_meta_tags
            tags_artist = post.all_artist_tags
            tags_character = post.all_character_tags

            raw_prompt = ", ".join(
                tags_artist + tags_character + tags_general + tags_meta
            )
            if post.rating:
                rating_value = post.rating
                rating_source = "provided"
                rating_evidence: str | None = None
            else:
                inferred, evidence = infer_image_rating(tags_general)
                rating_value = inferred
                rating_source = "inferred"
                rating_evidence = ",".join(evidence[:16]) or None

            img = Image(
                source_id=source_id,
                external_id=str(post.id),
                rating=rating_value,
                rating_source=rating_source,
                rating_evidence=rating_evidence,
                score=post.score,
                fav_count=post.fav_count,
                nai_model=None,
                software=site,
                width=post.image_width,
                height=post.image_height,
                raw_prompt=raw_prompt,
                raw_negative=None,
                external_created_at=_parse_booru_date(post.created_at) if post.created_at else None,
            )
            s.add(img)
            s.flush()
            inserted += 1

            # Stage 1 classification: artist/character/meta are deterministic
            for order_idx, name in enumerate(tags_general):
                tag_id = _resolve_tag(s, name, tag_cache)
                s.add(ImageTag(image_id=img.id, tag_id=tag_id, order_idx=order_idx))
            offset = len(tags_general)
            for i, name in enumerate(tags_artist):
                t = s.exec(select(Tag).where(Tag.name == name)).first()
                if t is None:
                    t = Tag(
                        name=name,
                        display=name.replace("_", " "),
                        bucket="artist",
                        bucket_source="dataset_category",
                        confidence=1.0,
                        category=1,
                    )
                    s.add(t)
                    s.flush()
                tag_cache[name] = t.id  # type: ignore[assignment]
                s.add(ImageTag(image_id=img.id, tag_id=t.id, order_idx=offset + i))
            offset += len(tags_artist)
            for i, name in enumerate(tags_character):
                t = s.exec(select(Tag).where(Tag.name == name)).first()
                if t is None:
                    t = Tag(
                        name=name,
                        display=name.replace("_", " "),
                        bucket="character",
                        bucket_source="dataset_category",
                        confidence=1.0,
                        category=4,
                    )
                    s.add(t)
                    s.flush()
                tag_cache[name] = t.id  # type: ignore[assignment]
                s.add(ImageTag(image_id=img.id, tag_id=t.id, order_idx=offset + i))

            # Include character tags so scene_line[character] is populated
            # for booru fetches — the exporter reads from scene_line, so
            # this is what makes `character.txt` exports work for trending
            # Danbooru characters. ``tags_character`` is already in
            # ImageTag (above), so this is a pure flow change.
            groups = _bucket_groups(
                s, tags_general + tags_character, drop_character_tags=False
            )
            _persist_scene_lines(s, img.id, groups)

    return inserted, source_id, source_reused


def _rebuild_scene_lines(
    *,
    drop_character_tags: bool = False,
    changed_tag_ids: list[int] | None = None,
    job_id: int | None = None,
    progress_range: tuple[float, float] = (0.0, 1.0),
) -> int:
    """Rebuild ``scene_line`` rows from current tag classifications.

    ``changed_tag_ids`` narrows the rebuild to only the images that reference
    those tags (delta rebuild after a classify pass); ``None`` rebuilds
    everything. The tag->bucket map is preloaded once, and images are
    processed in batched sessions — the difference between minutes and
    seconds at a 250k-image corpus.
    """
    with db.session_scope() as s:
        if changed_tag_ids is not None:
            if not changed_tag_ids:
                return 0
            affected: set[int] = set()
            # Chunk the IN clause to stay under SQLite's variable limit.
            for i in range(0, len(changed_tag_ids), 500):
                chunk = changed_tag_ids[i : i + 500]
                for row in s.exec(
                    select(ImageTag.image_id)
                    .where(ImageTag.tag_id.in_(chunk))  # type: ignore[attr-defined]
                    .distinct()
                ).all():
                    affected.add(row)
            image_ids = sorted(affected)
        else:
            image_ids = list(s.exec(select(Image.id)).all())
        tag_map = _load_tag_bucket_map(s)

    count = 0
    total = len(image_ids)
    for start in range(0, total, 500):
        batch = image_ids[start : start + 500]
        with db.session_scope() as s:
            for img_id in batch:
                rows = s.exec(
                    select(Tag.name)
                    .join(ImageTag, ImageTag.tag_id == Tag.id)
                    .where(ImageTag.image_id == img_id)
                    .order_by(ImageTag.order_idx)
                ).all()
                groups = _bucket_groups(
                    s, list(rows), drop_character_tags, tag_map
                )
                _persist_scene_lines(s, img_id, groups, tag_map)
                count += 1
        if job_id is not None and total:
            lo, hi = progress_range
            jobs.update_job(
                job_id,
                progress=lo + (hi - lo) * (count / total),
                message=f"rebuilding scene lines {count:,}/{total:,}",
            )
    return count


def _rebuild_booru_character_scene_lines() -> int:
    """Repopulate ``scene_line[bucket=character]`` for booru images only."""
    kinds = settings.origin_kinds("booru")
    if not kinds:
        return 0
    with db.session_scope() as s:
        image_ids = list(
            s.exec(
                select(Image.id)
                .join(Source, Source.id == Image.source_id)
                .where(Source.kind.in_(kinds))  # type: ignore[arg-type]
            ).all()
        )
        tag_map = _load_tag_bucket_map(s)

    count = 0
    for start in range(0, len(image_ids), 500):
        batch = image_ids[start : start + 500]
        with db.session_scope() as s:
            for img_id in batch:
                rows = s.exec(
                    select(Tag.name)
                    .join(ImageTag, ImageTag.tag_id == Tag.id)
                    .where(ImageTag.image_id == img_id)
                    .order_by(ImageTag.order_idx)
                ).all()
                groups = _bucket_groups(
                    s, list(rows), drop_character_tags=False, tag_map=tag_map
                )
                char_tags = filter_tag_names_for_scene(groups.get("character", []))

                for old in s.exec(
                    select(SceneLine)
                    .where(SceneLine.image_id == img_id)
                    .where(SceneLine.bucket == "character")
                ).all():
                    s.delete(old)
                s.flush()

                if char_tags:
                    s.add(
                        SceneLine(
                            image_id=img_id,
                            bucket="character",
                            tag_text=", ".join(
                                t.replace("_", " ") for t in char_tags
                            ),
                            tag_count=len(char_tags),
                        )
                    )
                count += 1
    return count

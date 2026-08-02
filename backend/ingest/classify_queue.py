"""Pre-flight counts for Smart classify passes."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, or_
from sqlmodel import select

from .. import db
from ..models import Image, Tag
from .stage3_llm import _load_cache
from .tag_categorizer import SCENE_BUCKETS, classify_tag


def _count_stage2_residuals() -> int:
    """Tags Stage 2 will embed (``other`` + unknown/tag_tree source only)."""
    with db.session_scope() as s:
        return int(
            s.exec(
                select(func.count())
                .select_from(Tag)
                .where(Tag.bucket == "other")
                .where(Tag.bucket_source.in_(("unknown", "tag_tree")))  # type: ignore[arg-type]
                .where(Tag.locked == False)  # noqa: E712
            ).one()
        )


def _stage3_residual_names(max_tags: Optional[int] = None) -> list[str]:
    with db.session_scope() as s:
        rows = s.exec(
            select(Tag.name)
            .where(Tag.bucket == "other")
            .where(Tag.locked == False)  # noqa: E712
            .order_by(Tag.post_count.desc())  # type: ignore[attr-defined]
        ).all()
    names = list(rows)
    if max_tags is not None:
        names = names[:max_tags]
    return names


def _count_stage3_residuals() -> int:
    with db.session_scope() as s:
        return int(
            s.exec(
                select(func.count())
                .select_from(Tag)
                .where(Tag.bucket == "other")
                .where(Tag.locked == False)  # noqa: E712
            ).one()
        )


def _count_stage2_reset(below_confidence: float) -> int:
    with db.session_scope() as s:
        return int(
            s.exec(
                select(func.count())
                .select_from(Tag)
                .where(Tag.bucket_source == "embed")
                .where(Tag.confidence < below_confidence)
                .where(Tag.locked == False)  # noqa: E712
            ).one()
        )


def _count_rating_images(only_missing: bool) -> int:
    with db.session_scope() as s:
        q = select(func.count()).select_from(Image)
        if only_missing:
            q = q.where(Image.rating_source.is_(None))  # type: ignore[attr-defined]
        return int(s.exec(q).one())


def _count_stage1_eligible(
    *,
    replace_below_confidence: float,
    touch_other: bool,
) -> tuple[int, int]:
    """Return ``(eligible_upgrades, candidate_tags_scanned)``."""
    cap = replace_below_confidence
    with db.session_scope() as s:
        candidates = list(
            s.exec(
                select(Tag.id, Tag.name, Tag.bucket, Tag.bucket_source, Tag.confidence)
                .where(Tag.locked == False)  # noqa: E712
                .where(
                    or_(
                        Tag.bucket == "other",
                        Tag.bucket_source.in_(("embed", "llm")),  # type: ignore[arg-type]
                        Tag.bucket.in_(tuple(SCENE_BUCKETS)),  # type: ignore[arg-type]
                        Tag.bucket_source.in_(("unknown", "tag_tree")),  # type: ignore[arg-type]
                    )
                )
            ).all()
        )

    eligible = 0
    for _tid, name, bucket, src, conf in candidates:
        assignment = classify_tag(name)
        if assignment.bucket == "other" and assignment.bucket_source == "unknown":
            continue

        src = src or ""
        is_eligible = False
        if assignment.bucket_source == "scene_exclude" and bucket in SCENE_BUCKETS:
            is_eligible = True
        elif touch_other and bucket == "other":
            is_eligible = True
        elif src in ("embed", "llm") and (conf or 0) <= cap:
            is_eligible = True
        elif src in ("unknown", "tag_tree") and (conf or 0) < assignment.confidence:
            is_eligible = True

        if (
            is_eligible
            and bucket == assignment.bucket
            and src == assignment.bucket_source
        ):
            is_eligible = False

        if is_eligible:
            eligible += 1

    return eligible, len(candidates)


def get_classify_queue_stats(
    *,
    replace_below_confidence: float = 0.85,
    reset_below_confidence: float = 0.65,
    touch_other: bool = True,
    max_tags: Optional[int] = None,
    rating_only_missing: bool = True,
) -> dict[str, Any]:
    """Counts shown beside each Smart classify action."""
    stage2_pending = _count_stage2_residuals()
    stage3_total = _count_stage3_residuals()
    stage3_names = _stage3_residual_names(max_tags)
    cache = _load_cache()
    stage3_uncached = sum(1 for n in stage3_names if n not in cache)
    stage3_cached = len(stage3_names) - stage3_uncached
    stage3_would_process = len(stage3_names)

    stage1_eligible, stage1_candidates = _count_stage1_eligible(
        replace_below_confidence=replace_below_confidence,
        touch_other=touch_other,
    )

    with db.session_scope() as s:
        total_tags = int(s.exec(select(func.count()).select_from(Tag)).one())
        other_unlocked = int(
            s.exec(
                select(func.count())
                .select_from(Tag)
                .where(Tag.bucket == "other")
                .where(Tag.locked == False)  # noqa: E712
            ).one()
        )

    return {
        "total_tags": total_tags,
        "other_unlocked": other_unlocked,
        "stage2": {
            "pending": stage2_pending,
            "description": "other + unknown/tag_tree (unlocked)",
        },
        "stage3": {
            "pending_total": stage3_total,
            "would_process": stage3_would_process,
            "uncached": stage3_uncached,
            "cached_in_batch": stage3_cached,
            "max_tags_cap": max_tags,
            "description": "other (unlocked); capped by max tags when set",
        },
        "stage1": {
            "eligible": stage1_eligible,
            "candidates_scanned": stage1_candidates,
            "replace_below_confidence": replace_below_confidence,
            "touch_other": touch_other,
        },
        "stage2_reset": {
            "pending": _count_stage2_reset(reset_below_confidence),
            "below_confidence": reset_below_confidence,
        },
        "ratings": {
            "pending": _count_rating_images(rating_only_missing),
            "only_missing": rating_only_missing,
        },
    }

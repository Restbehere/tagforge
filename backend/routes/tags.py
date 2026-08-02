"""Tag browse / manual-override endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select

from .. import db
from ..models import ImageTag, Tag, TagBucketOverride, TagClassificationHistory
from ..ingest.tag_categorizer import BUCKETS
from ..ingest.tag_history import record_change


router = APIRouter()


@router.get("")
def list_tags(
    bucket: Optional[str] = None,
    bucket_source: Optional[str] = None,
    search: Optional[str] = None,
    min_confidence: Optional[float] = None,
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    sort: str = "usage",  # 'name' | 'usage' | 'confidence' | 'confidence_asc' | 'post_count'
) -> dict[str, Any]:
    with db.session_scope() as s:
        usage_sub = (
            select(ImageTag.tag_id, func.count(ImageTag.image_id).label("usage"))
            .group_by(ImageTag.tag_id)
            .subquery()
        )

        q = select(Tag, func.coalesce(usage_sub.c.usage, 0).label("usage")).join(
            usage_sub, usage_sub.c.tag_id == Tag.id, isouter=True
        )
        # Count the same filters, but off a bare Tag select: the usage
        # LEFT JOIN and ORDER BY are irrelevant to the total and expensive.
        count_q = select(func.count(Tag.id)).select_from(Tag)
        if bucket:
            q = q.where(Tag.bucket == bucket)
            count_q = count_q.where(Tag.bucket == bucket)
        if bucket_source:
            q = q.where(Tag.bucket_source == bucket_source)
            count_q = count_q.where(Tag.bucket_source == bucket_source)
        if search:
            q = q.where(Tag.name.contains(search, autoescape=True))  # type: ignore[attr-defined]
            count_q = count_q.where(Tag.name.contains(search, autoescape=True))  # type: ignore[attr-defined]
        if min_confidence is not None:
            q = q.where(Tag.confidence >= min_confidence)
            count_q = count_q.where(Tag.confidence >= min_confidence)

        if sort == "name":
            q = q.order_by(Tag.name)
        elif sort == "confidence":
            q = q.order_by(Tag.confidence.desc())
        elif sort == "confidence_asc":
            # Surfaces the most uncertain Stage-2/Stage-3 relabels at the top
            # so audits start with the riskiest tags.
            q = q.order_by(Tag.confidence.asc())
        elif sort == "post_count":
            q = q.order_by(Tag.post_count.desc())
        else:
            q = q.order_by(func.coalesce(usage_sub.c.usage, 0).desc())

        total = s.exec(count_q).one()
        rows = list(s.exec(q.offset(offset).limit(limit)).all())

        return {
            "total": total,
            "items": [
                {
                    "id": t.id,
                    "name": t.name,
                    "bucket": t.bucket,
                    "bucket_source": t.bucket_source,
                    "confidence": t.confidence,
                    "post_count": t.post_count,
                    "category": t.category,
                    "locked": t.locked,
                    "usage": usage,
                }
                for t, usage in rows
            ],
        }


@router.get("/buckets")
def list_buckets() -> list[str]:
    return list(BUCKETS)


class BucketUpdate(BaseModel):
    bucket: str
    note: Optional[str] = None
    # lock=True (default): manual override — pin the bucket, exclude the tag
    # from future automated passes, persist a TagBucketOverride.
    # lock=False: release — used by "undo" in the UI. Sets the bucket but
    # returns the tag to the unclassified pool (source=unknown, unlocked)
    # and removes any override so re-ingest doesn't re-pin it.
    lock: bool = True


@router.post("/{tag_name}/bucket")
def set_bucket(tag_name: str, body: BucketUpdate) -> dict[str, Any]:
    if body.bucket not in BUCKETS:
        raise HTTPException(400, f"unknown bucket: {body.bucket}")

    with db.session_scope() as s:
        tag = s.exec(select(Tag).where(Tag.name == tag_name)).first()
        if tag is None:
            # SceneLines store display names (spaces); try Tag.display so edits
            # from the Trends page work without needing the canonical form.
            tag = s.exec(select(Tag).where(Tag.display == tag_name)).first()
        if tag is None:
            raise HTTPException(404, f"tag not found: {tag_name}")

        new_source = "manual" if body.lock else "unknown"
        new_confidence = 1.0 if body.lock else 0.0

        record_change(
            s,
            tag,
            new_bucket=body.bucket,
            new_source="manual",
            new_confidence=new_confidence,
            model=None,
            job_id=None,
        )
        tag.bucket = body.bucket
        tag.bucket_source = new_source
        tag.confidence = new_confidence
        tag.locked = body.lock
        tag.updated_at = datetime.utcnow()
        s.add(tag)

        # Override rows are keyed by the canonical name, not the URL form.
        ov = s.exec(
            select(TagBucketOverride).where(TagBucketOverride.tag_name == tag.name)
        ).first()
        if body.lock:
            if ov is None:
                ov = TagBucketOverride(
                    tag_name=tag.name, bucket=body.bucket, note=body.note
                )
            else:
                ov.bucket = body.bucket
                ov.note = body.note
            s.add(ov)
        elif ov is not None:
            s.delete(ov)

        return {
            "ok": True,
            "tag": {
                "name": tag.name,
                "bucket": tag.bucket,
                "bucket_source": tag.bucket_source,
                "locked": tag.locked,
            },
        }


# ---------------------------------------------------------------------------
# Audit log: every Stage-2/Stage-3/manual relabel writes a
# TagClassificationHistory row. These endpoints let the UI browse + roll
# back individual relabels.
# ---------------------------------------------------------------------------


@router.get("/history")
def list_history(
    to_source: Optional[str] = Query(None, description="'embed' | 'llm' | 'manual' | 'backfill'"),
    from_bucket: Optional[str] = None,
    to_bucket: Optional[str] = None,
    search: Optional[str] = None,
    job_id: Optional[int] = None,
    max_confidence: Optional[float] = Query(
        None, description="only rows where to_confidence <= this (risk audit)"
    ),
    min_confidence: Optional[float] = None,
    sort: str = Query(
        "recent",
        description="'recent' (newest first) | 'confidence_asc' (riskiest first)",
    ),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with db.session_scope() as s:
        q = select(TagClassificationHistory)
        if to_source:
            q = q.where(TagClassificationHistory.to_source == to_source)
        if from_bucket:
            q = q.where(TagClassificationHistory.from_bucket == from_bucket)
        if to_bucket:
            q = q.where(TagClassificationHistory.to_bucket == to_bucket)
        if search:
            q = q.where(TagClassificationHistory.tag_name.contains(search, autoescape=True))  # type: ignore[attr-defined]
        if job_id is not None:
            q = q.where(TagClassificationHistory.job_id == job_id)
        if max_confidence is not None:
            q = q.where(TagClassificationHistory.to_confidence <= max_confidence)
        if min_confidence is not None:
            q = q.where(TagClassificationHistory.to_confidence >= min_confidence)

        # Total must reflect the filters (the UI shows "N of TOTAL"); count
        # before the sort/pagination are attached.
        total = s.exec(select(func.count()).select_from(q.subquery())).one()

        if sort == "confidence_asc":
            q = q.order_by(TagClassificationHistory.to_confidence.asc())
        else:
            q = q.order_by(TagClassificationHistory.at.desc())

        rows = list(s.exec(q.offset(offset).limit(limit)).all())

        return {
            "total": total,
            "items": [
                {
                    "id": h.id,
                    "tag_id": h.tag_id,
                    "tag_name": h.tag_name,
                    "from_bucket": h.from_bucket,
                    "from_source": h.from_source,
                    "from_confidence": h.from_confidence,
                    "to_bucket": h.to_bucket,
                    "to_source": h.to_source,
                    "to_confidence": h.to_confidence,
                    "model": h.model,
                    "job_id": h.job_id,
                    "at": h.at.isoformat(),
                }
                for h in rows
            ],
        }


@router.get("/history/stats")
def history_stats(
    to_source: Optional[str] = None,
    job_id: Optional[int] = None,
) -> dict[str, Any]:
    """Per-(from→to) bucket histogram for the audit panel summary."""
    with db.session_scope() as s:
        q = select(
            TagClassificationHistory.from_bucket,
            TagClassificationHistory.to_bucket,
            func.count(TagClassificationHistory.id).label("n"),
            func.avg(TagClassificationHistory.to_confidence).label("avg_conf"),
        ).group_by(
            TagClassificationHistory.from_bucket,
            TagClassificationHistory.to_bucket,
        )
        if to_source:
            q = q.where(TagClassificationHistory.to_source == to_source)
        if job_id is not None:
            q = q.where(TagClassificationHistory.job_id == job_id)

        rows = s.exec(q).all()
        return {
            "items": [
                {
                    "from_bucket": fb,
                    "to_bucket": tb,
                    "count": int(n),
                    "avg_confidence": float(c) if c is not None else None,
                }
                for fb, tb, n, c in rows
            ]
        }


class HistoryRevertBody(BaseModel):
    lock: bool = True  # also set locked=True so future passes won't redo it
    note: Optional[str] = None


@router.post("/history/{history_id}/revert")
def revert_history(history_id: int, body: HistoryRevertBody) -> dict[str, Any]:
    """Roll back a single relabel by restoring the from_* state on the tag.

    The revert itself is recorded as a new history row (to_source='manual'),
    so the audit log stays complete.
    """
    with db.session_scope() as s:
        h = s.get(TagClassificationHistory, history_id)
        if h is None:
            raise HTTPException(404, f"history row not found: {history_id}")
        tag = s.get(Tag, h.tag_id)
        if tag is None:
            raise HTTPException(404, f"tag not found: id={h.tag_id}")

        revert_bucket = h.from_bucket or "other"
        revert_source = h.from_source or "unknown"
        revert_conf = h.from_confidence if h.from_confidence is not None else 0.0

        record_change(
            s,
            tag,
            new_bucket=revert_bucket,
            new_source="manual",
            new_confidence=1.0 if body.lock else revert_conf,
            model=None,
            job_id=None,
        )
        tag.bucket = revert_bucket
        tag.bucket_source = "manual" if body.lock else revert_source
        tag.confidence = 1.0 if body.lock else revert_conf
        if body.lock:
            tag.locked = True
            ov = s.exec(
                select(TagBucketOverride).where(TagBucketOverride.tag_name == tag.name)
            ).first()
            if ov is None:
                ov = TagBucketOverride(
                    tag_name=tag.name,
                    bucket=revert_bucket,
                    note=body.note or f"reverted history#{history_id}",
                )
            else:
                ov.bucket = revert_bucket
                ov.note = body.note or ov.note
            s.add(ov)
        tag.updated_at = datetime.utcnow()
        s.add(tag)

        return {
            "ok": True,
            "tag": {
                "name": tag.name,
                "bucket": tag.bucket,
                "bucket_source": tag.bucket_source,
                "locked": tag.locked,
            },
        }


class BackfillBody(BaseModel):
    sources: list[str] = ["embed", "llm"]  # which bucket_sources to backfill


@router.post("/history/backfill")
def backfill_history(body: BackfillBody) -> dict[str, Any]:
    """One-shot backfill so the audit panel has data for runs that
    happened *before* this audit log existed.

    Strategy: for every Tag whose ``bucket_source`` is in ``sources``, write
    a single history row asserting ``from_bucket='other'`` (which is the
    invariant that Stage 2 + Stage 3 enforce — they only touch tags that
    are still ``bucket='other'``). The ``at`` timestamp is the tag's
    ``updated_at`` so the timeline still makes sense.

    Idempotent: tags that already have a history row for the current
    ``to_source`` are skipped, so this can be safely re-run.
    """
    if not body.sources:
        raise HTTPException(400, "sources must be non-empty")
    for src in body.sources:
        if src not in ("embed", "llm"):
            raise HTTPException(400, f"unsupported source for backfill: {src}")

    inserted = 0
    skipped = 0
    with db.session_scope() as s:
        for src in body.sources:
            tags = list(
                s.exec(select(Tag).where(Tag.bucket_source == src)).all()
            )
            for t in tags:
                if t.id is None:
                    continue
                existing = s.exec(
                    select(TagClassificationHistory.id)
                    .where(TagClassificationHistory.tag_id == t.id)
                    .where(TagClassificationHistory.to_source == src)
                ).first()
                if existing is not None:
                    skipped += 1
                    continue
                h = TagClassificationHistory(
                    tag_id=t.id,
                    tag_name=t.name,
                    from_bucket="other",
                    from_source="unknown",
                    from_confidence=0.0,
                    to_bucket=t.bucket,
                    to_source=src,
                    to_confidence=t.confidence,
                    model=None,
                    job_id=None,
                    at=t.updated_at,
                )
                s.add(h)
                inserted += 1

    return {"inserted": inserted, "skipped": skipped}

"""Trend analysis endpoints (window-vs-baseline tag deltas)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select

from .. import db
from ..models import FeatureLog, Image, ImageTag, SceneLine, Source, Tag

# Use the Danbooru post date when available, fall back to our ingest timestamp.
# This expression is used in every date-window filter in this module.
_post_date = func.coalesce(Image.external_created_at, Image.created_at)


router = APIRouter()


def _compute_delta(
    *,
    recent_days: int,
    baseline_days: int,
    bucket: Optional[str],
    rating: Optional[str],
    booru_only: bool,
    source_id: Optional[int],
    limit: int,
    compare_offset_days: int = 0,
) -> dict[str, Any]:
    now = datetime.utcnow()
    recent_start = now - timedelta(days=recent_days)
    if compare_offset_days > 0:
        # Offset compare: baseline is the same-length window ending
        # compare_offset_days earlier. baseline_days is ignored here.
        baseline_hi = now - timedelta(days=compare_offset_days)
        baseline_lo = baseline_hi - timedelta(days=recent_days)
    else:
        # Default: baseline is the window immediately preceding recent.
        baseline_hi = recent_start
        baseline_lo = now - timedelta(days=baseline_days)
    baseline_start = baseline_lo

    with db.session_scope() as s:

        # ------------------------------------------------------------------
        # Character bucket: split SceneLine text so "hatsune miku, rem"
        # contributes +1 to each character individually, not to the
        # combined string.
        # ------------------------------------------------------------------
        if bucket == "character":
            # Tags the user has manually moved away from character. SceneLine
            # is a pre-built snapshot so we filter these out here instead of
            # requiring a full scene rebuild after every bucket edit.
            # SceneLine stores display names (spaces), so we compare against
            # Tag.display (not Tag.name which has underscores).
            excluded_chars: set[str] = {
                row[0] if not isinstance(row, str) else row
                for row in s.exec(
                    select(Tag.display)
                    .where(Tag.locked == True)  # noqa: E712
                    .where(Tag.bucket != "character")
                ).all()
                if (row[0] if not isinstance(row, str) else row)
            }

            def _char_counts(start: datetime, end: Optional[datetime] = None) -> dict[str, int]:
                q = (
                    select(SceneLine.tag_text)
                    .join(Image, Image.id == SceneLine.image_id)
                    .where(SceneLine.bucket == "character")
                    .where(_post_date >= start)
                )
                if end is not None:
                    q = q.where(_post_date < end)
                if rating:
                    q = q.where(Image.rating.in_(rating.split(",")))  # type: ignore[arg-type]
                if booru_only:
                    q = q.join(Source, Source.id == Image.source_id).where(
                        Source.kind != "metadata_file"
                    )
                if source_id is not None:
                    q = q.where(Image.source_id == source_id)
                counts: dict[str, int] = {}
                for row in s.exec(q).all():
                    tag_text = row[0] if not isinstance(row, str) else row
                    if not tag_text:
                        continue
                    for name in tag_text.split(", "):
                        name = name.strip()
                        if name and name not in excluded_chars:
                            counts[name] = counts.get(name, 0) + 1
                return counts

            recent_counts = _char_counts(recent_start)
            baseline_counts = _char_counts(baseline_lo, baseline_hi)

            items: list[dict[str, Any]] = []
            for name, rc in recent_counts.items():
                bc = float(baseline_counts.get(name, 0))
                items.append({
                    "tag_id": -1,
                    "name": name,
                    "bucket": "character",
                    "recent": int(rc),
                    "baseline": int(bc),
                    "ratio": (float(rc) + 1.0) / (bc + 1.0),
                })
            items.sort(key=lambda x: x["ratio"], reverse=True)
            return {
                "recent_days": recent_days,
                "baseline_days": baseline_days,
                "recent_start": recent_start.isoformat(),
                "baseline_start": baseline_start.isoformat(),
                "items": items[:limit],
            }

        # ------------------------------------------------------------------
        # All other buckets: ImageTag → Tag join (one count per image-tag).
        # ------------------------------------------------------------------
        def _apply_filters(q, *, with_bucket: bool = True):
            if with_bucket and bucket:
                q = q.where(Tag.bucket == bucket)
            if rating:
                q = q.where(Image.rating.in_(rating.split(",")))  # type: ignore[arg-type]
            if booru_only:
                q = q.join(Source, Source.id == Image.source_id).where(
                    Source.kind != "metadata_file"
                )
            if source_id is not None:
                q = q.where(Image.source_id == source_id)
            return q

        recent_q = _apply_filters(
            select(Tag.id, Tag.name, Tag.bucket, func.count(ImageTag.image_id).label("c"))
            .join(ImageTag, ImageTag.tag_id == Tag.id)
            .join(Image, Image.id == ImageTag.image_id)
            .where(_post_date >= recent_start)
            .group_by(Tag.id, Tag.name, Tag.bucket)
        )
        baseline_q = _apply_filters(
            select(Tag.id, func.count(ImageTag.image_id).label("c"))
            .join(ImageTag, ImageTag.tag_id == Tag.id)
            .join(Image, Image.id == ImageTag.image_id)
            .where(_post_date >= baseline_lo)
            .where(_post_date < baseline_hi)
            .group_by(Tag.id)
        )

        recent = {r.id: r for r in s.exec(recent_q).all()}
        baseline = {r.id: r.c for r in s.exec(baseline_q).all()}

        items = []
        for tag_id, row in recent.items():
            rc = float(row.c)
            bc = float(baseline.get(tag_id, 0))
            items.append({
                "tag_id": tag_id,
                "name": row.name,
                "bucket": row.bucket,
                "recent": int(rc),
                "baseline": int(bc),
                "ratio": (rc + 1.0) / (bc + 1.0),
            })

        items.sort(key=lambda x: x["ratio"], reverse=True)
        return {
            "recent_days": recent_days,
            "baseline_days": baseline_days,
            "recent_start": recent_start.isoformat(),
            "baseline_start": baseline_start.isoformat(),
            "items": items[:limit],
        }


@router.get("/delta")
def delta(
    recent_days: int = Query(7, ge=1, le=365),
    baseline_days: int = Query(30, ge=1, le=730),
    bucket: Optional[str] = None,
    rating: Optional[str] = None,
    booru_only: bool = Query(False),
    source_id: Optional[int] = Query(None),
    limit: int = Query(80, ge=1, le=500),
    compare_offset_days: int = Query(0, ge=0, le=365),
) -> dict[str, Any]:
    """Per-tag frequency delta between recent and baseline windows.

    ``bucket='character'`` splits scene_line text so each character in a
    multi-character image is counted individually.
    ``booru_only=true`` excludes locally-ingested metadata files.
    ``source_id`` scopes results to a single ingest source.
    ``compare_offset_days>0`` compares against the same-length window ending
    that many days earlier (``baseline_days`` is ignored in that mode).
    """
    return _compute_delta(
        recent_days=recent_days,
        baseline_days=baseline_days,
        bucket=bucket,
        rating=rating,
        booru_only=booru_only,
        source_id=source_id,
        limit=limit,
        compare_offset_days=compare_offset_days,
    )


@router.get("/export", response_class=PlainTextResponse)
def export_top(
    recent_days: int = Query(7, ge=1, le=365),
    baseline_days: int = Query(30, ge=1, le=730),
    bucket: Optional[str] = None,
    booru_only: bool = Query(False),
    source_id: Optional[int] = Query(None),
    top: int = Query(100, ge=1, le=1000),
    compare_offset_days: int = Query(0, ge=0, le=365),
) -> str:
    """Export the top N trending tag names, one per line (plain text)."""
    result = _compute_delta(
        recent_days=recent_days,
        baseline_days=baseline_days,
        bucket=bucket,
        rating=None,
        booru_only=booru_only,
        source_id=source_id,
        limit=top,
        compare_offset_days=compare_offset_days,
    )
    return "\n".join(item["name"] for item in result["items"])


# ---------------------------------------------------------------------------
# Feature log — track which characters were featured on which channel so the
# Trends chart can show "last featured" and help pace X posts / Patreon polls.
# ---------------------------------------------------------------------------

_FEATURE_CHANNELS = {"x", "patreon"}


class FeatureLogBody(BaseModel):
    character: str
    channel: str  # 'x' | 'patreon'


def _feature_dict(row: FeatureLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "character": row.character,
        "channel": row.channel,
        "at": row.at.isoformat() + "+00:00",  # stored naive-UTC
    }


@router.post("/feature-log")
def log_feature(body: FeatureLogBody) -> dict[str, Any]:
    channel = body.channel.strip().lower()
    if channel not in _FEATURE_CHANNELS:
        raise HTTPException(400, f"unknown channel: {body.channel}")
    character = body.character.strip()
    if not character:
        raise HTTPException(400, "character is required")
    with db.session_scope() as s:
        row = FeatureLog(character=character, channel=channel)
        s.add(row)
        s.flush()
        return _feature_dict(row)


@router.get("/feature-log")
def list_feature_log(limit: int = Query(500, ge=1, le=2000)) -> list[dict[str, Any]]:
    with db.session_scope() as s:
        rows = list(
            s.exec(
                select(FeatureLog).order_by(FeatureLog.at.desc()).limit(limit)  # type: ignore[attr-defined]
            ).all()
        )
        return [_feature_dict(r) for r in rows]


@router.delete("/feature-log/{log_id}")
def delete_feature_log(log_id: int) -> dict[str, Any]:
    with db.session_scope() as s:
        row = s.get(FeatureLog, log_id)
        if row is None:
            raise HTTPException(404, "feature log entry not found")
        s.delete(row)
        return {"ok": True}

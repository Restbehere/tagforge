"""Dashboard summary endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import select

from .. import db, settings
from ..models import Image, Job, SceneLine, Source, Tag


router = APIRouter()


@router.get("/summary")
def summary() -> dict[str, Any]:
    with db.session_scope() as s:
        image_count = s.exec(select(func.count(Image.id))).one()
        tag_count = s.exec(select(func.count(Tag.id))).one()
        source_count = s.exec(select(func.count(Source.id))).one()
        scene_count = s.exec(select(func.count(SceneLine.id))).one()

        by_source = s.exec(
            select(Source.kind, func.count(Image.id))
            .join(Image, Image.source_id == Source.id, isouter=True)
            .group_by(Source.kind)
        ).all()

        by_bucket = s.exec(
            select(Tag.bucket, func.count(Tag.id)).group_by(Tag.bucket)
        ).all()

        scene_by_bucket = s.exec(
            select(SceneLine.bucket, func.count(SceneLine.id)).group_by(
                SceneLine.bucket
            )
        ).all()

        recent_jobs = [
            {
                "id": j.id,
                "kind": j.kind,
                "label": j.label,
                "status": j.status,
                "progress": j.progress,
                "message": j.message,
                "updated_at": j.updated_at.isoformat(),
            }
            for j in s.exec(
                select(Job).order_by(Job.created_at.desc()).limit(8)
            ).all()
        ]

        classified_tags = s.exec(
            select(func.count(Tag.id)).where(Tag.bucket != "other")
        ).one()

    coverage = float(classified_tags) / float(tag_count) if tag_count else 0.0

    return {
        "image_count": image_count,
        "tag_count": tag_count,
        "source_count": source_count,
        "scene_count": scene_count,
        "classifier_coverage": coverage,
        "by_source": [
            {"kind": k, "count": c} for k, c in by_source if k is not None
        ],
        "by_bucket": [{"bucket": b, "count": c} for b, c in by_bucket],
        "scene_by_bucket": [{"bucket": b, "count": c} for b, c in scene_by_bucket],
        "recent_jobs": recent_jobs,
        "default_metadata_path": (
            str(settings.DEFAULT_METADATA_FILE)
            if settings.DEFAULT_METADATA_FILE
            else ""
        ),
        "default_wildcards_dir": str(settings.KOHAKU_WILDCARDS_DIR),
        "kohaku_tags_jsonl_exists": settings.KOHAKU_TAGS_JSONL.exists(),
    }

"""Scene browser endpoints."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlmodel import select

from .. import db, settings
from ..models import Image, ImageTag, SceneLine, Source, Tag


router = APIRouter()


@router.get("")
def list_scenes(
    source_id: Optional[int] = None,
    origin: Optional[str] = Query(None, description="local | booru"),
    rating: Optional[str] = None,
    nai_model: Optional[str] = None,
    score_min: Optional[int] = None,
    search: Optional[str] = None,
    external_id: Optional[str] = Query(
        None, description="exact external id (e.g. a Danbooru post id)"
    ),
    has_outfit: bool = False,
    has_background: bool = False,
    multi_char: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with db.session_scope() as s:
        q = select(Image)

        if source_id is not None:
            q = q.where(Image.source_id == source_id)
        origin_kinds = settings.origin_kinds(origin)
        if origin_kinds is not None:
            origin_ids = list(
                s.exec(select(Source.id).where(Source.kind.in_(origin_kinds))).all()  # type: ignore[arg-type]
            )
            if not origin_ids:
                return {"total": 0, "items": []}
            q = q.where(Image.source_id.in_(origin_ids))  # type: ignore[arg-type]
        if rating:
            ratings = rating.split(",")
            q = q.where(Image.rating.in_(ratings))  # type: ignore[arg-type]
        if nai_model:
            q = q.where(Image.nai_model == nai_model)
        if score_min is not None:
            q = q.where(Image.score >= score_min)
        if search:
            q = q.where(Image.raw_prompt.contains(search, autoescape=True))  # type: ignore[attr-defined]
        if external_id:
            q = q.where(Image.external_id == external_id.strip())
        if multi_char:
            q = q.where(
                or_(
                    *[
                        # autoescape: '_' in tags like multiple_girls is a
                        # literal, not a LIKE single-char wildcard.
                        Image.raw_prompt.contains(t, autoescape=True)  # type: ignore[attr-defined]
                        for t in settings.MULTI_CHAR_TAGS
                    ],
                    *[
                        and_(
                            Image.raw_prompt.contains(a, autoescape=True),  # type: ignore[attr-defined]
                            Image.raw_prompt.contains(b, autoescape=True),  # type: ignore[attr-defined]
                        )
                        for a, b in settings.MULTI_CHAR_PAIRS
                    ],
                )
            )
        if has_outfit:
            sub = select(SceneLine.image_id).where(SceneLine.bucket == "outfit")
            q = q.where(Image.id.in_(sub))  # type: ignore[arg-type]
        if has_background:
            sub2 = select(SceneLine.image_id).where(SceneLine.bucket == "background")
            q = q.where(Image.id.in_(sub2))  # type: ignore[arg-type]

        total = s.exec(select(func.count()).select_from(q.subquery())).one()

        q = q.order_by(Image.id.desc()).offset(offset).limit(limit)
        rows = list(s.exec(q).all())

        ids = [r.id for r in rows]
        previews: dict[int, dict[str, str]] = {i: {} for i in ids}
        if ids:
            for sl in s.exec(
                select(SceneLine).where(SceneLine.image_id.in_(ids))  # type: ignore[arg-type]
            ).all():
                previews.setdefault(sl.image_id, {})[sl.bucket] = sl.tag_text

        return {
            "total": total,
            "items": [
                {
                    "id": r.id,
                    "source_id": r.source_id,
                    "external_id": r.external_id,
                    "rating": r.rating,
                    "rating_source": r.rating_source,
                    "score": r.score,
                    "fav_count": r.fav_count,
                    "nai_model": r.nai_model,
                    "software": r.software,
                    "width": r.width,
                    "height": r.height,
                    "buckets": previews.get(r.id, {}),
                }
                for r in rows
            ],
        }


@router.get("/sources")
def list_sources() -> list[dict[str, Any]]:
    with db.session_scope() as s:
        rows = list(s.exec(select(Source).order_by(Source.fetched_at.desc())).all())
        out: list[dict[str, Any]] = []
        for r in rows:
            origin = (
                "local"
                if r.kind in settings.ORIGIN_LOCAL
                else "booru"
                if r.kind in settings.ORIGIN_BOORU
                else "other"
            )
            out.append(
                {
                    "id": r.id,
                    "kind": r.kind,
                    "origin": origin,
                    "label": r.label,
                    "fetched_at": r.fetched_at.isoformat(),
                    "image_count": r.image_count,
                    "note": r.note,
                }
            )
        return out


@router.get("/{image_id}")
def get_scene(image_id: int) -> dict[str, Any]:
    with db.session_scope() as s:
        img = s.get(Image, image_id)
        if img is None:
            raise HTTPException(404, f"image not found: {image_id}")
        scene_lines = list(
            s.exec(select(SceneLine).where(SceneLine.image_id == image_id)).all()
        )
        tag_rows = s.exec(
            select(Tag, ImageTag.order_idx)
            .join(ImageTag, ImageTag.tag_id == Tag.id)
            .where(ImageTag.image_id == image_id)
            .order_by(ImageTag.order_idx)
        ).all()
        evidence: list[str] = []
        if img.rating_evidence:
            evidence = [t for t in img.rating_evidence.split(",") if t]
        src = s.get(Source, img.source_id)
        origin = (
            "local"
            if src and src.kind in settings.ORIGIN_LOCAL
            else "booru"
            if src and src.kind in settings.ORIGIN_BOORU
            else "other"
        )
        return {
            "id": img.id,
            "source_id": img.source_id,
            "origin": origin,
            "subjects": settings.subject_summary(img.raw_prompt),
            "external_id": img.external_id,
            "rating": img.rating,
            "rating_source": img.rating_source,
            "rating_evidence": evidence,
            "score": img.score,
            "nai_model": img.nai_model,
            "software": img.software,
            "raw_prompt": img.raw_prompt,
            "raw_negative": img.raw_negative,
            "buckets": {sl.bucket: sl.tag_text for sl in scene_lines},
            "tags": [
                {
                    "name": tg.name,
                    "bucket": tg.bucket,
                    "bucket_source": tg.bucket_source,
                    "order": idx,
                }
                for tg, idx in tag_rows
            ],
        }

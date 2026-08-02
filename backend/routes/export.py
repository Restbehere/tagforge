"""Export endpoints."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlmodel import select

from .. import db, jobs, settings
from ..ingest.exporter import _DEFAULT_DENY, build_export
from ..models import DenyList


logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_component(value: str, field: str) -> str:
    """Reject path separators / drive letters / '..' in values used as
    filesystem name components. ``output_dir`` stays the one intentional
    way to write outside the exports root."""
    if value and (
        "/" in value
        or "\\" in value
        or value in (".", "..")
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ValueError(f"{field} must be a plain name, not a path")
    return value


class ExportFilters(BaseModel):
    name: str
    output_dir: Optional[str] = None
    source_ids: list[int] = Field(default_factory=list)
    origin: Optional[str] = None  # 'local' | 'booru' | None
    ratings: list[str] = Field(default_factory=list)
    nai_models: list[str] = Field(default_factory=list)
    score_min: Optional[int] = None
    max_rating: Optional[str] = None  # 'g' | 's' | 'q' | 'e' (strip mode)
    buckets: list[str] = Field(
        default_factory=lambda: [
            "outfit",
            "pose",
            "expression",
            "background",
            "composition",
            "accessory",
            "extras",
            "character",
            "scene",
        ]
    )
    min_tag_count: int = 2  # skip nearly-empty lines
    deduplicate: bool = True
    dedupe_ignore_order: bool = True  # same tag set, different order = dupe
    file_prefix: str = ""  # optional prefix on output filenames
    use_default_deny: bool = True  # apply baked-in deny list
    extra_deny_tags: list[str] = Field(default_factory=list)  # user additions
    # Which per-bucket lines compose scene.txt (export-time recipe).
    scene_buckets: list[str] = Field(
        default_factory=lambda: ["outfit", "pose", "expression", "background"]
    )
    # Down-sample lines containing these tags to at most cap_percent of each
    # file (e.g. keep white-background scenes under 25%).
    cap_tags: list[str] = Field(default_factory=list)
    cap_percent: int = Field(default=100, ge=1, le=100)
    # Optional second output location — files are written to both.
    mirror_dir: Optional[str] = None

    @field_validator("name", "file_prefix")
    @classmethod
    def _no_path_components(cls, v: str, info: Any) -> str:
        return _safe_component(v, info.field_name)


def _run_export_job(job_id: int, body: dict[str, Any]) -> None:
    try:
        jobs.update_job(job_id, status="running", progress=0.05, message="querying scene lines…")
        out_dir = Path(body["output_dir"]).expanduser() if body.get("output_dir") else (
            settings.EXPORTS_DIR / body["name"]
        )
        manifest = build_export(
            name=body["name"],
            output_dir=out_dir,
            buckets=body["buckets"],
            source_ids=body["source_ids"],
            origin=body.get("origin"),
            ratings=body["ratings"],
            nai_models=body["nai_models"],
            score_min=body.get("score_min"),
            max_rating=body.get("max_rating"),
            min_tag_count=body["min_tag_count"],
            deduplicate=body["deduplicate"],
            dedupe_ignore_order=body["dedupe_ignore_order"],
            file_prefix=body["file_prefix"],
            use_default_deny=body["use_default_deny"],
            extra_deny_tags=body["extra_deny_tags"],
            scene_buckets=body["scene_buckets"],
            cap_tags=body["cap_tags"],
            cap_percent=body["cap_percent"],
            mirror_dir=Path(body["mirror_dir"]).expanduser()
            if body.get("mirror_dir")
            else None,
            on_progress=lambda frac, msg: jobs.update_job(
                job_id, progress=0.05 + 0.9 * frac, message=msg
            ),
        )
        total = sum(manifest["line_counts"].values())
        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=(
                f"wrote {len(manifest['files'])} files · {total:,} lines"
                + (" (+ mirror)" if manifest.get("mirror_dir") else "")
            ),
            detail=manifest,
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


@router.post("/run")
def run(body: ExportFilters, bg: BackgroundTasks) -> dict[str, Any]:
    """Start an export as a tracked background job. The manifest lands in
    the job's detail once it finishes."""
    job_id = jobs.create_job("export", f"export: {body.name}", body.model_dump())
    bg.add_task(_run_export_job, job_id, body.model_dump())
    return {"job_id": job_id}


@router.get("/default-deny-tags")
def default_deny_tags() -> dict[str, list[str]]:
    """Return the baked-in default deny list so the UI can show it."""
    return {"tags": sorted(_DEFAULT_DENY)}


@router.get("/preset-dirs")
def preset_dirs() -> dict[str, str]:
    return {
        "tagforge_exports": str(settings.EXPORTS_DIR),
        "kohaku_wildcards": str(settings.KOHAKU_WILDCARDS_DIR),
        "kohaku_common_prompts": str(settings.KOHAKU_COMMON_PROMPTS_DIR),
    }


# ---------------------------------------------------------------------------
# Named deny lists — save, load, and reuse deny tag sets across exports.
# ---------------------------------------------------------------------------

def _deny_list_dict(dl: DenyList) -> dict[str, Any]:
    return {
        "id": dl.id,
        "name": dl.name,
        "tags": json.loads(dl.tags_json or "[]"),
        "created_at": dl.created_at.isoformat(),
        "updated_at": dl.updated_at.isoformat(),
    }


class DenyListBody(BaseModel):
    name: str
    tags: list[str]


class DenyListUpdateBody(BaseModel):
    name: Optional[str] = None
    tags: list[str]


@router.get("/deny-lists")
def list_deny_lists() -> list[dict[str, Any]]:
    with db.session_scope() as s:
        rows = list(s.exec(select(DenyList).order_by(DenyList.name)).all())
        return [_deny_list_dict(r) for r in rows]


@router.post("/deny-lists")
def create_deny_list(body: DenyListBody) -> dict[str, Any]:
    with db.session_scope() as s:
        existing = s.exec(select(DenyList).where(DenyList.name == body.name)).first()
        if existing:
            raise HTTPException(409, f"deny list '{body.name}' already exists")
        dl = DenyList(name=body.name, tags_json=json.dumps(sorted(set(body.tags))))
        s.add(dl)
        s.flush()
        return _deny_list_dict(dl)


@router.put("/deny-lists/{list_id}")
def update_deny_list(list_id: int, body: DenyListUpdateBody) -> dict[str, Any]:
    with db.session_scope() as s:
        dl = s.get(DenyList, list_id)
        if dl is None:
            raise HTTPException(404, "deny list not found")
        if body.name is not None and body.name != dl.name:
            # The name is UNIQUE — a colliding rename would surface as an
            # unhandled IntegrityError 500 at commit time.
            clash = s.exec(select(DenyList).where(DenyList.name == body.name)).first()
            if clash is not None:
                raise HTTPException(409, f"a deny list named {body.name!r} already exists")
            dl.name = body.name
        dl.tags_json = json.dumps(sorted(set(body.tags)))
        dl.updated_at = datetime.now(timezone.utc)
        s.add(dl)
        return _deny_list_dict(dl)


@router.delete("/deny-lists/{list_id}")
def delete_deny_list(list_id: int) -> dict[str, Any]:
    with db.session_scope() as s:
        dl = s.get(DenyList, list_id)
        if dl is None:
            raise HTTPException(404, "deny list not found")
        s.delete(dl)
        return {"ok": True}

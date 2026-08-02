"""Ingest endpoints (metadata file, future booru)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from .. import jobs, settings
from ..ingest.image_extract import count_images, iter_image_records
from ..ingest.metadata_parser import iter_metadata_records, preview_metadata_file
from ..ingest.runner import run_image_folder_ingest, run_metadata_ingest


router = APIRouter()


class MetadataPreviewIn(BaseModel):
    path: str
    sample_size: int = 20


@router.post("/metadata/preview")
def preview(body: MetadataPreviewIn) -> dict[str, Any]:
    path = Path(body.path).expanduser()
    if not path.exists():
        raise HTTPException(404, f"file not found: {path}")
    return preview_metadata_file(path, sample_size=body.sample_size)


class MetadataIngestIn(BaseModel):
    path: str
    label: Optional[str] = None
    drop_artist_tags: bool = True
    drop_quality_tags: bool = True
    drop_character_tags: bool = False  # the user's character is part of the scene
    # Chain Stage-3 GPT classification of new tags + delta scene rebuild
    # onto the same job after the ingest finishes.
    classify_after: bool = False


@router.post("/metadata/start")
def start(body: MetadataIngestIn, bg: BackgroundTasks) -> dict[str, Any]:
    path = Path(body.path).expanduser()
    if not path.exists():
        raise HTTPException(404, f"file not found: {path}")

    label = body.label or f"metadata:{path.stem}"
    job_id = jobs.create_job(
        kind="ingest_metadata",
        label=label,
        detail={
            "path": str(path),
            "drop_artist_tags": body.drop_artist_tags,
            "drop_quality_tags": body.drop_quality_tags,
            "drop_character_tags": body.drop_character_tags,
        },
    )

    bg.add_task(
        run_metadata_ingest,
        job_id=job_id,
        path=path,
        label=label,
        drop_artist_tags=body.drop_artist_tags,
        drop_quality_tags=body.drop_quality_tags,
        drop_character_tags=body.drop_character_tags,
        classify_after=body.classify_after,
    )
    return {"job_id": job_id}


class FolderPreviewIn(BaseModel):
    path: str
    recursive: bool = True
    sample_size: int = 12


@router.post("/images/preview")
def preview_folder(body: FolderPreviewIn) -> dict[str, Any]:
    """Read metadata from the first few images so the user can sanity-check
    a folder before committing to a long run."""
    folder = Path(body.path).expanduser()
    if not folder.is_dir():
        raise HTTPException(404, f"folder not found: {folder}")

    total = count_images(folder, recursive=body.recursive)
    samples: list[dict[str, Any]] = []
    with_meta = 0
    for path, rec in iter_image_records(folder, recursive=body.recursive):
        if len(samples) >= body.sample_size:
            break
        if rec is not None:
            with_meta += 1
        samples.append(
            {
                "filename": path.name,
                "has_metadata": rec is not None,
                "software": rec.software if rec else None,
                "nai_model": rec.nai_model if rec else None,
                "prompt": (rec.prompt[:400] if rec else ""),
            }
        )
    return {
        "path": str(folder),
        "total_images": total,
        "sampled": len(samples),
        "with_metadata": with_meta,
        "samples": samples,
    }


class FolderIngestIn(BaseModel):
    path: str
    label: Optional[str] = None
    recursive: bool = True
    drop_artist_tags: bool = True
    drop_quality_tags: bool = True
    drop_character_tags: bool = False
    classify_after: bool = False


@router.post("/images/start")
def start_folder(body: FolderIngestIn, bg: BackgroundTasks) -> dict[str, Any]:
    folder = Path(body.path).expanduser()
    if not folder.is_dir():
        raise HTTPException(404, f"folder not found: {folder}")

    label = body.label or f"images:{folder.name}"
    job_id = jobs.create_job(
        kind="ingest_images",
        label=label,
        detail={
            "path": str(folder),
            "recursive": body.recursive,
            "drop_artist_tags": body.drop_artist_tags,
            "drop_quality_tags": body.drop_quality_tags,
            "drop_character_tags": body.drop_character_tags,
        },
    )
    bg.add_task(
        run_image_folder_ingest,
        job_id=job_id,
        folder=folder,
        label=label,
        recursive=body.recursive,
        drop_artist_tags=body.drop_artist_tags,
        drop_quality_tags=body.drop_quality_tags,
        drop_character_tags=body.drop_character_tags,
        classify_after=body.classify_after,
    )
    return {"job_id": job_id}


@router.get("/metadata/defaults")
def defaults() -> dict[str, Any]:
    p = settings.DEFAULT_METADATA_FILE
    if p is None:
        return {"path": "", "exists": False, "size_bytes": 0}
    return {
        "path": str(p),
        "exists": p.exists(),
        "size_bytes": p.stat().st_size if p.exists() else 0,
    }

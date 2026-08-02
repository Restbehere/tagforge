"""Decompose tab: See-through anime layer decomposition.

Upload (or point at) an image, queue it through the external see-through
pipeline, then browse the per-layer PNG pieces and open the resulting PSD.
Everything runs locally; the heavy lifting happens in the see-through conda
env via backend.decompose's worker thread.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import decompose as svc
from ..db import get_session
from ..models import DecompItem

router = APIRouter()

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def _row(item: DecompItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "original_name": item.original_name,
        "input_path": item.input_path,
        "params": json.loads(item.params_json or "{}"),
        "status": item.status,
        "progress": item.progress,
        "message": item.message,
        "error": item.error,
        "has_psd": bool(item.psd_path),
        "has_depth_psd": bool(item.depth_psd_path),
        "created_at": item.created_at.isoformat() + "Z" if item.created_at else None,
        "started_at": item.started_at.isoformat() + "Z" if item.started_at else None,
        "finished_at": item.finished_at.isoformat() + "Z" if item.finished_at else None,
    }


def _get_item(session: Session, item_id: int) -> DecompItem:
    item = session.get(DecompItem, item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    return item


# ---------------------------------------------------------------------------
# Environment / config / repo status
# ---------------------------------------------------------------------------


@router.get("/status")
def status() -> dict[str, Any]:
    # Also (re)starts the worker so queued items survive a backend restart.
    svc.ensure_worker()
    return {"defaults": svc.DEFAULT_PARAMS, **svc.env_status()}


class ConfigBody(BaseModel):
    python_path: Optional[str] = None
    repo_dir: Optional[str] = None
    layerdiff_dir: Optional[str] = None
    depth_dir: Optional[str] = None


@router.get("/config")
def get_config() -> dict[str, str]:
    return svc.load_config()


@router.put("/config")
def put_config(body: ConfigBody) -> dict[str, str]:
    return svc.save_config(body.model_dump(exclude_none=True))


@router.get("/repo-status")
def repo_status(fetch: bool = Query(default=False)) -> dict[str, Any]:
    return svc.repo_status(fetch=fetch)


@router.post("/repo-update")
def repo_update() -> dict[str, Any]:
    return svc.repo_update()


# ---------------------------------------------------------------------------
# Upload + queue
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, str]:
    name = file.filename or "image.png"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"unsupported file type {ext or '(none)'}")
    # Keep the original stem (pipeline names outputs after it) but strip
    # anything path-like / exotic, and prefix a short uid against collisions.
    stem = re.sub(r"[^\w\- ]+", "_", Path(name).stem).strip() or "image"
    dest = svc.INPUTS_DIR / f"{uuid.uuid4().hex[:8]}_{stem}{ext}"
    svc.INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "file too large (64 MB max)")
            f.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "empty file")
    return {"path": str(dest), "name": name}


class QueueInput(BaseModel):
    path: str
    name: str = ""


class QueueBody(BaseModel):
    inputs: list[QueueInput]
    params: dict[str, Any] = {}


@router.post("/queue")
def queue(body: QueueBody) -> dict[str, Any]:
    if not body.inputs:
        raise HTTPException(400, "no inputs")
    items = []
    for inp in body.inputs:
        p = Path(inp.path)
        if not p.is_file():
            raise HTTPException(400, f"input not found: {inp.path}")
        if p.suffix.lower() not in ALLOWED_EXT:
            raise HTTPException(400, f"unsupported file type: {p.name}")
        items.append(svc.enqueue(str(p), inp.name or p.name, body.params))
    return {"queued": [_row(i) for i in items]}


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@router.get("/items")
def list_items(
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = session.exec(
        select(DecompItem).order_by(DecompItem.id.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()
    return {"items": [_row(r) for r in rows]}


@router.get("/items/{item_id}")
def get_item(
    item_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    item = _get_item(session, item_id)
    return {**_row(item), **svc.list_layers(item)}


@router.post("/items/{item_id}/cancel")
def cancel(item_id: int) -> dict[str, str]:
    try:
        return {"status": svc.cancel_item(item_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/items/{item_id}/requeue")
def requeue(
    item_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    item = _get_item(session, item_id)
    if item.status in {"queued", "running"}:
        raise HTTPException(400, "item is already queued or running")
    if not Path(item.input_path).is_file():
        raise HTTPException(400, "original input file no longer exists")
    new = svc.enqueue(
        item.input_path, item.original_name, json.loads(item.params_json or "{}")
    )
    return _row(new)


@router.delete("/items/{item_id}")
def delete_item(
    item_id: int, session: Session = Depends(get_session)
) -> dict[str, bool]:
    item = _get_item(session, item_id)
    if item.status in {"queued", "running"}:
        raise HTTPException(400, "cancel the item before deleting it")
    # Remove artifacts + the uploaded copy (never files outside our dirs).
    out_dir = svc.OUT_DIR / str(item.id)
    if out_dir.is_dir():
        import shutil

        shutil.rmtree(out_dir, ignore_errors=True)
    try:
        # resolve() first: a stored path containing '..' would otherwise
        # pass the parents check and unlink a file outside the inputs dir.
        inp = Path(item.input_path).resolve()
        if inp.is_file() and svc.INPUTS_DIR.resolve() in inp.parents:
            others = session.exec(
                select(DecompItem).where(
                    DecompItem.input_path == item.input_path,
                    DecompItem.id != item_id,
                )
            ).first()
            if others is None:
                inp.unlink(missing_ok=True)
    except OSError:
        pass
    session.delete(item)
    session.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Files: input preview, layer pieces, open-in-explorer
# ---------------------------------------------------------------------------


# Item ids are reused by SQLite after deletes, so the same URL can serve a
# different image later — force revalidation instead of heuristic caching.
_NO_CACHE = {"Cache-Control": "no-cache"}


@router.get("/items/{item_id}/input")
def input_image(
    item_id: int, session: Session = Depends(get_session)
) -> FileResponse:
    item = _get_item(session, item_id)
    p = Path(item.input_path)
    if not p.is_file():
        raise HTTPException(404, "input file missing")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, headers=_NO_CACHE)


@router.get("/items/{item_id}/psd")
def psd_file(
    item_id: int, session: Session = Depends(get_session)
) -> FileResponse:
    """The layered PSD itself — consumed by the Rig tab (Anime2.5DRig)."""
    item = _get_item(session, item_id)
    if not item.psd_path or not Path(item.psd_path).is_file():
        raise HTTPException(404, "this item has no PSD")
    p = Path(item.psd_path)
    return FileResponse(
        p,
        media_type="image/vnd.adobe.photoshop",
        headers=_NO_CACHE,
        filename=p.name,
    )


@router.get("/items/{item_id}/asset/{filename}")
def asset(
    item_id: int, filename: str, session: Session = Depends(get_session)
) -> FileResponse:
    item = _get_item(session, item_id)
    p = svc.resolve_asset(item, filename)
    if p is None:
        raise HTTPException(404, "no such asset")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(p, media_type=media, headers=_NO_CACHE)


class OpenBody(BaseModel):
    target: str = "psd"  # psd | depth | folder | input


@router.post("/items/{item_id}/open")
def open_item(
    item_id: int, body: OpenBody, session: Session = Depends(get_session)
) -> dict[str, bool]:
    item = _get_item(session, item_id)
    target: Optional[str]
    if body.target == "psd":
        target = item.psd_path
    elif body.target == "depth":
        target = item.depth_psd_path
    elif body.target == "folder":
        target = str(svc.OUT_DIR / str(item.id))
    elif body.target == "input":
        target = item.input_path
    else:
        raise HTTPException(400, "unknown target")
    if not target or not Path(target).exists():
        raise HTTPException(404, "target does not exist")
    if not hasattr(os, "startfile"):  # non-Windows: no AttributeError 500
        raise HTTPException(501, "opening a folder is only supported on Windows")
    try:
        os.startfile(target)  # noqa: S606 — local desktop tool, Windows only
    except OSError as exc:
        raise HTTPException(500, f"could not open: {exc}")
    return {"opened": True}

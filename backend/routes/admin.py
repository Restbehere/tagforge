"""Admin endpoints — WAL-safe SQLite backups of the live database.

Backups land in ``backend/data/backups/`` and are optionally mirrored to
extra directories (e.g. a second drive) configured via the generic Preset
store (kind='config', name='backup'). ``/backup/auto`` implements the
open-the-app weekly policy: back up when the newest snapshot is older than
seven days.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import db, settings
from ..models import Preset


router = APIRouter()

BACKUP_DIR = settings.DATA_DIR / "backups"
AUTO_BACKUP_AGE = timedelta(days=7)

# Strict filename pattern — re-validated everywhere a name is accepted so a
# crafted name can never escape BACKUP_DIR (no path traversal).
_BACKUP_NAME_RE = re.compile(r"^tagforge-\d{8}-\d{6}\.db$")


def _backup_dict(path) -> dict[str, Any]:
    st = path.stat()
    return {
        "name": path.name,
        "size_bytes": st.st_size,
        "created_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Mirror configuration (stored in the generic preset table)
# ---------------------------------------------------------------------------


def _load_mirror_dirs() -> list[str]:
    with db.session_scope() as s:
        row = s.exec(
            select(Preset).where(Preset.kind == "config", Preset.name == "backup")
        ).first()
        if row is None:
            return []
        try:
            data = json.loads(row.data_json or "{}")
        except Exception:
            return []
        dirs = data.get("mirror_dirs", [])
        return [str(d) for d in dirs if str(d).strip()]


def _save_mirror_dirs(dirs: list[str]) -> None:
    cleaned = [str(d).strip() for d in dirs if str(d).strip()]
    with db.session_scope() as s:
        row = s.exec(
            select(Preset).where(Preset.kind == "config", Preset.name == "backup")
        ).first()
        payload = json.dumps({"mirror_dirs": cleaned})
        if row is None:
            s.add(Preset(kind="config", name="backup", data_json=payload))
        else:
            row.data_json = payload
            row.updated_at = datetime.now(timezone.utc)
            s.add(row)


class BackupConfigBody(BaseModel):
    mirror_dirs: list[str]


@router.get("/backup-config")
def get_backup_config() -> dict[str, Any]:
    return {"mirror_dirs": _load_mirror_dirs(), "auto_days": AUTO_BACKUP_AGE.days}


@router.put("/backup-config")
def put_backup_config(body: BackupConfigBody) -> dict[str, Any]:
    _save_mirror_dirs(body.mirror_dirs)
    return {"mirror_dirs": _load_mirror_dirs(), "auto_days": AUTO_BACKUP_AGE.days}


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


def _newest_backup_at() -> datetime | None:
    if not BACKUP_DIR.exists():
        return None
    newest: float | None = None
    for p in BACKUP_DIR.glob("*.db"):
        if p.is_file() and _BACKUP_NAME_RE.match(p.name):
            mtime = p.stat().st_mtime
            if newest is None or mtime > newest:
                newest = mtime
    if newest is None:
        return None
    return datetime.fromtimestamp(newest, tz=timezone.utc)


def _do_backup() -> dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # One-second name resolution: bump forward instead of silently
    # overwriting an existing snapshot from the same second.
    stamp = datetime.now()
    while True:
        name = stamp.strftime("tagforge-%Y%m%d-%H%M%S.db")
        dest = BACKUP_DIR / name
        if not dest.exists():
            break
        stamp += timedelta(seconds=1)

    src = sqlite3.connect(str(settings.DB_PATH))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)  # WAL-safe live backup via the SQLite backup API
        finally:
            dst.close()
    finally:
        src.close()

    # Mirror the finished snapshot to configured extra locations. A dead
    # drive must not fail the primary backup — report per-mirror status.
    mirrors: list[dict[str, Any]] = []
    for raw_dir in _load_mirror_dirs():
        entry: dict[str, Any] = {"dir": raw_dir, "ok": False}
        try:
            mdir = Path(raw_dir).expanduser()
            mdir.mkdir(parents=True, exist_ok=True)
            tmp = mdir / (name + ".tmp")
            shutil.copyfile(dest, tmp)
            os.replace(tmp, mdir / name)
            entry["ok"] = True
        except Exception as exc:
            entry["error"] = str(exc)
        mirrors.append(entry)

    out = _backup_dict(dest)
    out["mirrors"] = mirrors
    return out


@router.post("/backup")
def run_backup() -> dict[str, Any]:
    return _do_backup()


@router.post("/backup/auto")
def auto_backup() -> dict[str, Any]:
    """Weekly-on-open policy: back up if the newest snapshot is > 7 days old.

    Called once by the frontend when the app loads; cheap no-op otherwise.
    """
    newest = _newest_backup_at()
    now = datetime.now(timezone.utc)
    if newest is not None and now - newest < AUTO_BACKUP_AGE:
        return {
            "backed_up": False,
            "last_backup_at": newest.isoformat(),
            "next_due_at": (newest + AUTO_BACKUP_AGE).isoformat(),
        }
    result = _do_backup()
    result["backed_up"] = True
    return result


@router.get("/backups")
def list_backups() -> list[dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    files = [
        p
        for p in BACKUP_DIR.glob("*.db")
        if p.is_file() and _BACKUP_NAME_RE.match(p.name)
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_backup_dict(p) for p in files]


@router.delete("/backups/{name}")
def delete_backup(name: str) -> dict[str, Any]:
    if not _BACKUP_NAME_RE.match(name):
        raise HTTPException(404, "backup not found")
    path = BACKUP_DIR / name
    if not path.is_file():
        raise HTTPException(404, "backup not found")
    path.unlink()
    return {"ok": True}

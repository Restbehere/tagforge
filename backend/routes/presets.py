"""Named presets — save, load, and reuse form snapshots across pages."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import db
from ..models import Preset


router = APIRouter()


def _preset_dict(p: Preset) -> dict[str, Any]:
    return {
        "id": p.id,
        "kind": p.kind,
        "name": p.name,
        "data": json.loads(p.data_json or "{}"),
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


class PresetBody(BaseModel):
    kind: str
    name: str
    data: dict[str, Any]


class PresetUpdateBody(BaseModel):
    name: Optional[str] = None
    data: dict[str, Any]


@router.get("")
def list_presets(kind: Optional[str] = None) -> list[dict[str, Any]]:
    with db.session_scope() as s:
        stmt = select(Preset)
        if kind:
            stmt = stmt.where(Preset.kind == kind)
        rows = list(s.exec(stmt.order_by(Preset.name)).all())
        return [_preset_dict(r) for r in rows]


@router.post("")
def create_preset(body: PresetBody) -> dict[str, Any]:
    with db.session_scope() as s:
        existing = s.exec(
            select(Preset).where(Preset.kind == body.kind, Preset.name == body.name)
        ).first()
        if existing:
            raise HTTPException(
                409, f"preset '{body.name}' already exists for kind '{body.kind}'"
            )
        p = Preset(kind=body.kind, name=body.name, data_json=json.dumps(body.data))
        s.add(p)
        s.flush()
        return _preset_dict(p)


@router.put("/{preset_id}")
def update_preset(preset_id: int, body: PresetUpdateBody) -> dict[str, Any]:
    with db.session_scope() as s:
        p = s.get(Preset, preset_id)
        if p is None:
            raise HTTPException(404, "preset not found")
        if body.name is not None and body.name != p.name:
            clash = s.exec(
                select(Preset).where(
                    Preset.kind == p.kind, Preset.name == body.name
                )
            ).first()
            if clash is not None:
                raise HTTPException(
                    409, f"preset '{body.name}' already exists for kind '{p.kind}'"
                )
            p.name = body.name
        p.data_json = json.dumps(body.data)
        p.updated_at = datetime.now(timezone.utc)
        s.add(p)
        return _preset_dict(p)


@router.delete("/{preset_id}")
def delete_preset(preset_id: int) -> dict[str, Any]:
    with db.session_scope() as s:
        p = s.get(Preset, preset_id)
        if p is None:
            raise HTTPException(404, "preset not found")
        s.delete(p)
        return {"ok": True}

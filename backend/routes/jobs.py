"""Job tracking + SSE progress stream."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from .. import jobs as jobs_mod


router = APIRouter()


@router.get("")
def list_recent() -> list[dict[str, Any]]:
    items = jobs_mod.list_jobs(limit=50)
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "label": j.label,
            "status": j.status,
            "progress": j.progress,
            "message": j.message,
            "error": j.error,
            "created_at": j.created_at.isoformat(),
            "updated_at": j.updated_at.isoformat(),
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        }
        for j in items
    ]


@router.get("/{job_id}")
def get_one(job_id: int) -> dict[str, Any]:
    job = jobs_mod.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {
        "id": job.id,
        "kind": job.kind,
        "label": job.label,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "detail": json.loads(job.detail_json) if job.detail_json else None,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("/{job_id}/stream")
async def stream(job_id: int) -> EventSourceResponse:
    async def event_gen():
        async for payload in jobs_mod.subscribe(job_id):
            yield {"event": "job", "data": json.dumps(payload)}

    return EventSourceResponse(event_gen())


@router.get("/stream/all")
async def stream_all() -> EventSourceResponse:
    async def event_gen():
        async for payload in jobs_mod.subscribe(None):
            yield {"event": "job", "data": json.dumps(payload)}

    return EventSourceResponse(event_gen())

"""Background-job tracking + an in-process publish/subscribe bus for SSE.

We deliberately keep this very small: jobs run as ``asyncio.Task``s or threads
spawned from FastAPI routes, and they call :func:`update_job` to push progress.
A simple async queue per subscriber is used to fan out events to clients.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, AsyncIterator

from sqlmodel import select

from . import db
from .models import Job


# job_id -> set of subscriber queues
_subscribers: dict[int, set[asyncio.Queue]] = {}
_global_subscribers: set[asyncio.Queue] = set()
_lock = asyncio.Lock()


def create_job(kind: str, label: str, detail: dict[str, Any] | None = None) -> int:
    """Insert a new job row and return its id."""
    with db.session_scope() as s:
        job = Job(
            kind=kind,
            label=label,
            detail_json=json.dumps(detail) if detail else None,
        )
        s.add(job)
        s.flush()
        return job.id  # type: ignore[return-value]


def get_job(job_id: int) -> Job | None:
    with db.session_scope() as s:
        job = s.get(Job, job_id)
        if job is not None:
            s.refresh(job)
            s.expunge(job)
        return job


def list_jobs(limit: int = 50) -> list[Job]:
    with db.session_scope() as s:
        items = list(
            s.exec(select(Job).order_by(Job.created_at.desc()).limit(limit)).all()
        )
        for it in items:
            s.refresh(it)
            s.expunge(it)
        return items


# Booru credentials travel as query params, so any error carrying the
# request URL (httpx puts the full URL in HTTPStatusError) would persist
# them into job.error and echo them over the jobs API and SSE stream.
_SECRET_QS_RE = re.compile(
    r"((?:api_key|login|password|token)=)[^&\s'\"]+", re.IGNORECASE
)


def redact_secrets(text: str) -> str:
    return _SECRET_QS_RE.sub(r"\1***", text)


def update_job(
    job_id: int,
    *,
    status: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
    finished: bool = False,
) -> None:
    """Update job columns and notify subscribers."""
    with db.session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            job.message = redact_secrets(message)
        if error is not None:
            job.error = redact_secrets(error)
            job.status = "error"
        if detail is not None:
            job.detail_json = json.dumps(detail)
        if finished:
            job.finished_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        s.add(job)
        payload = _job_payload(job)

    _notify(job_id, payload)


# ---- pub/sub event loop bridge ----------------------------------------------

# Set by the FastAPI lifespan handler so threads spawned by BackgroundTasks
# can publish back into the server's loop.
_main_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the running asyncio loop for cross-thread notifications."""
    global _main_loop
    _main_loop = loop


def _notify(job_id: int, payload: dict[str, Any]) -> None:
    """Push a job event into the asyncio loop (no-op outside a server)."""
    loop = _main_loop
    if loop is None:
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if not loop.is_running():
                loop = None
        except RuntimeError:
            loop = None
    if loop is None or not loop.is_running():
        return
    loop.call_soon_threadsafe(_dispatch_sync, job_id, payload)


def _job_payload(job: Job) -> dict[str, Any]:
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


def _dispatch_sync(job_id: int, payload: dict[str, Any]) -> None:
    loop = asyncio.get_event_loop()
    loop.create_task(_dispatch(job_id, payload))


async def _dispatch(job_id: int, payload: dict[str, Any]) -> None:
    async with _lock:
        subs = list(_subscribers.get(job_id, set())) + list(_global_subscribers)
    for q in subs:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the OLDEST event, not this one. A chatty job could fill a
            # stalled subscriber's queue and then lose its terminal
            # done/error payload, leaving that stream open and the UI showing
            # the job as running forever. Newest always wins.
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


async def subscribe(job_id: int | None = None) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding job events. ``None`` subscribes to all jobs."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    async with _lock:
        if job_id is None:
            _global_subscribers.add(queue)
        else:
            _subscribers.setdefault(job_id, set()).add(queue)

    try:
        # Send the current snapshot first if subscribed to a specific job
        if job_id is not None:
            existing = get_job(job_id)
            if existing is None:
                # Unknown id (DB reset, pruned rows): emit a terminal payload
                # so the client can settle instead of spinning forever.
                yield {
                    "id": job_id,
                    "kind": "unknown",
                    "label": f"job #{job_id}",
                    "status": "error",
                    "progress": 0.0,
                    "message": "",
                    "error": "job not found — it may have been cleared",
                    "detail": None,
                }
                return
            yield _job_payload(existing)
            if existing.status in {"done", "error", "cancelled"}:
                # Already terminal — nothing more will ever arrive; close so
                # reconnecting clients don't hold idle streams open forever.
                return

        while True:
            payload = await queue.get()
            yield payload
            if payload.get("status") in {"done", "error", "cancelled"}:
                # Keep the connection a moment so the client receives the final event
                await asyncio.sleep(0.1)
                if job_id is not None:
                    break
    finally:
        async with _lock:
            if job_id is None:
                _global_subscribers.discard(queue)
            else:
                subs = _subscribers.get(job_id)
                if subs is not None:
                    subs.discard(queue)
                    if not subs:
                        _subscribers.pop(job_id, None)

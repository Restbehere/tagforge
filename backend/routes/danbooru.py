"""Danbooru / AIBooru fetching endpoints."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from .. import jobs
from ..ingest.runner import run_booru_fetch


router = APIRouter()


class BooruFetchIn(BaseModel):
    site: str = "danbooru"  # 'danbooru' | 'aibooru'
    mode: str = "popular"  # 'popular' | 'rank' | 'score' | 'tag_search' | 'trending'
    date: Optional[str] = None  # for popular: yyyy-mm-dd
    scale: str = "day"  # 'day' | 'week' | 'month'
    date_min: Optional[str] = None
    date_max: Optional[str] = None
    rating: Optional[str] = None  # comma-separated 'g,s' etc.
    score_min: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    limit: int = 200
    pages: int = 1
    login: Optional[str] = None
    api_key: Optional[str] = None
    label: Optional[str] = None
    # for trending mode
    recent_days: int = 7
    baseline_days: int = 30
    # Chain Stage-3 GPT classification of new tags + delta scene rebuild
    # onto the same job after the fetch finishes.
    classify_after: bool = False


@router.post("/fetch")
def fetch(body: BooruFetchIn, bg: BackgroundTasks) -> dict[str, Any]:
    label = body.label or f"{body.site}:{body.mode}"
    params = body.model_dump()
    job_id = jobs.create_job(
        kind="fetch_booru",
        label=label,
        # Credentials must not be persisted: detail_json is echoed back by
        # GET /api/jobs/{id} and the SSE stream, and lands in DB backups.
        detail={k: v for k, v in params.items() if k not in ("login", "api_key")},
    )
    bg.add_task(run_booru_fetch, job_id=job_id, params=params)
    return {"job_id": job_id}


@router.get("/estimate-tag-budget")
def estimate_tag_budget(tags: str = "") -> dict[str, Any]:
    """Tell the UI whether the query will exceed the anon 2-paid-tag limit."""
    from ..ingest.danbooru_client import classify_tag_budget

    return classify_tag_budget(tags)

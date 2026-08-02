"""Local LLM endpoints: llama-swap management + the NAI prompt splitter."""

from __future__ import annotations

from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import llm as svc

router = APIRouter()


@router.get("/status")
def status() -> dict[str, Any]:
    return svc.server_status()


@router.post("/start")
def start() -> dict[str, Any]:
    return svc.start_server()


@router.post("/unload")
def unload() -> dict[str, Any]:
    res = svc.unload_models()
    if not res.get("ok"):
        raise HTTPException(502, res.get("error") or "unload failed")
    return res


class NaiSplitIn(BaseModel):
    tags: str = Field(min_length=1)
    mode: str = "split"  # split | natural
    model: Optional[str] = None
    include_speech: bool = False
    strip_identity: bool = False
    invent_background: bool = False
    enrich_background: bool = False
    # Speech-only knobs; ignored when include_speech is False.
    bubble: Literal["auto", "on", "off"] = "auto"
    text_position: Literal["attributed", "placed", "free"] = "attributed"


def _call_llm(fn, *args) -> dict[str, Any]:
    """Run an LLM service call with httpx/output errors mapped to HTTP."""
    try:
        return fn(*args)
    except httpx.ConnectError:
        raise HTTPException(
            503, "llama-swap is not running — start the LLM server first"
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"LLM backend error: {exc.response.status_code} {exc.response.text[:300]}"
        )
    except httpx.TimeoutException:
        raise HTTPException(504, "LLM request timed out")
    except httpx.HTTPError as exc:
        # ReadError / RemoteProtocolError etc. — connection died mid-request
        raise HTTPException(502, f"LLM connection failed mid-request: {exc}")
    except svc.LlmOutputError as exc:
        raise HTTPException(502, str(exc))


@router.post("/nai-split")
def nai_split(body: NaiSplitIn) -> dict[str, Any]:
    if body.mode not in ("split", "natural"):
        raise HTTPException(400, "mode must be 'split' or 'natural'")
    if not body.tags.strip():
        raise HTTPException(400, "tags is empty")
    return _call_llm(
        svc.nai_split,
        body.tags.strip(),
        body.mode,
        body.model,
        body.include_speech,
        body.strip_identity,
        body.invent_background,
        body.enrich_background,
        body.bubble,
        body.text_position,
    )


class TtlIn(BaseModel):
    minutes: int = Field(ge=0, le=24 * 60)


@router.post("/ttl")
def set_ttl(body: TtlIn) -> dict[str, Any]:
    res = svc.set_ttl_minutes(body.minutes)
    if not res.get("ok"):
        raise HTTPException(502, res.get("error", "could not update ttl"))
    return res


class NaiComposeIn(BaseModel):
    idea: str = Field(min_length=3)
    model: Optional[str] = None


@router.post("/nai-compose")
def nai_compose(body: NaiComposeIn) -> dict[str, Any]:
    if not body.idea.strip():
        raise HTTPException(400, "idea is empty")
    return _call_llm(svc.nai_compose, body.idea.strip(), body.model)

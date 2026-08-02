"""Local LLM endpoints: llama-swap management + the NAI prompt splitter."""

from __future__ import annotations

from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import llm as svc
from .. import llm_config

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
    except llm_config.LlmConfigError as exc:
        # Misconfiguration, not a backend failure — 400 so the UI shows the
        # message as-is instead of "LLM backend error".
        raise HTTPException(400, str(exc))


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


class LlmFeatureConfigIn(BaseModel):
    kind: Optional[Literal["openai", "openai_compatible", "anthropic", "local", "echo"]] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=12)
    send_temperature: Optional[bool] = None
    # Write-only: stored in api_credential and never read back.
    api_key: Optional[str] = None


class LlmConfigIn(BaseModel):
    stage3: Optional[LlmFeatureConfigIn] = None
    splitter: Optional[LlmFeatureConfigIn] = None


def _config_payload() -> dict[str, Any]:
    return {
        "config": llm_config.load_config(),
        "key_hints": llm_config.key_hints(),
        "suggested_models": llm_config.SUGGESTED_MODELS,
        "kinds": list(llm_config.KINDS),
        # Lets the form prefill a model when the provider changes, so a
        # remote endpoint is never saved without one.
        "default_models": llm_config.DEFAULT_MODEL_BY_KIND,
    }


@router.get("/config")
def get_llm_config() -> dict[str, Any]:
    """Endpoint settings plus masked key hints — never the keys themselves."""
    return _config_payload()


@router.put("/config")
def put_llm_config(body: LlmConfigIn) -> dict[str, Any]:
    from .. import llm_config

    patch: dict[str, Any] = {}
    for feature in ("stage3", "splitter"):
        got: Optional[LlmFeatureConfigIn] = getattr(body, feature)
        if got is None:
            continue
        fields = got.model_dump(exclude_none=True)
        # The key goes to its own table, never into the config row.
        key = fields.pop("api_key", None)
        if key is not None:
            llm_config.set_api_key(feature, key)
        if fields:
            patch[feature] = fields
    if patch:
        llm_config.save_config(patch)
    return _config_payload()


class LlmTestIn(BaseModel):
    feature: Literal["stage3", "splitter"] = "stage3"


@router.post("/config/test")
def test_llm_config(body: LlmTestIn) -> dict[str, Any]:
    """Round-trip one tiny request so misconfiguration surfaces here rather
    than halfway through a long classification run."""
    from .. import llm_config

    target = llm_config.get_target(body.feature)
    try:
        if target.kind == "echo":
            return {"ok": True, "detail": "echo provider — no network call"}
        if target.is_local:
            st = svc.server_status()
            if not st.get("up"):
                return {"ok": False, "detail": "local llama-swap server is not reachable"}
            return {"ok": True, "detail": f"llama-swap up — {len(st.get('models', []))} model(s)"}
        if target.kind == "anthropic":
            return {"ok": False, "detail": "no test implemented for Anthropic yet"}

        probe = [{"role": "user", "content": "Reply with the single word: ok"}]
        if body.feature == "splitter":
            # The splitter speaks plain HTTP, never the OpenAI SDK — testing
            # it through the SDK would demand the [llm] extras it does not use.
            url, headers, model, _local = svc._chat_target(None)
            with httpx.Client(timeout=30.0) as c:
                r = c.post(
                    url,
                    headers=headers,
                    json={"model": model, "messages": probe, "max_tokens": 8},
                )
            if r.status_code >= 400:
                return {"ok": False, "detail": f"HTTP {r.status_code}: {r.text[:200]}"}
            got = (r.json()["choices"][0]["message"]["content"] or "").strip()
            return {"ok": True, "detail": f"{model} replied: {got[:60]!r}"}

        from ..ingest.stage3_llm import _get_openai_client

        client = _get_openai_client(
            base_url=target.base_url, api_key=target.api_key(), timeout=30.0
        )
        resp = client.chat.completions.create(
            model=target.require_model(), messages=probe, max_tokens=8
        )
        got = (resp.choices[0].message.content or "").strip()
        return {"ok": True, "detail": f"{target.describe()} replied: {got[:60]!r}"}
    except llm_config.LlmConfigError as exc:
        # A settings problem, not a provider failure — say so plainly.
        return {"ok": False, "detail": str(exc)}
    except Exception as exc:
        # Never surface a key that a provider echoed back in an error URL.
        from ..jobs import redact_secrets

        return {"ok": False, "detail": redact_secrets(f"{type(exc).__name__}: {exc}")[:400]}


class NaiComposeIn(BaseModel):
    idea: str = Field(min_length=3)
    model: Optional[str] = None


@router.post("/nai-compose")
def nai_compose(body: NaiComposeIn) -> dict[str, Any]:
    if not body.idea.strip():
        raise HTTPException(400, "idea is empty")
    return _call_llm(svc.nai_compose, body.idea.strip(), body.model)

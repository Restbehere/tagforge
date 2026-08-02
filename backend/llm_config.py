"""Where each LLM feature sends its requests.

Two features call models independently:

* **stage3**   — tag classification (batches of residual `other` tags)
* **splitter** — the NAI prompt splitter / compose

Each can point at OpenAI, Anthropic, the local llama-swap server, or ANY
OpenAI-compatible endpoint (OpenRouter, Together, Groq, a self-hosted
server...). Routing them separately is the point: this corpus is explicit
anime tags, and mainstream providers may refuse or silently soften them,
so users need to send classification to an open-weights model without
giving up a local splitter — or vice versa.

Non-secret settings live in ``Preset(kind='config', name='llm')``,
mirroring how the Decompose tab stores its paths. API keys do NOT: they
live in :class:`ApiCredential`, because ``GET /api/presets`` returns
every preset's ``data_json`` and would hand the key straight back.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from sqlmodel import select

from . import db, settings
from .models import ApiCredential, Preset


CONFIG_KIND = "config"
CONFIG_NAME = "llm"
CRED_SCOPE = "llm_provider"

# 'openai' and 'openai_compatible' share a request shape; they differ only
# in whether provider-only features (the Batch API) are offered.
KINDS = ("openai", "openai_compatible", "anthropic", "local", "echo")

# gpt-4.1-mini over gpt-4o-mini: same cheap/fast tier, better at holding the
# "return a JSON map" instruction over 50 tags, and unlike the o-series it
# still accepts a plain `temperature`, so the existing request shape works
# untouched.
DEFAULT_OPENAI_STAGE3_MODEL = "gpt-4.1-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"

# Open-weights endpoints worth suggesting, surfaced in the UI as a datalist.
# Slugs drift, so the UI tells users to confirm against the provider.
SUGGESTED_MODELS = {
    "stage3": [
        "deepseek/deepseek-chat-v3",
        "qwen/qwen3-235b-a22b-instruct",
        "mistralai/mistral-small-3.2-24b-instruct",
        "nousresearch/hermes-3-llama-3.1-70b",
    ],
    "splitter": [
        "qwen/qwen3-235b-a22b-instruct",
        "nousresearch/hermes-3-llama-3.1-70b",
        "deepseek/deepseek-chat-v3",
    ],
}

DEFAULTS: dict[str, Any] = {
    "stage3": {
        "kind": "openai",
        "base_url": "",  # blank = the kind's own default endpoint
        "model": DEFAULT_OPENAI_STAGE3_MODEL,
        "max_concurrency": 6,
        "send_temperature": True,
    },
    "splitter": {
        "kind": "local",
        "base_url": "",
        "model": "",  # blank = llm.DEFAULT_MODEL
        "max_concurrency": 1,
        "send_temperature": True,
    },
}

_FEATURES = tuple(DEFAULTS)


def _row(s, create: bool = False) -> Optional[Preset]:
    row = s.exec(
        select(Preset).where(Preset.kind == CONFIG_KIND, Preset.name == CONFIG_NAME)
    ).first()
    if row is None and create:
        row = Preset(kind=CONFIG_KIND, name=CONFIG_NAME, data_json="{}")
        s.add(row)
        s.flush()
    return row


def load_config() -> dict[str, Any]:
    """Stored settings merged over the defaults (never includes keys)."""
    stored: dict[str, Any] = {}
    try:
        with db.session_scope() as s:
            row = _row(s)
            if row is not None:
                stored = json.loads(row.data_json or "{}")
    except Exception:
        stored = {}

    cfg: dict[str, Any] = {}
    for feature, base in DEFAULTS.items():
        merged = dict(base)
        got = stored.get(feature)
        if isinstance(got, dict):
            for k, v in got.items():
                if k in base:
                    merged[k] = v
        if merged.get("kind") not in KINDS:
            merged["kind"] = base["kind"]
        cfg[feature] = merged
    return cfg


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial update. Unknown features/fields are ignored."""
    current = load_config()
    for feature in _FEATURES:
        got = patch.get(feature)
        if isinstance(got, dict):
            for k, v in got.items():
                if k in DEFAULTS[feature]:
                    current[feature][k] = v
        if current[feature].get("kind") not in KINDS:
            current[feature]["kind"] = DEFAULTS[feature]["kind"]
        try:
            current[feature]["max_concurrency"] = max(
                1, min(12, int(current[feature]["max_concurrency"]))
            )
        except (TypeError, ValueError):
            current[feature]["max_concurrency"] = DEFAULTS[feature]["max_concurrency"]

    with db.session_scope() as s:
        row = _row(s, create=True)
        row.data_json = json.dumps(current, indent=2, sort_keys=True)
        s.add(row)
    return current


# ---------------------------------------------------------------------------
# Credentials — stored apart from the config, never returned by a route
# ---------------------------------------------------------------------------


def set_api_key(feature: str, key: str) -> str:
    """Store (or clear, when key is blank) a feature's API key. Returns the hint."""
    key = (key or "").strip()
    hint = f"…{key[-4:]}" if len(key) >= 4 else ("set" if key else "")
    with db.session_scope() as s:
        row = s.exec(
            select(ApiCredential).where(
                ApiCredential.scope == CRED_SCOPE, ApiCredential.name == feature
            )
        ).first()
        if not key:
            if row is not None:
                s.delete(row)
            return ""
        if row is None:
            row = ApiCredential(scope=CRED_SCOPE, name=feature)
        row.value, row.hint = key, hint
        s.add(row)
    return hint


def get_api_key(feature: str) -> str:
    """The stored key, else the environment fallback for that feature's kind.

    Env vars keep working untouched, so an existing OPENAI_API_KEY setup
    needs no migration.
    """
    with db.session_scope() as s:
        row = s.exec(
            select(ApiCredential).where(
                ApiCredential.scope == CRED_SCOPE, ApiCredential.name == feature
            )
        ).first()
        if row is not None and row.value:
            return row.value

    kind = load_config().get(feature, {}).get("kind", "")
    if kind == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "")
    return os.environ.get("OPENAI_API_KEY", "")


def key_hints() -> dict[str, str]:
    """Masked hints only — safe to return over the API."""
    hints = {f: "" for f in _FEATURES}
    try:
        with db.session_scope() as s:
            for row in s.exec(
                select(ApiCredential).where(ApiCredential.scope == CRED_SCOPE)
            ).all():
                if row.name in hints:
                    hints[row.name] = row.hint
    except Exception:
        pass
    for feature in _FEATURES:
        if hints[feature]:
            continue
        kind = load_config().get(feature, {}).get("kind", "")
        env = "ANTHROPIC_API_KEY" if kind == "anthropic" else "OPENAI_API_KEY"
        if os.environ.get(env):
            hints[feature] = f"from {env}"
    return hints


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class Target:
    """A fully-resolved endpoint for one feature."""

    def __init__(self, feature: str, cfg: dict[str, Any]) -> None:
        self.feature = feature
        self.kind: str = cfg["kind"]
        self.model: str = (cfg.get("model") or "").strip()
        self.max_concurrency: int = int(cfg.get("max_concurrency") or 1)
        self.send_temperature: bool = bool(cfg.get("send_temperature", True))
        base = (cfg.get("base_url") or "").strip().rstrip("/")
        if self.kind == "local" and not base:
            base = settings.LLAMA_SWAP_URL.rstrip("/")
        elif self.kind == "openai" and not base:
            base = "https://api.openai.com/v1"
        self.base_url: str = base

    @property
    def is_local(self) -> bool:
        """True when this points at the bundled llama-swap server.

        Its control plane (start/unload/TTL, /running) only exists there.
        """
        return self.kind == "local"

    @property
    def uses_openai_sdk(self) -> bool:
        return self.kind in ("openai", "openai_compatible")

    @property
    def supports_batch_api(self) -> bool:
        """OpenAI's 50%-cheaper Batch API — no third-party gateway has it."""
        return self.kind == "openai"

    def api_key(self) -> str:
        return get_api_key(self.feature)

    def describe(self) -> str:
        return f"{self.kind}:{self.model or 'default'}"


def get_target(feature: str) -> Target:
    if feature not in _FEATURES:
        raise ValueError(f"unknown LLM feature: {feature}")
    return Target(feature, load_config()[feature])

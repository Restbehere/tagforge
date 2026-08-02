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

# What to prefill when the user switches provider. Only the local server can
# pick a model on its own, so every remote kind needs a concrete name here —
# see Target.require_model for why a blank one cannot be defaulted later.
DEFAULT_MODEL_BY_KIND = {
    "openai": DEFAULT_OPENAI_STAGE3_MODEL,
    "anthropic": DEFAULT_ANTHROPIC_MODEL,
    "openai_compatible": "",  # gateway-specific; the datalist suggests some
    "local": "",
    "echo": "",
}

FEATURE_LABELS = {"stage3": "tag classification", "splitter": "the prompt splitter"}

# Which environment variable legitimately belongs to which provider.
# 'openai_compatible' deliberately has NO entry: an OpenAI key is not a
# credential for an arbitrary gateway, and falling back to it would put the
# user's OPENAI_API_KEY in an Authorization header addressed to a
# third-party host they chose specifically to avoid OpenAI.
ENV_KEY_BY_KIND = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

# The splitter speaks the OpenAI chat-completions wire format directly, so
# Anthropic's Messages API cannot drive it, and 'echo' has no meaning there.
# Offering them in its dropdown only produced a silent misroute to the local
# server, so each feature advertises what it can actually dispatch.
SUPPORTED_KINDS = {
    "stage3": KINDS,
    "splitter": ("openai", "openai_compatible", "local"),
}


class LlmConfigError(RuntimeError):
    """The configured endpoint cannot be used as-is (e.g. no model name).

    A user-facing message: routes surface it verbatim rather than as a
    provider error, because the fix is in Settings, not the network.
    """

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
        # Only a gateway carries its own endpoint. Keeping one after a switch
        # away from 'openai_compatible' left an invisible value steering the
        # request — the field is not even rendered for other kinds — so an
        # OpenAI-labelled target went on talking to the old gateway, with the
        # OpenAI key attached.
        if current[feature]["kind"] != "openai_compatible":
            current[feature]["base_url"] = ""
        base = (current[feature].get("base_url") or "").strip()
        if base and not base.lower().startswith(("http://", "https://")):
            raise LlmConfigError(
                f"Base URL for {FEATURE_LABELS.get(feature, feature)} must start "
                f"with http:// or https:// — got {base!r}."
            )
        current[feature]["base_url"] = base
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


def _cred_name(feature: str, kind: str = "") -> str:
    """Credential rows are scoped to (feature, provider).

    A key is issued by one provider and is meaningless — and dangerous — at
    another's endpoint. Storing it per-feature meant switching Provider
    silently re-sent the previous provider's key to the new host, which is
    the same crossing the environment-variable fallback used to allow.
    """
    kind = kind or load_config().get(feature, {}).get("kind", "")
    return f"{feature}:{kind}"


def _find_cred(s, feature: str, kind: str = ""):
    name = _cred_name(feature, kind)
    row = s.exec(
        select(ApiCredential).where(
            ApiCredential.scope == CRED_SCOPE, ApiCredential.name == name
        )
    ).first()
    if row is not None:
        return row
    # One-time upgrade of a pre-0.9 row, which was scoped to the feature
    # alone. It was entered for whatever provider is configured now, so
    # that is the only provider it can safely be attributed to.
    legacy = s.exec(
        select(ApiCredential).where(
            ApiCredential.scope == CRED_SCOPE, ApiCredential.name == feature
        )
    ).first()
    if legacy is not None:
        legacy.name = name
        s.add(legacy)
    return legacy


def set_api_key(feature: str, key: str) -> str:
    """Store (or clear, when key is blank) the key for this feature's current
    provider. Returns the hint."""
    key = (key or "").strip()
    hint = f"…{key[-4:]}" if len(key) >= 4 else ("set" if key else "")
    with db.session_scope() as s:
        row = _find_cred(s, feature)
        if not key:
            if row is not None:
                s.delete(row)
            return ""
        if row is None:
            row = ApiCredential(scope=CRED_SCOPE, name=_cred_name(feature))
        row.value, row.hint = key, hint
        s.add(row)
    return hint


def get_api_key(feature: str) -> str:
    """The stored key, else the environment fallback for that feature's kind.

    Env vars keep working untouched, so an existing OPENAI_API_KEY setup
    needs no migration.
    """
    with db.session_scope() as s:
        row = _find_cred(s, feature)
        if row is not None and row.value:
            return row.value

    kind = load_config().get(feature, {}).get("kind", "")
    env = ENV_KEY_BY_KIND.get(kind)
    return os.environ.get(env, "") if env else ""


def key_hints() -> dict[str, str]:
    """Masked hints only — safe to return over the API."""
    hints = {f: "" for f in _FEATURES}
    try:
        with db.session_scope() as s:
            for feature in _FEATURES:
                row = _find_cred(s, feature)
                if row is not None:
                    hints[feature] = row.hint
    except Exception:
        pass
    for feature in _FEATURES:
        if hints[feature]:
            continue
        kind = load_config().get(feature, {}).get("kind", "")
        env = ENV_KEY_BY_KIND.get(kind)
        if env and os.environ.get(env):
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
        """OpenAI, any OpenAI-compatible gateway, and llama-swap itself all
        speak the same wire format, so one SDK client drives all three.

        llama-swap belongs here: Stage 3 previously dispatched on the kind
        name and had no 'local' handler, so selecting it — the most private
        option, and the whole point of configurable endpoints — failed with
        "unknown LLM provider: local".
        """
        return self.kind in ("openai", "openai_compatible", "local")

    @property
    def supports_batch_api(self) -> bool:
        """OpenAI's 50%-cheaper Batch API — no third-party gateway has it."""
        return self.kind == "openai"

    def api_key(self) -> str:
        return get_api_key(self.feature)

    def sdk_base_url(self, local_default: str = "") -> str:
        """Base URL for the OpenAI SDK, which appends ``/chat/completions``.

        Gateways publish their URL with the version already on it
        ("https://openrouter.ai/api/v1"); llama-swap is configured as a bare
        host, so it needs ``/v1`` added.
        """
        base = self.require_base_url(local_default).rstrip("/")
        return base if base.endswith("/v1") else f"{base}/v1"

    def sdk_api_key(self) -> str:
        """llama-swap ignores auth, but the SDK refuses to build without a
        non-empty key — so give the local server a placeholder rather than
        letting a real key be resolved for it."""
        if self.is_local:
            return "not-needed"
        return self.api_key()

    def require_model(self, local_default: str = "") -> str:
        """The model name to send, or a clear error when there is none.

        A blank model means "let the endpoint choose", which only the local
        llama-swap server can do. Falling back to the local model name on a
        remote provider is worse than failing: it produced a bewildering
        404 from OpenAI naming a local model the user had never selected.
        """
        if self.model:
            return self.model
        if self.is_local and local_default:
            return local_default
        raise LlmConfigError(
            f"No model set for {FEATURE_LABELS.get(self.feature, self.feature)}. "
            f"'{self.kind}' has no default to fall back on — set a model name "
            "under Settings → LLM providers."
        )

    def require_base_url(self, local_default: str = "") -> str:
        """The endpoint to call, or a clear error when there is none.

        Only 'local' and 'openai' have an endpoint of their own. A blank
        URL used to fall through to whatever the caller's default was —
        the local server for the splitter, api.openai.com for Stage 3 —
        so picking a gateway and forgetting its URL silently sent the
        request somewhere the user had not chosen. For Stage 3 that meant
        OpenAI, inverting the reason for selecting a gateway at all.
        """
        if self.base_url:
            return self.base_url
        if self.is_local and local_default:
            return local_default
        raise LlmConfigError(
            f"No base URL set for {FEATURE_LABELS.get(self.feature, self.feature)}. "
            f"'{self.kind}' has no endpoint of its own — paste the gateway's URL "
            "(e.g. https://openrouter.ai/api/v1) under Settings → LLM providers."
        )

    def require_supported(self) -> None:
        """Reject a provider this feature cannot actually dispatch."""
        allowed = SUPPORTED_KINDS.get(self.feature, KINDS)
        if self.kind not in allowed:
            raise LlmConfigError(
                f"{FEATURE_LABELS.get(self.feature, self.feature).capitalize()} "
                f"cannot use '{self.kind}' — it speaks the OpenAI "
                f"chat-completions API. Choose one of: {', '.join(allowed)}."
            )

    def describe(self) -> str:
        return f"{self.kind}:{self.model or 'default'}"


def get_target(feature: str) -> Target:
    if feature not in _FEATURES:
        raise ValueError(f"unknown LLM feature: {feature}")
    return Target(feature, load_config()[feature])

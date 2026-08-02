"""Runtime settings for the Tag Forge backend.

All paths are absolute and resolved relative to the project root.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path


logger = logging.getLogger(__name__)


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent


def _read_version() -> str:
    """Project version, read from backend/pyproject.toml.

    Keeping one declaration per side (pyproject for Python,
    frontend/package.json for the UI) avoids the usual drift where a
    hardcoded string in app.py silently reports a stale release.
    """
    try:
        text = (BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


VERSION = _read_version()


def _load_dotenv(path: Path) -> int:
    """Tiny ``.env`` parser. Loads ``KEY=value`` pairs into ``os.environ``
    without overwriting variables that are already set (so a real OS
    environment variable still wins).

    Returns the number of new keys loaded. Avoids the ``python-dotenv``
    dependency since our needs are trivial: comments (``#``), blank
    lines, optional single/double-quoted values, no expansion.
    """
    if not path.exists():
        return 0
    text: str | None = None
    # Windows users may create .env via PowerShell which defaults to UTF-16
    # with a BOM. Try common encodings in order before giving up.
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("could not read %s: %s", path, exc)
            return 0
    if text is None:
        logger.warning("could not decode %s with any common encoding", path)
        return 0

    loaded = 0
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")  # strip stray BOM mid-stream
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value
        loaded += 1
    if loaded:
        logger.info("loaded %d env vars from %s", loaded, path)
    return loaded


# Load .env BEFORE we read any os.environ defaults below so the file values
# can supply things like OPENAI_API_KEY / ANTHROPIC_API_KEY / port overrides.
_load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    """Read ``TAGFORGE_<name>``, falling back to the pre-rename
    ``PROMPTFINDER_<name>`` so existing .env files keep working."""
    # An empty value counts as unset. A bare ``TAGFORGE_SEETHROUGH_DIR=`` in
    # .env otherwise resolved that path to Path("."), pointing directory
    # settings at the repo root — where Decompose's "Update" would have run
    # git pull against Tag Forge's own checkout.
    return (
        os.environ.get(f"TAGFORGE_{name}")
        or os.environ.get(f"PROMPTFINDER_{name}")
        or default
    )


DATA_DIR = BACKEND_DIR / "data"
# The app was renamed from PromptFinder. An existing corpus keeps its old
# filename so it is used in place instead of being stranded behind the new
# default; fresh installs create tagforge.db.
_LEGACY_DB = DATA_DIR / "promptfinder.db"
_db_env = _env("DB_PATH")
DB_PATH = Path(
    _db_env
    or (_LEGACY_DB if _LEGACY_DB.exists() else DATA_DIR / "tagforge.db")
)
TAG_TREE_PATH = DATA_DIR / "tag_tree.json"
SEED_LABELS_PATH = DATA_DIR / "seed_labels.json"
CLASSIFICATION_CACHE_PATH = DATA_DIR / "tag_classification_cache.json"

# Optional: a 194k-row Danbooru ``tags.jsonl`` database, used as an
# extra Stage-1 category signal. Falls back gracefully if absent.
TAGS_JSONL_PATH = Path(
    _env(
        "TAGS_JSONL_PATH",
        WORKSPACE_ROOT / "danbooru-tags" / "tags.jsonl",
    )
)

# Optional one-click export destinations, surfaced as pills on the
# Export page. Point them at wherever your generation front-end reads
# wildcards from. The Export endpoint can override them per-call.
WILDCARDS_DIR = Path(
    _env(
        "WILDCARDS_DIR",
        WORKSPACE_ROOT / "wildcards",
    )
)
COMMON_PROMPTS_DIR = Path(
    _env(
        "COMMON_PROMPTS_DIR",
        WORKSPACE_ROOT / "common-prompts",
    )
)
EXPORTS_DIR = Path(_env("EXPORTS_DIR", PROJECT_ROOT / "exports"))

# Default location of a scraped metadata dump. Used only as a hint in the
# UI; ingestion takes an explicit path. None when unconfigured.
_metadata_env = _env("METADATA_FILE", "")
DEFAULT_METADATA_FILE: Path | None = Path(_metadata_env) if _metadata_env else None

# See-through layer decomposition (Decompose tab). Machine-specific paths;
# defaults assume the sibling checkout + conda env + local model dirs, all
# overridable via env vars or the in-app config (Preset kind='config',
# name='decompose').
SEETHROUGH_REPO_DIR = Path(_env("SEETHROUGH_DIR", WORKSPACE_ROOT / "see-through"))
SEETHROUGH_PYTHON = Path(_env("SEETHROUGH_PYTHON", "python"))
SEETHROUGH_LAYERDIFF_DIR = Path(
    _env("SEETHROUGH_LAYERDIFF", WORKSPACE_ROOT / "REPOS" / "seethrough_layerdiff3d")
)
SEETHROUGH_DEPTH_DIR = Path(
    _env("SEETHROUGH_DEPTH", WORKSPACE_ROOT / "REPOS" / "seethrough_marigold")
)
DECOMPOSE_DIR = DATA_DIR / "decompose"

# Local LLM via llama-swap: an OpenAI-compatible server that loads/swaps/
# unloads GGUF models on demand (https://github.com/mostlygeek/llama-swap).
# The BAT/CONFIG paths are optional — without them the NAI splitter still
# works against an already-running server; only the in-app "start server"
# button and the idle-TTL setting need them.
LLAMA_SWAP_URL = _env("LLAMA_SWAP_URL", "http://127.0.0.1:8080")
LLAMA_SWAP_START_BAT = Path(_env("LLAMA_SWAP_BAT", ""))
LLAMA_SWAP_CONFIG = Path(_env("LLAMA_SWAP_CONFIG", ""))

# Subject-count tags marking multi-character images (matched in raw_prompt).
MULTI_CHAR_TAGS = (
    "2girls",
    "3girls",
    "4girls",
    "5girls",
    "6+girls",
    "multiple_girls",
    "2boys",
    "3boys",
    "4boys",
    "5boys",
    "6+boys",
    "multiple_boys",
    "2others",
    "multiple_others",
)
# Tag pairs that also mean "more than one character" (e.g. a 1boy+1girl
# couple) — each pair must BOTH be present in raw_prompt.
MULTI_CHAR_PAIRS = (
    ("1girl", "1boy"),
    ("1girl", "1other"),
    ("1boy", "1other"),
)

_SUBJECT_TAG_RE = re.compile(
    r"^(?:\d+\+?(?:girl|boy|other)s?|multiple_(?:girls|boys|others)|solo)$"
)


def subject_summary(raw_prompt: str) -> str:
    """Compact character-count summary from a raw tag list, e.g.
    '2girls · 1boy'. Empty string when no subject-count tags exist."""
    counts = [
        t.strip().replace("_", " ")
        for t in (raw_prompt or "").split(",")
        if _SUBJECT_TAG_RE.match(t.strip().lower())
    ]
    counts = [c for c in counts if c != "solo"] or counts
    return " · ".join(dict.fromkeys(counts))

# Optional SOCKS/HTTP proxy for booru fetches only, for networks where
# boorus are unreachable (e.g. "socks5://127.0.0.1:40000" for a local
# Cloudflare WARP proxy). The port is probed at fetch time and the client
# falls back to a direct connection when the proxy is down. "" = direct.
BOORU_PROXY = _env("BOORU_PROXY", "")

# Networking / scraping
DEFAULT_USER_AGENT = _env(
    "USER_AGENT", "TagForge/0.1 (+https://github.com/Restbehere/tagforge)"
)
DEFAULT_REQ_PER_SECOND = float(_env("REQ_PER_SECOND", "5"))

API_HOST = _env("HOST", "127.0.0.1")
API_PORT = int(_env("PORT", "9301"))

# Host headers the API accepts. CORS cannot stop DNS rebinding: a page on
# http://attacker.example:9301 that re-resolves to 127.0.0.1 makes
# *same-origin* requests to this unauthenticated local API, so the origin
# allowlist is never consulted. Validating Host is what blocks it.
# Binding a non-loopback TAGFORGE_HOST (e.g. 0.0.0.0 for LAN access)
# means adding the name/IP you actually type in the browser here.
# "*" disables the check (also needed for IPv6 literals, which Starlette's
# port-stripping cannot match).
ALLOWED_HOSTS = [
    h.strip()
    for h in _env("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if h.strip()
]

DEV_FRONTEND_ORIGIN = _env("DEV_ORIGIN", "http://localhost:9300")

# Source.kind values grouped by provenance. The Export / Scenes / Builder UIs
# filter on these to give the user a "local only" vs "booru only" toggle.
ORIGIN_LOCAL: frozenset[str] = frozenset({"metadata_file"})
# Must match the site keys in ingest/danbooru_client.SITE_HOSTS — a site
# missing here silently vanishes from every booru-origin filter.
ORIGIN_BOORU: frozenset[str] = frozenset(
    {"danbooru", "aibooru", "safebooru-donmai", "safebooru"}
)


def origin_kinds(origin: str | None) -> list[str] | None:
    """Map a UI ``origin`` value to the set of Source.kind strings it covers."""
    if not origin:
        return None
    o = origin.lower()
    if o == "local":
        return sorted(ORIGIN_LOCAL)
    if o == "booru":
        return sorted(ORIGIN_BOORU)
    return None


def ensure_dirs() -> None:
    """Create writable directories at startup."""
    for d in (DATA_DIR, EXPORTS_DIR, DECOMPOSE_DIR):
        d.mkdir(parents=True, exist_ok=True)

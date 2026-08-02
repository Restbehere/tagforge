"""Tag classifier (Stage 1).

Stage 1 covers the deterministic, free portion of the pipeline:

1. If a tag is present in an optional 194k-row Danbooru ``tags.jsonl``
   with category 1/4/5 (artist/character/meta) we route it to a drop bucket
   immediately.
2. Otherwise, we look up the tag in the ``tag_tree.json`` taxonomy from
   ``KohakuBlueleaf/danbooru-tag-tree``. The tree's top-level paths are mapped
   to our six target buckets via :data:`PRIORITY_MAP` (priority order matters
   because some tags appear under multiple parents -- e.g. ``glasses`` lives
   under both Eyewear and Body parts).
3. Unmatched tags get bucket ``"other"`` with ``bucket_source = "unknown"`` and
   ``confidence = 0`` -- these are the residuals stage 2/3 can address later.

This module is intentionally pure (no DB writes); the runner persists results.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .. import settings
from .scene_exclude import is_eye_color_tag, is_scene_excluded


logger = logging.getLogger(__name__)


# The buckets used everywhere downstream (export, UI, builder).
#
# ``extras`` captures known-but-otherwise-residual Danbooru tags (objects, food,
# props, animals, etc.) that don't fit the six "scene" buckets but are still
# useful to ship in a wildcard file. The internal ``other`` bucket is reserved
# for tags that aren't in any taxonomy at all — useful for debugging via the
# Tags page but excluded from exports by default.
BUCKETS: tuple[str, ...] = (
    "outfit",
    "pose",
    "expression",
    "background",
    "composition",
    "accessory",
    "extras",
    "character",
    "quality_meta",
    "artist",
    "other",
)


# Buckets that produce one line per image in scene_line, in the order the
# runner persists them. Adding/removing here is the single source of truth
# for which buckets the exporter and Builder see by default.
SCENE_BUCKETS: tuple[str, ...] = (
    "outfit",
    "pose",
    "expression",
    "background",
    "composition",
    "accessory",
    "extras",
    # Persisted so users can export trending characters from Danbooru and
    # keep per-image character lists alongside the rest of a scene. The
    # combined "scene" bucket below intentionally still omits character so
    # the wildcard stays drop-in for prompts that supply their own character.
    "character",
)


# Mapping from substrings that appear in ``tag_tree.json`` paths to one of our
# buckets. The **first** match in priority order wins, so the order is significant.
# Paths look like: ["Visual characteristics", "Attire and body accessories",
#                   "Attire", "shirts", "white_shirt"]
PRIORITY_MAP: list[tuple[str, str]] = [
    # outfit / clothing first
    ("Attire and body accessories", "outfit"),
    ("Sexual attire", "outfit"),
    ("Swimsuits", "outfit"),
    ("Dresses", "outfit"),
    ("Eyewear", "accessory"),
    ("Hair", "accessory"),
    ("Piercings", "accessory"),
    ("Headwear", "outfit"),
    ("Footwear", "outfit"),
    ("Legwear", "outfit"),
    ("Sleeves", "outfit"),
    ("Tops", "outfit"),
    ("Skirts", "outfit"),
    ("Pants", "outfit"),
    ("Bottoms", "outfit"),
    # pose / action
    ("Posture", "pose"),
    ("Sex acts", "pose"),
    ("Verbs and Gerunds", "pose"),
    # expression (eye *colors* are stripped earlier via :func:`is_eye_color_tag`)
    ("Face tags", "expression"),
    ("Eyes tags", "expression"),
    ("Mouth", "expression"),
    ("Tears", "expression"),
    # background / scene
    ("Backgrounds", "background"),
    ("Locations", "background"),
    ("Plants", "background"),
    ("Weather", "background"),
    ("Sky", "background"),
    ("Time", "background"),
    # composition / framing
    ("Image composition", "composition"),
    ("Focus tags", "composition"),
    # extras — props, objects, food, animals, vehicles, instruments, weapons.
    # These were previously routed to "other"; promoting them gives the user a
    # `extras.txt` wildcard with things like ``holding cup of tea``, ``cat``,
    # ``katana``, etc. They overlap with pose for verb-like tags (``holding``)
    # but Stage 1 still routes verbs through Posture / Verbs first.
    ("Objects", "extras"),
    ("Object", "extras"),
    ("Food", "extras"),
    ("Foods", "extras"),
    ("Drink", "extras"),
    ("Drinks", "extras"),
    ("Animals", "extras"),
    ("Creatures", "extras"),
    ("Vehicles", "extras"),
    ("Weapons", "extras"),
    ("Musical instruments", "extras"),
    ("Instruments", "extras"),
    ("Tools", "extras"),
    ("Items", "extras"),
    ("Furniture", "extras"),
    ("Props", "extras"),
]


@dataclass
class TagAssignment:
    name: str
    bucket: str
    bucket_source: str
    confidence: float
    category: int = 0


@lru_cache(maxsize=1)
def _load_tag_tree() -> dict[str, str]:
    """Walk ``tag_tree.json`` once and return ``{tag_name: bucket}``.

    The KohakuBlueleaf tree encodes leaves as
    ``{"Dress": "/wiki_pages/dress"}`` -- the key is the tag display name and
    the value is the wiki path. ``self`` keys hold inner-node descriptions
    (sometimes also leaf dicts) that should be walked too.
    """
    path = settings.TAG_TREE_PATH
    if not path.exists():
        logger.warning(
            "tag_tree.json missing at %s; stage1 will only use dataset categories",
            path,
        )
        return {}

    with path.open("r", encoding="utf-8") as fh:
        tree = json.load(fh)

    # tag -> (priority rank, bucket). Tags appear under several parents, and
    # PRIORITY_MAP order is supposed to pick the winner. The old walk kept
    # whichever leaf the JSON iteration reached FIRST (dict insertion order),
    # which mis-bucketed hundreds of common tags ('nature' via composition
    # instead of background) and — worse — pinned tags whose first-seen leaf
    # sat under an unmapped section to 'other', hiding their real path from
    # Stage 1 entirely. Keeping the lowest rank across every occurrence makes
    # the result match the documented priority and stable under upstream
    # re-orderings of tag_tree.json.
    best: dict[str, tuple[int, str]] = {}

    def canonical_from_wiki(value: str) -> str:
        # "/wiki_pages/short_hair" -> "short_hair"
        return value.rsplit("/", 1)[-1].strip().lower().replace(" ", "_")

    def canonical_from_key(key: str) -> str:
        return key.strip().lower().replace(" ", "_")

    def walk(node, path_stack: list[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    # Leaf: {"Display Name": "/wiki_pages/canonical_name"}
                    if k == "self":
                        continue
                    if v.startswith("/wiki_pages/"):
                        tag = canonical_from_wiki(v)
                    else:
                        tag = canonical_from_key(k)
                    if tag:
                        ranked = _rank_bucket_for_path(path_stack + [k])
                        prev = best.get(tag)
                        if prev is None or ranked[0] < prev[0]:
                            best[tag] = ranked
                else:
                    # Inner node: recurse, but don't add the section name to the
                    # path twice for the magic 'self' container.
                    next_path = path_stack if k == "self" else path_stack + [k]
                    walk(v, next_path)
        elif isinstance(node, list):
            for item in node:
                walk(item, path_stack)

    walk(tree, [])

    mapping = {tag: bucket for tag, (_rank, bucket) in best.items()}
    logger.info("Loaded %d tag→bucket mappings from tag_tree.json", len(mapping))
    return mapping


def _rank_bucket_for_path(path_stack: Iterable[str]) -> tuple[int, str]:
    """(PRIORITY_MAP rank, bucket) for one leaf path; unmapped ranks last so
    any real bucket from another occurrence of the tag beats 'other'."""
    joined = " / ".join(path_stack)
    for rank, (needle, bucket) in enumerate(PRIORITY_MAP):
        if needle in joined:
            return rank, bucket
    return len(PRIORITY_MAP), "other"


@lru_cache(maxsize=1)
def _load_dataset_categories() -> dict[str, dict]:
    """Load ``tags.jsonl`` if present. Maps tag_name → {category, post_count}."""
    path = settings.TAGS_JSONL_PATH
    if not path.exists():
        logger.info("tags.jsonl not found at %s; skipping dataset categories", path)
        return {}

    out: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            name = entry.get("name")
            if not name:
                continue
            if entry.get("is_deprecated"):
                continue
            out[name.lower()] = {
                "category": int(entry.get("category", 0)),
                "post_count": int(entry.get("post_count", 0)),
            }
    logger.info("Loaded %d tags from tags.jsonl", len(out))
    return out


def reload_caches() -> None:
    """Force-reload taxonomies (used when files are added/updated at runtime)."""
    _load_tag_tree.cache_clear()
    _load_dataset_categories.cache_clear()


_ANTHRO_SUFFIXES: frozenset[str] = frozenset(
    {"boy", "boys", "girl", "girls", "man", "men", "woman", "women"}
)

# Parenthesized suffix → bucket. Danbooru's canonical disambiguation form is
# ``<name>_(<qualifier>)`` — most are character names from a franchise, so
# anything not matched by a more specific rule below defaults to ``character``.
# Order doesn't matter because we look up by exact suffix.
_QUALIFIER_SUFFIX_BUCKET: dict[str, str] = {
    "cosplay": "outfit",   # wearing a character's costume
    "style": "composition",  # art style modifier
    "art_style": "composition",
    "weapon": "extras",
    "object": "extras",
    "item": "extras",
    "food": "extras",
    "meme": "other",
    "module": "other",  # idolmaster module is a costume but too ambiguous
    # Danbooru's qualifier families that are disambiguation but NOT franchise
    # names. Without these, shower_(place) and diamond_(shape) fell through
    # to the franchise fallback and were exported as characters.
    "place": "background",
    "shape": "extras",
    "symbol": "extras",
    "cheerleading": "extras",  # pom_pom_(cheerleading) — the prop
    "medium": "quality_meta",  # dakimakura_(medium) describes the image itself
    "software": "quality_meta",
    "sex": "pose",  # shimaidon_(sex) etc. — act tags
}

# Qualifiers that mark a body/transformation variant, not a franchise —
# genderswap_(mtf), crossdressing_(ftm). No scene bucket fits cleanly, so
# they must fall through to the tree / Stage 2/3 instead of being stamped
# 'character' by the franchise fallback.
_NON_FRANCHISE_QUALIFIERS: frozenset[str] = frozenset({"mtf", "ftm", "otf"})

_QUALIFIER_RE = re.compile(r"^(?P<base>.+?)_\((?P<qualifier>[a-z0-9_\-+&!\.']+)\)$")


def _classify_qualifier_suffix(name: str) -> tuple[str, str, float] | None:
    """Return ``(bucket, source_label, confidence)`` for a KNOWN qualifier.

    Handles tags of the form ``<base>_(<qualifier>)`` whose qualifier is in
    the map (cosplay → outfit, place → background, ...). Unknown qualifiers
    return ``None`` here — the franchise-disambiguation guess lives in
    :func:`classify_tag` AFTER the tag-tree lookup, because guessing
    'character' first permanently shadowed tags the tree actually knows.

    Confidence is intentionally bounded to ~0.88 so an explicit
    ``tags.jsonl`` category (1.0) still wins. We do NOT match if the result
    is ``"other"`` because that's not actionable.
    """
    m = _QUALIFIER_RE.match(name)
    if m is None:
        return None
    qualifier = m.group("qualifier").lower()
    if qualifier in _QUALIFIER_SUFFIX_BUCKET:
        bucket = _QUALIFIER_SUFFIX_BUCKET[qualifier]
        if bucket == "other":
            return None  # don't proactively shove things into other
        return bucket, "qualifier_rule", 0.88
    return None


def _is_franchise_disambiguation(name: str) -> bool:
    """``<base>_(<qualifier>)`` with a qualifier we have no rule for —
    Danbooru's character form (``hatsune_miku_(vocaloid)``), assumed only
    once every deterministic lookup has failed."""
    m = _QUALIFIER_RE.match(name)
    if m is None:
        return False
    return m.group("qualifier").lower() not in _NON_FRANCHISE_QUALIFIERS


def _is_anthro_subject(name: str) -> bool:
    """Heuristic for anthropomorphic / species subject identifiers.

    Catches things like ``hedgehog_boy``, ``goat_boy``, ``red_panda_girl``,
    ``alpaca_girl``, ``cat_girl``, ``cool_old_man`` — multi-segment tags
    whose last segment is a gender/species marker. These aren't in
    ``tag_tree.json`` and aren't ``category=4`` in ``tags.jsonl``, so
    without this rule they fall to ``other`` and Stage 2 routes them at
    random by cosine of the noun. They're really subject identifiers, so
    we send them to ``character``.

    Requires at least one segment before the suffix so bare ``boy``/``girl``
    don't get swept up — those are generic descriptors, not identifiers.
    Single-token counters like ``1girl`` / ``2boys`` are unaffected
    because they have no underscore separator.
    """
    parts = name.split("_")
    if len(parts) < 2:
        return False
    return parts[-1] in _ANTHRO_SUFFIXES


def classify_tag(name: str) -> TagAssignment:
    """Classify a single canonical tag name (lower-cased, underscore-spaced)."""
    name = name.strip().lower()
    if not name:
        return TagAssignment(name=name, bucket="other", bucket_source="unknown", confidence=0.0)

    ds = _load_dataset_categories()
    info = ds.get(name)
    category = int(info["category"]) if info else 0

    if info and category in {1, 4, 5}:
        bucket = {1: "artist", 4: "character", 5: "quality_meta"}[category]
        return TagAssignment(
            name=name,
            bucket=bucket,
            bucket_source="dataset_category",
            confidence=1.0,
            category=category,
        )

    # Eye colors + subject counters + body anatomy — user supplies in main prompt.
    if is_eye_color_tag(name) or is_scene_excluded(name):
        return TagAssignment(
            name=name,
            bucket="quality_meta",
            bucket_source="scene_exclude",
            confidence=1.0,
            category=category,
        )

    if _is_anthro_subject(name):
        return TagAssignment(
            name=name,
            bucket="character",
            bucket_source="anthro_rule",
            confidence=0.90,
            category=category,
        )

    qual = _classify_qualifier_suffix(name)
    if qual is not None:
        q_bucket, q_source, q_conf = qual
        return TagAssignment(
            name=name,
            bucket=q_bucket,
            bucket_source=q_source,
            confidence=q_conf,
            category=category,
        )

    tree = _load_tag_tree()
    bucket = tree.get(name)
    if bucket and bucket != "other":
        return TagAssignment(
            name=name,
            bucket=bucket,
            bucket_source="tag_tree",
            confidence=0.95,
            category=category,
        )

    # Try a few cheap rewrites: hyphenated, hyphen-to-underscore, no-trailing-s
    for candidate in (name.replace("-", "_"), name.rstrip("s")):
        if candidate != name:
            b = tree.get(candidate)
            if b and b != "other":
                return TagAssignment(
                    name=name,
                    bucket=b,
                    bucket_source="tag_tree",
                    confidence=0.80,
                    category=category,
                )

    # Only now assume an unknown parenthesised qualifier is a franchise name.
    # Guessing before the tree lookups routed shower_(place)-style tags into
    # character.txt with a confidence Stage 2/3 never revisit.
    if _is_franchise_disambiguation(name):
        return TagAssignment(
            name=name,
            bucket="character",
            bucket_source="franchise_suffix",
            confidence=0.85,
            category=category,
        )

    return TagAssignment(
        name=name,
        bucket="other",
        bucket_source="unknown",
        confidence=0.0,
        category=category,
    )


def classify_many(names: Iterable[str]) -> list[TagAssignment]:
    return [classify_tag(n) for n in names]

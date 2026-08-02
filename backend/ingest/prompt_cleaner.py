"""Normalize raw NovelAI / Stable-Diffusion prompts into canonical Danbooru tags.

The dataset is a giant mix of authoring styles:

- NovelAI V4 ``1.5::tag::`` style weighted regions
- A1111 ``(tag:1.2)``, ``{{tag}}``, ``[[tag]]`` curly/bracket weights
- LoRA invocations ``<lora:name:weight>``
- Negative-prompt boilerplate (we skip these by reading the prompt block only)
- Stray full-width commas (``，``), CRLF artifacts, repeated commas, etc.

Output: a list of canonical lowercase tags with spaces replaced by ``_``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order-sensitive: weight wrappers must be peeled before splitting on commas.
_LORA_RE = re.compile(r"<\s*lora\s*:[^>]*>", flags=re.IGNORECASE)
_EMBEDDING_RE = re.compile(r"<\s*embedding\s*:[^>]*>", flags=re.IGNORECASE)
_NAI_WEIGHT_RE = re.compile(r"-?\d+(?:\.\d+)?\s*::")  # opening 1.5::
_NAI_WEIGHT_CLOSE_RE = re.compile(r"::")  # closing
_A1111_WEIGHT_RE = re.compile(r":\s*-?\d+(?:\.\d+)?\s*\)")  # (tag:1.2)
_PARENS_BAL_RE = re.compile(r"^[\(\)\[\]\{\}]+|[\(\)\[\]\{\}]+$")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")

# NAI dynamic prompt syntax: || option1 | option2 | option3 ||
# Options are separated by | and the whole block is delimited by ||.
# Each option can itself contain comma-separated tags.
_PIPE_BLOCK_RE = re.compile(r"\|\|(.*?)\|\|", re.DOTALL)
_FULLWIDTH_COMMA = "，"

# Quality / meta words to strip when ``drop_quality_tags`` is enabled.
_QUALITY_TAGS: set[str] = {
    "masterpiece",
    "best_quality",
    "amazing_quality",
    "very_aesthetic",
    "incredibly_absurdres",
    "absurdres",
    "highres",
    "high_quality",
    "low_quality",
    "worst_quality",
    "lowres",
    "rating:general",
    "rating:safe",
    "rating:sensitive",
    "rating:questionable",
    "rating:explicit",
    "very_awa",
    "newest",
    "very_aesthetic",
    "no_text",
    "bad_anatomy",
    "bad_hands",
    "blurry",
    "jpeg_artifacts",
    "watermark",
    "signature",
    "username",
    "scan",
    "displeasing",
    "very_displeasing",
    "chromatic_aberration",
    "ai-generated",
    "ai-assisted",
    "ai_generated",
    "ai_assisted",
    "flat_color",
    "noise",
    "scan_artifacts",
    "film_grain",
    "dithering",
    "halftone",
    "screentone",
    "negative_space",
    "blank_page",
    "logo",
    "too_many_watermarks",
    "dated",
    "@_@",
    "best_quality,",
    "+_+",
}

# Year tags like 'year 2025', 'year2024' commonly used as quality boosters.
_YEAR_RE = re.compile(r"^year[\s_]*\d{4}$")

# Detect leading 'artist:' / 'character:' style namespace prefixes.
_NS_RE = re.compile(r"^(artist|character|copyright|meta)\s*:\s*", flags=re.IGNORECASE)


@dataclass
class CleanedPrompt:
    """Result of :func:`clean_prompt`."""

    tags: list[str]
    artist_tags: list[str]
    quality_tags: list[str]
    raw_tokens: int


def _expand_pipe_blocks(text: str) -> str:
    """Replace each NAI || opt1 | opt2 | opt3 || block with all non-empty options.

    Used as a safety-net when the resolved ``actual_prompts`` field is absent.
    Example: ``|| |pull down pantyhose, |torn thighhighs, ||``
             → ``pull down pantyhose, torn thighhighs``
    """
    def _sub(m: re.Match) -> str:
        parts = [p.strip(" ,\t") for p in m.group(1).split("|")]
        non_empty = [p for p in parts if p]
        # Trailing comma separates expanded options from any text that
        # immediately follows the closing || without its own comma.
        return (", ".join(non_empty) + ",") if non_empty else ""

    return _PIPE_BLOCK_RE.sub(_sub, text)


def extract_pipe_block_options(text: str) -> list[str]:
    """Return every non-empty tag option from all || dynamic || blocks.

    Operates on the *template* prompt (the field with pipe syntax) to harvest
    every choice that could appear.  Each option may contain comma-separated
    tags; all individual tags are returned in canonical ``snake_case`` form.
    """
    tags: list[str] = []
    for m in _PIPE_BLOCK_RE.finditer(text):
        for option in m.group(1).split("|"):
            for piece in option.split(","):
                norm = _normalize_tag(piece)
                if norm:
                    tags.append(norm)
    return tags


def _strip_weight_syntax(text: str) -> str:
    """Remove A1111 / NAI weight wrappers without losing the inner tags."""
    # Expand pipe blocks first (safety net when actual_prompts is unavailable)
    text = _expand_pipe_blocks(text)
    text = _LORA_RE.sub("", text)
    text = _EMBEDDING_RE.sub("", text)

    # NAI: "1.5::tag1, tag2::" -> "tag1, tag2"
    text = _NAI_WEIGHT_RE.sub("", text)
    text = _NAI_WEIGHT_CLOSE_RE.sub("", text)

    # A1111: "(tag:1.2)" -> "(tag)" so the next pass strips parens
    text = _A1111_WEIGHT_RE.sub(")", text)

    # A1111 escapes parens in tag names (\( \)); restore them so character
    # tags like ``hatsune_miku_(vocaloid)`` survive normalisation.
    text = text.replace("\\(", "(").replace("\\)", ")")

    # Normalize fullwidth comma & CRLF
    text = text.replace(_FULLWIDTH_COMMA, ",").replace("\r\n", "\n")

    return text


def _normalize_tag(raw: str) -> str:
    """Lowercase, underscore-spaces, strip wrapping brackets/spaces."""
    t = raw.strip()
    # Peel matching outer brackets repeatedly: "{{[tag]}}" -> "tag"
    for _ in range(6):
        peeled = _PARENS_BAL_RE.sub("", t).strip()
        if peeled == t:
            break
        t = peeled

    t = t.strip(" \t\n,.;:|")
    t = t.lower()
    t = t.replace(" ", "_")
    t = _MULTI_UNDERSCORE_RE.sub("_", t)
    t = t.strip("_")
    return t


def clean_prompt(
    raw: str,
    *,
    drop_artist_tags: bool = True,
    drop_quality_tags: bool = True,
) -> CleanedPrompt:
    if not raw:
        return CleanedPrompt(tags=[], artist_tags=[], quality_tags=[], raw_tokens=0)

    stripped = _strip_weight_syntax(raw)
    # Split on commas AND newlines: humans use both as separators in this corpus.
    rough = [t for chunk in stripped.split("\n") for t in chunk.split(",")]

    tags: list[str] = []
    artist_tags: list[str] = []
    quality_tags: list[str] = []
    seen: set[str] = set()

    for raw_tok in rough:
        if not raw_tok or not raw_tok.strip():
            continue
        norm = _normalize_tag(raw_tok)
        if not norm:
            continue
        # Strip namespace prefixes
        ns_match = _NS_RE.match(norm)
        if ns_match:
            ns = ns_match.group(1).lower()
            stripped_norm = norm[ns_match.end():]
            if ns == "artist":
                if not drop_artist_tags:
                    artist_tags.append(stripped_norm)
                else:
                    artist_tags.append(stripped_norm)  # always tracked, just dropped from main tags
                continue
            # character/copyright/meta - keep stripped form so we can categorize
            norm = stripped_norm

        if _YEAR_RE.match(norm):
            quality_tags.append(norm)
            if drop_quality_tags:
                continue

        if norm in _QUALITY_TAGS:
            quality_tags.append(norm)
            if drop_quality_tags:
                continue

        if norm in seen:
            continue
        seen.add(norm)
        tags.append(norm)

    return CleanedPrompt(
        tags=tags,
        artist_tags=artist_tags,
        quality_tags=quality_tags,
        raw_tokens=len(rough),
    )

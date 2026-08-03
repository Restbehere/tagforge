"""Tags that must never appear in scene wildcards or the Builder.

The user's main prompt already supplies subject counters (``1girl``, ``solo``)
and they do not want body-anatomy descriptors (``breasts``, ``tail``, ``wings``)
polluting pose/outfit/background/composition rolls. These tags may still exist
in the DB for auditing, but they are routed to ``quality_meta`` at Stage 1 and
stripped when building ``scene_line`` rows, exporting, and rolling in the Builder.
"""

from __future__ import annotations

import re

# Re-exported as the export UI "default deny" list (subject counters only).
# Body-anatomy tags are excluded via :func:`is_scene_excluded` even if not listed
# here — export uses the full predicate when ``use_default_deny`` is on.
DEFAULT_DENY_SUBJECT: frozenset[str] = frozenset(
    {
        "1girl",
        "2girls",
        "3girls",
        "4girls",
        "5girls",
        "6+girls",
        "multiple_girls",
        "solo",
        "solo_focus",
        "1boy",
        "2boys",
        "3boys",
        "4boys",
        "5boys",
        "6+boys",
        "multiple_boys",
        "male",
        "male_focus",
        "solo_male",
        "mature_male",
        "muscular_male",
        "old_man",
        "1boy_1girl",
        "1girl_1boy",
        "1other",
        "multiple_others",
        "ambiguous_gender",
    }
)

# Extra anatomy / subject descriptors excluded from scene buckets (not in the
# export deny textarea by default, but always stripped when ``use_default_deny``
# is enabled because :func:`default_deny_set` unions this with subject counters).
DEFAULT_DENY_ANATOMY: frozenset[str] = frozenset(
    {
        "breasts",
        "flat_chest",
        "cleavage",
        "tail",
        "tails",
        "no_tail",
        "wings",
        "no_wings",
        "alternate_wings",
        # Hair / horn descriptors that embed often misroutes into composition.
        "ahoge",
        "horns",
    }
)

# Danbooru-style subject counters and focus tokens.
_SUBJECT_COUNTER_RE = re.compile(
    r"^(\d+\+?)?(girls?|boys?|others?)$"
    r"|^solo(_|$)"
    r"|^solo_focus$"
    r"|^multiple_(girls|boys|others)$"
    r"|^male(_focus|_child)?$"
    r"|^female(_focus|_child)?$"
    r"|^mature_(male|female)$"
    r"|^old_(man|woman)$"
    r"|^muscular_(male|female)$"
    r"|^1boy_1girl$"
    r"|^1girl_1boy$"
)

# ``tail`` must be a whole underscore-delimited token. Matching it anywhere
# swallowed every name that merely ends in the letters — ``cottontail``,
# ``swallowtail``, ``huntail``, ``flametail_(arknights)``, ``cattail`` —
# and filed those characters and plants as anatomy.
_TAIL_RE = re.compile(
    r"^(no_)?tail(s)?$"
    r"|(?:^|_)tail(s)?(?:_|$)"
    r"|_(?:cat|dog|fox|dragon|demon|snake|fish|fluffy|long|short|multiple)_tail$"
)
_QUALIFIED_NAME_RE = re.compile(r"_\([a-z0-9_\-+&!.':]+\)$")
_WINGS_RE = re.compile(r"^(no_|alternate_)?wings$|_wings$")
_BREASTS_RE = re.compile(
    r"^breasts$"
    r"|_breasts$"
    r"|^breasts_"
    r"|^between_breasts$"
    r"|^flat_chest$"
)

# Danbooru eye *color* tags (``red_eyes``, ``blue_eyes``, …). Eye *state* tags
# (``closed_eyes``, ``half-closed_eyes``, ``rolling_eyes``, ``one_eye_closed``)
# are kept — they belong in expression/pose, not the character color block.
_EYE_COLOR_PREFIXES: frozenset[str] = frozenset(
    {
        "red",
        "blue",
        "green",
        "purple",
        "orange",
        "yellow",
        "aqua",
        "brown",
        "grey",
        "gray",
        "pink",
        "white",
        "black",
        "golden",
        "amber",
        "violet",
        "lime",
        "teal",
        "cyan",
        "magenta",
        "indigo",
        "maroon",
        "beige",
        "silver",
        "gold",
        "ruby",
        "sapphire",
        "emerald",
        "multicolored",
        "multicolor",
        "gradient",
        "two-tone",
        "two_tone",
        "colored",
    }
)
_EYE_COLOR_EXACT: frozenset[str] = frozenset(
    {
        "eyes",
        "heterochromia",
        "multicolored_eyes",
        "gradient_eyes",
        "colored_eyelashes",
        "slit_pupils",
        "symbol-shaped_pupils",
    }
)
_EYE_COLOR_SUFFIX_RE = re.compile(
    r"^(" + "|".join(sorted(_EYE_COLOR_PREFIXES, key=len, reverse=True)) + r")_eyes$"
)


def is_eye_color_tag(name: str) -> bool:
    """True for iris/color tags the user supplies via their character prompt."""
    n = name.strip().lower().replace(" ", "_")
    if not n:
        return False
    if n in _EYE_COLOR_EXACT:
        return True
    if _EYE_COLOR_SUFFIX_RE.match(n):
        return True
    # ``light_blue_eyes``, ``dark_green_eyes``, etc.
    if n.endswith("_eyes"):
        parts = n[:-5].split("_")
        if parts and parts[-1] in _EYE_COLOR_PREFIXES:
            return True
        if len(parts) >= 2 and parts[-2] in {"light", "dark", "pale", "bright", "deep"}:
            if parts[-1] in _EYE_COLOR_PREFIXES:
                return True
    return False


def is_scene_excluded(name: str) -> bool:
    """Return True if ``name`` must not appear in scene buckets / Builder rolls."""
    n = name.strip().lower().replace(" ", "_")
    if not n:
        return False
    # A parenthesised qualifier marks a disambiguated proper noun, so the
    # anatomy heuristics below must not read it as body text:
    # flametail_(arknights) and tail_(honkai:_star_rail) are characters,
    # cattail_(plants_vs._zombies) is a plant. Let the character rules
    # classify them instead of filing them as body parts.
    if _QUALIFIED_NAME_RE.search(n):
        return False
    if is_eye_color_tag(n):
        return True
    if n in DEFAULT_DENY_SUBJECT or n in DEFAULT_DENY_ANATOMY:
        return True
    if _SUBJECT_COUNTER_RE.match(n):
        return True
    if _BREASTS_RE.search(n):
        return True
    # twintails / low_twintails / short_twintails are hairstyles, not anatomy
    # — they were silently stripped from every scene line for months.
    if (
        _TAIL_RE.search(n)
        and "ponytail" not in n
        and "cocktail" not in n
        and "twintail" not in n
    ):
        return True
    if _WINGS_RE.search(n) and "earrings" not in n:
        return True
    return False


def default_deny_set() -> frozenset[str]:
    """Full deny set for export + Builder (subject + anatomy heuristics)."""
    return DEFAULT_DENY_SUBJECT | DEFAULT_DENY_ANATOMY


def filter_scene_line_text(
    line: str,
    *,
    extra_deny: frozenset[str] | None = None,
    use_scene_heuristics: bool = True,
) -> str | None:
    """Strip excluded tags from a comma-joined scene line; ``None`` if empty."""
    if not line or not line.strip():
        return None
    extra = extra_deny or frozenset()
    pieces = [p.strip() for p in line.split(",") if p.strip()]
    if not pieces:
        return None
    keep: list[str] = []
    for piece in pieces:
        canonical = piece.replace(" ", "_").lower()
        # Eye colors are always stripped from scene wildcards (even when the
        # user turned off the built-in deny list for export).
        if is_eye_color_tag(canonical):
            continue
        if use_scene_heuristics and is_scene_excluded(canonical):
            continue
        if canonical in extra:
            continue
        keep.append(piece)
    if not keep:
        return None
    return ", ".join(keep)


def filter_tag_names_for_scene(names: list[str]) -> list[str]:
    """Drop excluded tags while preserving order."""
    return [
        n
        for n in names
        if not is_eye_color_tag(n) and not is_scene_excluded(n)
    ]

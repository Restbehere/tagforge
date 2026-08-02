"""Tag → rating severity lookups for local-image rating inference.

Local NovelAI / SD metadata records don't carry a rating field. We infer one
by walking the image's tags and taking the worst severity present:

    g < s < q < e

A tag's severity comes from the curated dicts below. Anything not listed is
treated as ``g`` (general). The lists are intentionally conservative — body
descriptors that often appear in safe-for-work pinup art (``cleavage``,
``thighs``, ``midriff``) sit at ``s`` (sensitive), not higher.

Used both for ingest-time classification and export-time per-tag stripping.
"""

from __future__ import annotations

from typing import Iterable


SEVERITY: dict[str, int] = {"g": 0, "s": 1, "q": 2, "e": 3}
SEVERITY_NAME: dict[int, str] = {0: "g", 1: "s", 2: "q", 3: "e"}


# --- explicit (e) ----------------------------------------------------------
# Nudity, sexual acts, genital tags, bodily fluids in sexual context.
_EXPLICIT: tuple[str, ...] = (
    "nude",
    "naked",
    "nipples",
    "nipple",
    "pussy",
    "anus",
    "penis",
    "cock",
    "vaginal",
    "vaginal_sex",
    "anal",
    "anal_sex",
    "sex",
    "fellatio",
    "cunnilingus",
    "paizuri",
    "blowjob",
    "handjob",
    "footjob",
    "masturbation",
    "fingering",
    "rape",
    "bukkake",
    "creampie",
    "cum",
    "cum_in_mouth",
    "cum_on_body",
    "cum_on_face",
    "cum_on_hair",
    "cum_on_breasts",
    "cum_on_ass",
    "cum_string",
    "semen",
    "pubic_hair",
    "clitoris",
    "labia",
    "testicles",
    "scrotum",
    "ejaculation",
    "facial",
    "gangbang",
    "double_penetration",
    "triple_penetration",
    "tentacle_sex",
    "futanari",
    "dildo",
    "vibrator",
    "sex_toy",
    "anal_beads",
    "buttplug",
    "fisting",
    "nipple_play",
    "nipple_tweak",
    "nipple_pinch",
    "nipple_sucking",
    "licking_penis",
    "licking_nipple",
    "after_sex",
    "after_vaginal",
    "after_anal",
    "vaginal_object_insertion",
    "anal_object_insertion",
    "pussy_juice",
    "spread_pussy",
    "spread_anus",
    "spread_legs",  # commonly sexual context but ambiguous; keep at e
)

# --- questionable (q) ------------------------------------------------------
# Suggestive but not explicitly showing genitals/penetration. Partial nudity,
# heavy fetish gear, sexually-suggestive poses.
_QUESTIONABLE: tuple[str, ...] = (
    "topless",
    "bottomless",
    "partially_nude",
    "see-through",
    "see_through",
    "naked_apron",
    "naked_towel",
    "naked_shirt",
    "naked_ribbon",
    "nipple_bulge",
    "nipple_slip",
    "cameltoe",
    "pantyshot",
    "panty_shot",
    "panty_pull",
    "wet_panties",
    "wet_pussy",
    "bare_butt",
    "bare_ass",
    "ass_focus",
    "ass_grab",
    "ass_visible_through_thighs",
    "breast_grab",
    "breast_press",
    "breast_squeeze",
    "groping",
    "molestation",
    "motorboating",
    "lactation",
    "bondage",
    "shibari",
    "bdsm",
    "leash",
    "ball_gag",
    "spread_anus",
    "presenting",
    "presenting_pussy",
    "ass_up",
    "huddled_position",
    "covering_breasts",
    "covering_nipples",
    "covering_crotch",
    "convenient_censoring",
    "censored",
    "uncensored",
    "x-ray",
    "anatomical_nonsense",
    "puffy_nipples",
    "areola_slip",
    "nip_slip",
    "wardrobe_malfunction",
    "torn_clothes",
    "torn_panties",
    "torn_pantyhose",
    "ripped_clothes",
    "clothes_pull",
    "shirt_lift",
    "skirt_lift",
    "dress_lift",
    "lifted_by_self",
    "lifted_by_another",
    "downblouse",
    "upskirt",
    "undressing",
    "after_kiss",
    "frenchkiss",
    "tongue_kiss",
    "kissing",  # ambiguous, keep at q to be safe
    "spanked",
    "spanking",
    "stomach_bulge",
    "x_x",
    "x-ray",
)

# --- sensitive (s) ---------------------------------------------------------
# Swimwear, lingerie, racy outfits, bust-emphasising descriptors. These are
# fine for a "sensitive" wildcard build but should be stripped from a "general"
# build.
_SENSITIVE: tuple[str, ...] = (
    "bikini",
    "micro_bikini",
    "string_bikini",
    "swimsuit",
    "school_swimsuit",
    "one-piece_swimsuit",
    "competition_swimsuit",
    "lingerie",
    "babydoll_(lingerie)",
    "negligee",
    "garter_belt",
    "garter_straps",
    "thong",
    "g-string",
    "panties",
    "underwear",
    "underwear_only",
    "bra",
    "sports_bra",
    "lace_bra",
    "lace_panties",
    "leotard",
    "playboy_bunny",
    "bunnysuit",
    "bunny_costume",
    "fishnets",
    "fishnet_pantyhose",
    "cleavage",
    "sideboob",
    "underboob",
    "backboob",
    "huge_breasts",
    "gigantic_breasts",
    "large_breasts",
    "bare_shoulders",
    "bare_midriff",
    "bare_back",
    "bare_legs",
    "midriff",
    "navel",
    "stomach",
    "cleavage_cutout",
    "armpit_cutout",
    "cleavage_window",
    "hip_focus",
    "thighs",
    "thick_thighs",
    "thigh_focus",
    "ass",
    "from_behind",
    "wide_hips",
    "curvy",
    "voluptuous",
    "skimpy",
    "revealing_clothes",
    "tight_clothes",
    "wet_clothes",
    "wet_shirt",
    "see-through_dress",
    "see-through_shirt",
    "see-through_skirt",
    "transparent",
    "transparent_clothes",
    "transparent_dress",
    "transparent_shirt",
    "transparent_skirt",
    "translucent",
    "covered_nipples",
    "covered_navel",
    "low_neckline",
    "plunging_neckline",
    "open_clothes",
    "open_jacket",
    "open_shirt",
    "open_robe",
    "unbuttoned",
    "unzipped",
    "lowleg",
    "low-leg_panties",
    "highleg",
    "highleg_panties",
    "highleg_leotard",
    "miniskirt",
    "microskirt",
    "very_short_shorts",
    "short_shorts",
    "booty_shorts",
    "buruma",
    "gym_shorts",
    "loincloth",
    "panty_lines",
    "string_panties",
    "side-tie_panties",
    "side-tie_bikini_bottom",
    "front-tie_bikini_top",
    "untied_bikini",
    "open_clothes",
    "wet",
    "sweat",
    "steamy",
    "pole_dancing",
    "stripper_pole",
    "garter",
    "stockings",
    "fishnet_top",
    "armpits",
    "armpit_hair",
)


def _build_rating_map() -> dict[str, str]:
    out: dict[str, str] = {}
    # Lowest first so the higher severities overwrite if a tag appears twice.
    for tag in _SENSITIVE:
        out[tag] = "s"
    for tag in _QUESTIONABLE:
        out[tag] = "q"
    for tag in _EXPLICIT:
        out[tag] = "e"
    return out


TAG_RATING: dict[str, str] = _build_rating_map()


def tag_rating(name: str) -> str:
    """Return the curated rating for ``name`` (default ``'g'``)."""
    if not name:
        return "g"
    key = name.strip().lower().replace(" ", "_")
    return TAG_RATING.get(key, "g")


def max_rating(*levels: str) -> str:
    """Return the worst rating across ``levels`` (g < s < q < e)."""
    worst = 0
    for lv in levels:
        worst = max(worst, SEVERITY.get(lv, 0))
    return SEVERITY_NAME[worst]


def infer_image_rating(tags: Iterable[str]) -> tuple[str, list[str]]:
    """Infer a per-image rating from its tags.

    Returns ``(rating, evidence)`` where ``rating`` is one of ``g/s/q/e`` and
    ``evidence`` is the list of tags that contributed at the highest severity.
    """
    worst = 0
    contributors: list[tuple[int, str]] = []
    for raw in tags:
        if not raw:
            continue
        lv = SEVERITY[tag_rating(raw)]
        if lv == 0:
            continue
        contributors.append((lv, raw))
        if lv > worst:
            worst = lv

    if worst == 0:
        return "g", []

    evidence = [name for lv, name in contributors if lv == worst]
    return SEVERITY_NAME[worst], evidence


def rating_allows(tag_name: str, cap: str) -> bool:
    """True if ``tag_name``'s rating is <= ``cap``."""
    return SEVERITY.get(tag_rating(tag_name), 0) <= SEVERITY.get(cap.lower(), 3)

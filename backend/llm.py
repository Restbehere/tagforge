"""Local LLM integration via llama-swap (reuses the Hermes setup).

llama-swap serves an OpenAI-compatible API and loads/swaps/unloads GGUF
models on demand (auto-unload after its configured idle ttl). Tag Forge
uses it for the NAI prompt splitter: flat Danbooru tag lists in, NovelAI
V4.5 base + per-character prompts out.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import Any

import httpx
from sqlmodel import select

from . import db, settings
from .models import Tag

logger = logging.getLogger("tagforge.llm")

# The dense 27B beats the 35B-A3B MoE on this task: clean per-character
# attribution with no tag duplication (the MoE hedges by copying ambiguous
# tags to every character). The abliterated build keeps that attribution
# discipline, stops the topic-level explicit-tag drops of the stock model,
# and runs ~2x faster via MTP speculative decoding (llama-swap entry).
DEFAULT_MODEL = "qwen3.6-27b-abliterated"


class LlmOutputError(RuntimeError):
    """The model returned a 200 with unusable content (truncated / not JSON)."""


NAI_SYSTEM = """You convert flat Danbooru tag lists into NovelAI V4.5 prompts. Output strict JSON:
{"base_prompt": "...", "scene_description": "...", "dialogue": "...", "characters": [{"name": "short label", "prompt": "..."}]}

The image model consumes:
- base_prompt: scene-level tags ONLY - subject-count tags, background/setting, lighting, art style, composition/camera tags (cowboy shot, from above, close-up...), and cross-character interaction.
- characters[]: one entry per visible character. The subject-count tags dictate how many entries: 1girl + 1boy = exactly two (one girl, one boy); 2girls = two girl entries; solo/1girl = one. UNNAMED characters still get their own entry - the prompt just starts "boy, " or "girl, " without a name. Each prompt begins with "girl, " or "boy, " (matching that character's sex), then the character's name tag copied verbatim if present, then ONLY that character's own appearance (hair, eyes, skin), clothing/accessories, expression and personal pose.

Rules:
1. Use ONLY information in the input tags. Never invent characters, clothing, colours, objects or scenery. Copy tags verbatim - never append a series/parenthetical that is not in the input.
2. DROP entirely (put them NOWHERE): artist/author handles (the leading tags that are a person's username, not a visual property or character name) and all meta tags - artist name, fanbox/patreon/twitter/pixiv/weibo username, web address, watermark, signature, dated, logo, commentary, commentary request, translation request, translated, bad id, highres, absurdres, lowres, huge filesize, revision, paid reward available.
3. Subject-count tags (1girl, 1boy, 2girls, 3girls, 2boys, 1other, multiple girls...) ALWAYS go in base_prompt, never in a character prompt. Keep EVERY count tag that is in the input - a 1girl + 1boy image keeps both "1girl, 1boy" in base_prompt.
4. Each remaining tag lands in exactly one place. Attribute character tags using name tags + canonical knowledge of those characters (hair/eye colours, signature outfits); paired attributes split one per character. Never give the same tag to two characters.
5. CHARACTER-VISUAL tags - clothing, outfits, underwear, accessories, expressions, appearance and body features - must NEVER be left in base_prompt when the image has more than one character: base tags get applied to EVERY character by the image model, so a stray outfit tag in base dresses all characters in the same merged mush. If you cannot tell whose clothing/visual tag it is, assign it to the single most plausible character anyway - a reasoned one-character guess always beats contaminating everyone. base_prompt may only contain scene-level content: count tags, setting/background, lighting, art style, composition/camera, and the interaction. (Single-character images: clothing still goes in the character's prompt, not base.)
6. Do NOT add quality tags (masterpiece, best quality...). Tags stay lowercase, comma-separated, space form.

scene_description (mode NATURAL only): 1-3 concise sentences, each ending in '.', describing spatial arrangement, poses, gaze and who-does-what interaction - the things flat tags express poorly. Remove from base_prompt the tags those sentences fully replace. Mode SPLIT: "".

dialogue (only when SPEECH is ON): ART-DIRECT the in-image text, do not just quote a line. The image model reads plain English here, so DESCRIBE how the text should look and where it sits, then give the words in double quotes. Booru tags do not control text appearance - write natural language.

When SPEECH is ON, dialogue is NEVER empty - always write something, however short. A crowded many-character scene does not need everyone voiced: pick the one or two whose line carries the moment (or a single sound effect over the whole image) and write only that.

Build the dialogue string as comma-separated pieces, in this order:
1. APPEARANCE - describe the lettering itself. Any of: size (large text, small text, huge bold text), colour (red text, light pink text, glowing white text), rendering (painted text, handwritten text, dripping text, cracked text, text without outline, thick black outline, glitchy text), shape (curved text, tilted text, text splitted in two, text framing face), and whether it is speech or noise (sound effect text). Colour and one or two treatments is usually plenty.
2. WHERE THE TEXT GOES - obey the Text position setting in the request.
   ATTRIBUTED (the best default): do not place the text by coordinates at all - ATTRIBUTE it to whoever makes it, and the image model puts it beside them automatically. Write it as a short clause around the quote: she says "abc", the girl on the left whispers "abc", he shouts "STOP!". This works for non-speech too: a soft "mmm" hanging in the air around her body, "SLAP" printed across her thigh, small "thump thump" over his chest. Name the character the way the scene identifies them (she / he / the girl on the left / the taller one) so the right person gets the text.
   PLACED: position it roughly on the frame instead - text on top left, text on top right, text across the middle, text in background, text behind character, text next to her face, text over her chest.
   FREE: give no placement and no attribution - just the appearance words and the quoted line, and let the image model decide where it lands. This sometimes puts text somewhere better than you would have chosen.
3. BUBBLE CONTROL - obey the Bubble setting in the request. AUTO: you choose - conversational lines usually want "speech bubble", big impact/sound-effect text almost always wants it suppressed with -1::speech bubble:: . ON: always include "speech bubble". OFF: always suppress it with -1::speech bubble:: and let the words sit as bare lettering.
4. DECORATION (optional, sparing) - hearts, tiny hearts, sparkles, motion lines, small stars floating around the text.
5. THE WORDS - MANDATORY. Every dialogue you write MUST contain at least one double-quoted string. Write it as text "LIKE THIS" or just "LIKE THIS". Styling directions with no quoted words are INVALID and will be thrown away - the image model would render invented glyphs. For several separate blocks, write each placement immediately before its own quoted string so each line is bound to its position.

Worked examples of the range:
- ATTRIBUTED, minimal (the common case): light pink text, speech bubble, she says "It got bigger again..."
- ATTRIBUTED, sound rather than speech: small breathy pink text, -1::speech bubble::, a soft "mmm" hanging around her body
- FREE, minimal: light pink text "It got bigger again..."
- PLACED, full comic treatment: large text, sound effect text, text splitted in two, curved text, text without outline, text in background, text behind character, text framing face, painted text, red text, tilted text, hearts, -1::speech bubble::, text on top left, text "YOU ARE DEAD", text on top right, "AFTER THIS!"

MATCH THE IMAGE. The styling is direction for THIS picture, not decoration bolted on: a loud action or horror scene earns big painted impact lettering; a quiet intimate moment wants small soft-coloured text in a bubble; a comedic beat suits handwritten text with a sweatdrop or hearts. Take the cue from the mood, the rating, the action and the colours already in the tags - e.g. pink or red lettering for an intimate scene, harsh white or blood-red for horror.

Scale the treatment to how loud the picture is, and VARY IT - "small text in a speech bubble" is not a default, it is one option among many. Pick the register from the scene:
- quiet / intimate / tender: small soft-coloured text in a bubble, maybe a heart. e.g. light pink text, speech bubble, "It got bigger again..."
- comedic: handwritten text, a sweatdrop or hearts, bubble. e.g. handwritten text, spoken sweatdrop, speech bubble, "...eh?"
- shouting / impact / action: large bold text, sound effect text, suppress the bubble. e.g. large text, sound effect text, painted red text, tilted text, -1::speech bubble::, "STOP!"
- horror / dread: cracked or dripping blood-red lettering, thick black outline, no bubble, often placed. e.g. large dripping blood-red text, thick black outline, -1::speech bubble::, text across the middle, "DON'T LOOK BACK"
- koma / comic layout or a title card: this is where the full treatment above earns its keep - go big, place each block, bind one line per panel.

Anything mid-range takes three or four pieces. Never stack contradictory directions (text in background AND text framing face), and never let the styling outweigh what the scene deserves - overdressed text on a quiet image reads as noise, but timid text on a loud one wastes the shot.

The WORDS themselves should sound like the character in that moment - short, natural, in character. If the tags describe a comic or Nkoma layout you may write one line per panel.

When SPEECH is OFF: dialogue MUST be "" and you must DROP every text tag (speech bubble, thought bubble, english/japanese/chinese/korean text, comic, Nkoma, spoken ...) so the image contains no text.

Background INVENT mode (when the prompt says Background: INVENT): if the input's background is plain (white background, simple background, grey background, gradient background, transparent background) or there are no setting tags at all, REPLACE those plain-background tags with an invented setting that fits the other tags - the outfit, mood, props, season and rating should drive the choice (school uniform -> classroom or schoolyard; bikini -> beach; armor -> battlefield). Register: descriptive but restrained, like "sitting on a chair on the seawall, fishing toward the ocean, the ocean in the foreground". Put the invented setting tags (3 to 7: place, one or two objects, a lighting cue, optionally a depth cue like foreground/bokeh) in the background_tags field, plus at most ONE setting sentence in scene_description when the mode is NATURAL. Never an exhaustive list. If the image already has a real background, change nothing and leave background_tags empty.

Background ENRICH mode (when the prompt says Background: ENRICH): keep every existing background/setting tag and put 2 to 5 NEW concrete setting tags that plausibly belong to that place (secondary objects, lighting, weather/time-of-day, depth cues) in the background_tags field; in NATURAL mode you may additionally extend the setting description by one short sentence. Same restraint: enough to ground the scene, never clutter. If both INVENT and ENRICH are requested, invent when the background is plain, otherwise enrich.

Invented and enriched backgrounds must be UNPOPULATED: never add pedestrians, crowds, bystanders, background characters or any other people - only the subject characters appear in the image.

Invented and enriched backgrounds must also be CLEAN and legible: the image model renders dense clutter as unrecognizable artifacts. Never use clutter descriptors (cluttered, messy, scattered, strewn, piles, crowded, busy) and never stack many small props. Prefer a FEW large, clearly readable elements - simple geometry, tidy surfaces, one clear light source, one or two anchor objects at most.

The background_tags field: comma-separated tags ONLY when Background mode is INVENT or ENRICH as described above; otherwise it MUST be an empty string.

Identity STRIP mode (when the prompt says Identity: STRIP): the user will substitute their OWN characters into this scene, so the output must be character-agnostic. Character prompts keep ONLY transferable elements - clothing, accessories, expression, personal pose/action - and still start with "girl, " or "boy, ". OMIT character name tags, series/copyright tags, and every innate physical trait: hair colour/length/style (blonde hair, long hair, twintails, ahoge, bangs...), eye colour/shape, pupil shape, skin tone, breast/body size, height/age descriptors, facial marks, and species features (animal ears, horns, tails, wings, fangs, pointy ears) - unless a species feature is explicitly a costume piece (fake animal ears stay). The same applies to base_prompt and scene_description: no names, no series, no innate traits; refer to characters generically (the girl, the boy). Identity: KEEP means normal behavior."""

NAI_COMPOSE_SYSTEM = """You author NovelAI V4.5 image prompts from a user's idea. NAI V4.5 understands Danbooru tags AND natural language: the strongest prompts mix both - concrete visual attributes as comma-separated tags, and spatial arrangement / story / interaction as short sentences ending in '.'. Output strict JSON:
{"base_prompt": "...", "scene_description": "...", "dialogue": "...", "characters": [{"name": "short label", "prompt": "..."}]}

- base_prompt: subject-count tags first (1girl, 2girls, 1boy...), then scene tags: setting/background, lighting, art style, composition/camera. For comics use the koma tags (2koma, 3koma, 4koma) plus "comic". Keep it tags-only; the sentences live in scene_description.
- scene_description: 1-4 concise sentences (each ending '.') that direct the image: panel-by-panel content for komas ("First panel: ... Second panel: ..."), spatial layout, who does what, expressions changing between panels.
- dialogue: the in-image text, ART-DIRECTED in plain English rather than just quoted. Comma-separated pieces: appearance first (size, colour, rendering, shape - e.g. large text, painted red text, curved text, sound effect text, text without outline), then optional placement (text on top left, text in background, text behind character), then bubble control ("speech bubble" to have one, or -1::speech bubble:: to suppress it for bare impact lettering), then optional sparing decoration (hearts, sparkles), then the words as text "LIKE THIS" or just "LIKE THIS". Bind each placement to its own quoted string when there are several blocks; for komas say which panel each line belongs to. Match the styling to the idea's mood - loud painted lettering for impact, small soft text in a bubble for quiet moments - and keep most of them to one line with two or three appearance words. Empty string only if the idea needs no text.
- characters[]: one entry per character, starting "girl, " or "boy, ", then concrete appearance/clothing/expression tags you invent to fit the idea. Consistent characters across panels happen automatically - do not create separate entries per panel.

Rules:
1. Everything must be VISUALLY CONCRETE. Translate abstract ideas into things a picture can show (a "developer changing a model" becomes a person at a computer/control panel; "a style breaking" becomes a glitching canvas/painting). Use visual metaphor freely.
2. Do NOT add quality tags (masterpiece, best quality...) - the user appends their own.
3. Tags lowercase, comma-separated, space form. Danbooru vocabulary preferred where it exists.
4. Honor the requested character count and format (e.g. meme layouts, reaction formats) - adapt classic meme compositions with koma layout + dialogue rather than naming the meme.
5. Keep the total concise: base under ~40 tags, each character under ~20 tags.
6. Backgrounds must be UNPOPULATED: never add pedestrians, crowds, bystanders or background characters - only the subject characters appear in the image.
7. Backgrounds must be CLEAN and legible: no clutter descriptors (cluttered, messy, scattered, piles, busy), no stacks of small props - a few large clear elements with one light source render best.
8. background_tags MUST be an empty string in this mode - put setting tags directly in base_prompt."""

_DIALOGUE_REPAIR_SYSTEM = """You write the in-image text for a NovelAI image prompt. You are given a scene that has already been described; return ONLY the dialogue field.

ART-DIRECT it in plain English: comma-separated appearance words (size, colour, rendering, shape - e.g. large text, light pink text, painted text, curved text, sound effect text), then bubble control, then optional sparing decoration (hearts, sparkles), then the words in double quotes.

Obey the two settings you are given:
- Bubble AUTO = you choose (conversational wants "speech bubble", impact wants it suppressed); ON = always include "speech bubble"; OFF = always suppress with -1::speech bubble:: .
- Text position ATTRIBUTED = do not place the text on the frame; attribute it to whoever makes it so it lands beside them - she says "abc", he shouts "STOP!", a soft "mmm" hanging around her body, "SLAP" printed across her thigh. PLACED = position it roughly instead (text on top left, text across the middle, text next to her face). FREE = no placement and no attribution, just appearance words and the quoted line.

Your answer MUST contain at least one double-quoted string - styling with no words is invalid.

Match the register to the scene: quiet or intimate wants small soft-coloured text in a bubble; shouting, impact or horror wants large painted lettering with the bubble suppressed; comedic suits handwritten text with a sweatdrop or hearts. Keep quiet scenes to a colour and one treatment word. Output strict JSON: {"dialogue": "..."}"""

_DIALOGUE_SCHEMA = {
    "type": "object",
    "required": ["dialogue"],
    "properties": {"dialogue": {"type": "string"}},
}

_SPLIT_SCHEMA = {
    "type": "object",
    "required": [
        "base_prompt",
        "scene_description",
        "dialogue",
        "background_tags",
        "characters",
    ],
    "properties": {
        "base_prompt": {"type": "string"},
        "scene_description": {"type": "string"},
        "dialogue": {"type": "string"},
        "background_tags": {"type": "string"},
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "prompt"],
                "properties": {
                    "name": {"type": "string"},
                    "prompt": {"type": "string"},
                },
            },
        },
    },
}

# Meta / attribution tags always dropped (space form, lowercase).
_META_DROP = {
    "artist name", "fanbox username", "patreon username", "twitter username",
    "x username", "pixiv username", "weibo username", "instagram username",
    "deviantart username", "tumblr username", "bluesky username", "gumroad username",
    "ko-fi username", "picarto username", "username", "web address", "watermark",
    "artist logo", "logo", "signature", "dated", "stamp", "artist request",
    "character request", "source request", "commentary", "commentary request",
    "english commentary", "korean commentary", "chinese commentary",
    "japanese commentary", "translation request", "translated", "check translation",
    "hard-translated", "partially translated", "bad id", "bad pixiv id",
    "bad twitter id", "bad link", "md5 mismatch", "resolution mismatch", "highres",
    "absurdres", "incredibly absurdres", "lowres", "huge filesize", "scan",
    "revision", "revised", "paid reward available", "third-party edit",
    "sample watermark", "third-party watermark",
}

# Text / speech tags dropped only when speech is OFF.
_TEXT_DROP = {
    "speech bubble", "thought bubble", "spoken heart", "spoken ellipsis",
    "spoken question mark", "spoken exclamation mark", "spoken musical note",
    "spoken blush", "spoken anger vein", "spoken interrobang", "spoken squiggle",
    "spoken sweatdrop", "spoken star", "text", "text focus", "english text",
    "japanese text", "chinese text", "korean text", "russian text", "french text",
    "german text", "spanish text", "thai text", "engrish text", "garbled text",
    "fake text", "copyright name", "character name", "song name", "company name",
    "comic", "2koma", "3koma", "4koma", "5koma", "6koma", "silent comic",
    "greyscale comic", "sound effects", "onomatopoeia", "dialogue",
}

_COUNT_RE = re.compile(r"^\d+\+?(?:girl|boy|other)s?$")
_MULTI_WORD_COUNTS = {"multiple girls", "multiple boys", "multiple others"}

# Backstop patterns for identity-strip mode: innate traits the LLM may echo.
# Exact-tag anchored matches — accessories ("hair ribbon", "hair ornament"),
# expressions ("closed eyes") and costume pieces ("fake animal ears") must
# survive. The style/species/body vocab below is the measured leak set from
# the 2026-07 A/B eval (both 27Bs leak the same families: hairstyles, species
# features, body build).
_COLORS = (
    "aqua|blue|red|brown|green|yellow|purple|pink|orange|grey|gray|black|"
    "white|blonde|silver|golden|gold|amber|violet|lavender|crimson|platinum|"
    "light blue|light brown|dark blue|dark brown|multicolored|two-tone|"
    "gradient|streaked|colored"
)
_ID_STYLES = (
    "twintails|twin braids|ponytail|side ponytail|braid|braided ponytail|"
    "french braid|crown braid|single braid|low twintails|short twintails|"
    "ahoge|antenna hair|blunt bangs|swept bangs|parted bangs|"
    "asymmetrical bangs|crossed bangs|bangs|hair between eyes|sidelocks|"
    "hair bun|double bun|cone hair bun|single hair bun|bob cut|hime cut|"
    "pixie cut|buzz cut|undercut|drill hair|twin drills|wavy hair|curly hair|"
    "straight hair|messy hair|spiked hair|hair intakes|hair flaps|"
    "hair over one eye|asymmetrical hair|flipped hair|half updo|one side up|"
    "two side up|low ponytail|high ponytail|folded ponytail|"
    "short hair with long locks|colored inner hair|split-color hair|"
    "colored tips"
)
_ID_SPECIES = (
    "animal ears|cat ears|fox ears|dog ears|rabbit ears|bunny ears|"
    "horse ears|cow ears|mouse ears|bear ears|wolf ears|raccoon ears|"
    "tiger ears|animal ear fluff|cat tail|fox tail|dog tail|cow tail|"
    "dragon tail|demon tail|wolf tail|tiger tail|monkey tail|fish tail|"
    "tail|horns|horn|demon horns|dragon horns|curled horns|cow horns|"
    "goat horns|antlers|pointy ears|elf|dark elf|fangs|fang|skin fang|"
    "wings|angel wings|demon wings|dragon wings|feathered wings|"
    "butterfly wings|fairy wings|bat wings|halo|kemonomimi mode|mermaid|"
    "centaur|lamia"
)
_ID_BODY = (
    "mole|mole under eye|mole under mouth|mole on breast|mole on neck|"
    "freckles|scar|scar across eye|scar on face|scar on cheek|facial mark|"
    "whisker markings|forehead mark|tattoo|arm tattoo|back tattoo|tall|"
    "petite|loli|shota|mature female|mature male|old man|old woman|muscular|"
    "muscular female|muscular male|abs|thick thighs|wide hips|curvy|plump|"
    "skinny|toned|dark-skinned female|dark-skinned male|tanlines|"
    "veiny arms|thick eyebrows"
)
_IDENTITY_RE = re.compile(
    rf"^(?:(?:{_COLORS}) (?:hair|eyes|skin)"
    r"|(?:very )?(?:short|long|medium) hair|absurdly long hair"
    r"|(?:flat chest|small breasts|medium breasts|large breasts|huge breasts|gigantic breasts)"
    r"|.* pupils"
    rf"|{_ID_STYLES}"
    rf"|{_ID_SPECIES}"
    rf"|{_ID_BODY}"
    r"|dark skin|pale skin|tan|heterochromia)$"
)


def _strip_identity_backstop(text: str) -> str:
    return ", ".join(t for t in _tokens(text) if not _IDENTITY_RE.match(_canon(t)))


# Style/quality vocabulary that must never be smuggled in via the invented-
# background channel ("anime composition", "detailed artwork", ...) — NAI
# gets quality tags from the user, and style tags distort the render.
_BG_JUNK_RE = re.compile(
    r"\b(anime|style|composition|quality|masterpiece|aesthetic|detailed|"
    r"render|rendering|illustration|artwork|painting|4k|8k|hd|hdr|resolution)\b"
)

_CHAR_PREFIX = frozenset({"girl", "boy"})

# Styling directions with no quoted words would tell the image model to
# render text without saying what it says — it then invents glyphs. A
# runaway pile of directions before the first quote is the other failure
# mode, so the lead-in is capped (pieces are comma-separated and quote-free
# up to that point, so trimming there can never split a spoken line).
# The ceiling sits well above a legitimate full comic treatment, which runs
# to ~15 pieces — this only catches genuine runaway, and taste is the
# prompt's job, not the clamp's.
_MAX_LEAD_IN_PIECES = 24


_BUBBLE_RE = re.compile(r"speech\s+bubble", re.IGNORECASE)


def _apply_bubble_mode(text: str, bubble: str) -> str:
    """Force the user's speech-bubble choice onto the dialogue.

    'auto' leaves the model's judgement alone. 'on'/'off' are explicit
    user instructions, so they are enforced here rather than trusted to
    the prompt — any existing bubble piece is replaced by the wanted one.
    The piece that hugs the opening quote is left hugging, because
    `light pink text "X"` binds that descriptor to that string.
    """
    if bubble not in ("on", "off") or '"' not in text:
        return text
    lead, sep, rest = text.partition('"')
    want = "speech bubble" if bubble == "on" else "-1::speech bubble::"
    pieces = [p.strip() for p in lead.split(",") if p.strip()]
    pieces = [p for p in pieces if not _BUBBLE_RE.search(p)]
    # A trailing comma means every piece is separated from the quote; without
    # one the final piece qualifies the quoted string and must stay adjacent.
    hugging = bool(lead.strip()) and not lead.rstrip().endswith(",")
    if hugging:
        head, hug = pieces[:-1], pieces[-1]
        return f"{', '.join(head + [want, hug])} {sep}{rest}"
    return f"{', '.join(pieces + [want])}, {sep}{rest}"


def _repair_dialogue(
    model: str,
    base: str,
    scene: str,
    characters: list[dict[str, str]],
    bubble: str = "auto",
    text_position: str = "attributed",
) -> str:
    """Second, focused call for when the main split dropped the dialogue.

    At temperature 0.5 the model intermittently returns an empty dialogue,
    or styling with no quoted words (which is unusable and gets dropped).
    Re-asking for just this one field is short and cheap, and only happens
    on that minority of calls.
    """
    who = "; ".join(
        f"{c.get('name') or 'character'}: {c.get('prompt', '')[:110]}" for c in characters[:4]
    )
    user = (
        f"Scene: {base[:700]}\n"
        + (f"Action: {scene[:500]}\n" if scene else "")
        + (f"Characters: {who}\n" if who else "")
        + f"Bubble: {bubble.upper()}\nText position: {text_position.upper()}\n"
    )
    try:
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=5.0)) as c:
            r = c.post(
                f"{settings.LLAMA_SWAP_URL}/v1/chat/completions",
                json={
                    "model": model,
                    "temperature": 0.6,
                    "max_tokens": 200,
                    "chat_template_kwargs": {"enable_thinking": False},
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "dialogue", "schema": _DIALOGUE_SCHEMA},
                    },
                    "messages": [
                        {"role": "system", "content": _DIALOGUE_REPAIR_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                },
            )
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"]).get("dialogue", "")
    except Exception:
        # Best-effort: a failed repair must never fail the whole split.
        logger.warning("dialogue repair call failed", exc_info=True)
        return ""


def _clean_dialogue(text: str) -> str:
    text = " ".join(text.split())
    if '"' not in text:
        return ""
    lead, sep, rest = text.partition('"')
    pieces = [p.strip() for p in lead.split(",") if p.strip()]
    if len(pieces) <= _MAX_LEAD_IN_PIECES:
        # Punctuation here is meaningful — `light pink text "X"` binds the
        # descriptor to that string, while `..., text on top left, text "X"`
        # separates blocks. Never reformat output that is already sane.
        return text
    # Runaway: keep the piece that hugs the quote (it binds to the words)
    # and every weighted directive such as -1::speech bubble:: , since those
    # change what renders rather than merely decorating it. Fill the rest in
    # original order.
    last = pieces[-1]
    trailing = lead[lead.rfind(last) + len(last):]
    head_pool = pieces[:-1]
    weighted = [p for p in head_pool if "::" in p]
    plain = [p for p in head_pool if "::" not in p]
    budget = max(0, _MAX_LEAD_IN_PIECES - 1 - len(weighted))
    keep = set(weighted) | set(plain[:budget])
    head = ", ".join(p for p in head_pool if p in keep)
    return f"{head}, {last}{trailing}{sep}{rest}" if head else f"{last}{trailing}{sep}{rest}"


def _verbatim_backstop(text: str, allowed: set[str], extra: frozenset[str] = frozenset()) -> str:
    """Deterministic rule-1 enforcement: every output tag must exist in the
    input. The models occasionally invent tags ("anime composition") despite
    the verbatim instruction; anything not in the input is dropped. Invented
    background tags don't pass through here — they arrive via the dedicated
    background_tags channel."""
    return ", ".join(
        t for t in _tokens(text) if _canon(t) in allowed or _canon(t) in extra
    )


def _canon(tag: str) -> str:
    return tag.strip().lower().replace("_", " ")


def _tokens(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def _strip(text: str, drop: set[str]) -> str:
    return ", ".join(t for t in _tokens(text) if _canon(t) not in drop)


def _is_count(tag: str) -> bool:
    c = _canon(tag)
    return bool(_COUNT_RE.match(c)) or c in _MULTI_WORD_COUNTS


def _tag_categories(tokens: list[str]) -> dict[str, int]:
    """Booru tag categories from the Tag table, keyed by canonical form.

    0=general, 1=artist, 3=copyright, 4=character, 5=meta. Tags the table
    doesn't know return no entry (treated as general).
    """
    names = {_canon(t).replace(" ", "_") for t in tokens}
    if not names:
        return {}
    with db.session_scope() as s:
        rows = s.exec(select(Tag).where(Tag.name.in_(names))).all()  # type: ignore[attr-defined]
        return {r.name: r.category for r in rows}


def _prefilter_tags(tags: str, strip_identity: bool = False) -> tuple[str, list[str]]:
    """Drop artist/meta tags BEFORE the LLM sees them (it cannot reliably
    tell an artist handle from a character name — the Tag table can) and
    collect known character-name tags as attribution hints.

    With strip_identity, character (4) and copyright (3) tags are dropped
    outright — the LLM never sees who the scene originally featured."""
    tokens = _tokens(tags)
    cats = _tag_categories(tokens)
    kept: list[str] = []
    character_names: list[str] = []
    for t in tokens:
        cat = cats.get(_canon(t).replace(" ", "_"))
        if cat in (1, 5):  # artist / meta
            continue
        if _canon(t) in _META_DROP:
            continue
        if cat == 4:
            if strip_identity:
                continue
            character_names.append(_canon(t))
        elif cat == 3 and strip_identity:  # copyright / series
            continue
        kept.append(t)
    return ", ".join(kept), character_names


def _ensure_counts(base: str, input_tags: str) -> str:
    """Guarantee every subject-count tag from the input leads base_prompt."""
    have = {_canon(t) for t in _tokens(base)}
    missing = []
    for t in _tokens(input_tags):
        c = _canon(t)
        if _is_count(t) and c not in have:
            have.add(c)
            missing.append(c)
    if not missing:
        return base
    return ", ".join(missing + _tokens(base))


_TTL_RE = re.compile(r"^(\s*ttl:\s*)(\d+)\s*$", re.MULTILINE)


def get_ttl_minutes() -> int | None:
    """Idle-unload ttl from the llama-swap config, in minutes (0 = never).

    None when the config can't be read."""
    cfg = settings.LLAMA_SWAP_CONFIG
    try:
        m = _TTL_RE.search(cfg.read_text(encoding="utf-8"))
        return round(int(m.group(2)) / 60) if m else 0
    except OSError:
        return None


def set_ttl_minutes(minutes: int) -> dict[str, Any]:
    """Rewrite every model's ttl in the llama-swap config (0 = keep loaded).

    Text-level replace so the config's comments/format survive. The server
    runs with -watch-config, so the change applies live."""
    minutes = max(0, min(24 * 60, int(minutes)))
    cfg = settings.LLAMA_SWAP_CONFIG
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"cannot read llama-swap config: {exc}"}
    new_text, n = _TTL_RE.subn(rf"\g<1>{minutes * 60}", text)
    if n == 0:
        return {"ok": False, "error": "no ttl entries found in the llama-swap config"}
    try:
        # Atomic: llama-swap watches this file, so a torn write would be
        # hot-reloaded as a broken config.
        tmp = cfg.with_suffix(cfg.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, cfg)
    except OSError as exc:
        return {"ok": False, "error": f"cannot write llama-swap config: {exc}"}
    return {"ok": True, "ttl_minutes": minutes, "models_updated": n}


def server_status() -> dict[str, Any]:
    """Reachability + configured + currently loaded models."""
    base = settings.LLAMA_SWAP_URL
    try:
        with httpx.Client(timeout=3) as c:
            models = [
                m["id"] for m in c.get(f"{base}/v1/models").json().get("data", [])
            ]
            running = [
                {"model": r.get("model"), "state": r.get("state")}
                for r in c.get(f"{base}/running").json().get("running", [])
            ]
        return {
            "up": True,
            "models": models,
            "running": running,
            "default_model": DEFAULT_MODEL if DEFAULT_MODEL in models else (models[0] if models else None),
            "ttl_minutes": get_ttl_minutes(),
        }
    except Exception:
        return {
            "up": False,
            "models": [],
            "running": [],
            "default_model": None,
            "ttl_minutes": get_ttl_minutes(),
        }


def start_server() -> dict[str, Any]:
    """Launch the llama-swap bat (detached) and wait for /health."""
    status = server_status()
    if status["up"]:
        return {"started": False, "already_up": True, **status}
    bat = settings.LLAMA_SWAP_START_BAT
    if not bat.is_file():
        return {
            "started": False,
            "already_up": False,
            "error": f"start script not found: {bat}",
            **status,
        }
    subprocess.Popen(  # noqa: S603 — launching the user's own configured script
        ["cmd", "/c", str(bat)],
        cwd=str(bat.parent),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        time.sleep(1)
        status = server_status()
        if status["up"]:
            return {"started": True, "already_up": False, **status}
    return {
        "started": False,
        "already_up": False,
        "error": "server did not come up within 15s",
        **status,
    }


def unload_models() -> dict[str, Any]:
    """Free VRAM immediately (llama-swap reloads on the next request)."""
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(f"{settings.LLAMA_SWAP_URL}/api/models/unload")
        if r.status_code >= 400:
            return {"ok": False, "error": f"llama-swap returned {r.status_code}: {r.text[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **server_status()}


def nai_split(
    tags: str,
    mode: str,
    model: str | None = None,
    include_speech: bool = False,
    strip_identity: bool = False,
    invent_background: bool = False,
    enrich_background: bool = False,
    bubble: str = "auto",
    text_position: str = "attributed",
) -> dict[str, Any]:
    """Split a flat tag list into NAI V4.5 base + character prompts.

    mode 'split' redistributes tags; 'natural' additionally rewrites the
    scene arrangement/interaction as sentences. include_speech has the model
    author in-image dialogue (and keeps text tags). A deterministic pass then
    drops artist/meta (and, without speech, text) tags and guarantees the
    input's subject-count tags survive in the base prompt.

    Blocking — the first request after idle loads the model (tens of
    seconds), so callers should use a generous timeout.
    """
    model = model or DEFAULT_MODEL
    t0 = time.time()
    filtered_tags, character_names = _prefilter_tags(tags, strip_identity)
    if not filtered_tags:
        filtered_tags = tags
    hint = (
        f"Known character name tags: {', '.join(character_names)}. "
        "Characters beyond these (per the count tags) are unnamed but still get their own entry.\n"
        if character_names and not strip_identity
        else ""
    )
    background = (
        "INVENT+ENRICH"
        if invent_background and enrich_background
        else "INVENT"
        if invent_background
        else "ENRICH"
        if enrich_background
        else "KEEP"
    )
    speech_cfg = (
        f"Bubble: {bubble.upper()}\nText position: {text_position.upper()}\n"
        if include_speech
        else ""
    )
    user = (
        f"Mode: {mode.upper()}\nSpeech: {'ON' if include_speech else 'OFF'}\n"
        f"{speech_cfg}"
        f"Identity: {'STRIP' if strip_identity else 'KEEP'}\n"
        f"Background: {background}\n"
        f"{hint}\nTags:\n{filtered_tags}"
    )
    try:
        with httpx.Client(timeout=httpx.Timeout(420.0, connect=5.0)) as c:
            r = c.post(
                f"{settings.LLAMA_SWAP_URL}/v1/chat/completions",
                json={
                    "model": model,
                    "temperature": 0.5 if include_speech else 0.15,
                    "max_tokens": 2048,
                    "chat_template_kwargs": {"enable_thinking": False},
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "nai_prompt", "schema": _SPLIT_SCHEMA},
                    },
                    "messages": [
                        {"role": "system", "content": NAI_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                },
            )
    except httpx.HTTPError:
        raise  # mapped to HTTP status codes in the route layer
    r.raise_for_status()
    body = r.json()
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError):
        raise LlmOutputError("unexpected response shape from the LLM backend")
    if choice.get("finish_reason") == "length":
        raise LlmOutputError(
            "the model hit the output length limit — the tag list is too long to split; "
            "trim it and try again"
        )
    try:
        out = json.loads(choice["message"]["content"])
    except (json.JSONDecodeError, KeyError, TypeError):
        raise LlmOutputError("the model did not return valid JSON")

    drop = set(_META_DROP)
    if not include_speech:
        drop |= _TEXT_DROP

    allowed = {_canon(t) for t in _tokens(tags)}
    base_tags = _verbatim_backstop(
        _ensure_counts(_strip(out.get("base_prompt", ""), drop), tags), allowed
    )
    # Merge the schema-enforced background additions (invent/enrich modes)
    # into base, deduped against what's already there. Clutter descriptors
    # and style/quality junk are stripped outright — NAI renders clutter as
    # artifacts, and style tags don't belong in an invented setting.
    if invent_background or enrich_background:
        clutter = ("clutter", "messy", "scattered", "strewn", "pile", "crowded", "busy")
        bg_extra = [
            t
            for t in _tokens(out.get("background_tags", ""))
            if _canon(t) not in {_canon(x) for x in _tokens(base_tags)}
            and not any(c in _canon(t) for c in clutter)
            and not _BG_JUNK_RE.search(_canon(t))
        ]
        if bg_extra:
            base_tags = f"{base_tags}, {', '.join(bg_extra)}" if base_tags else ", ".join(bg_extra)
    scene = out.get("scene_description", "").strip() if mode == "natural" else ""
    dialogue = _clean_dialogue(out.get("dialogue", "")) if include_speech else ""
    characters = [
        {
            "name": ch.get("name", ""),
            "prompt": _verbatim_backstop(
                _strip(ch.get("prompt", ""), drop), allowed, _CHAR_PREFIX
            ),
        }
        for ch in out.get("characters", [])
        if ch.get("prompt", "").strip()
    ]
    if strip_identity:
        base_tags = _strip_identity_backstop(base_tags)
        characters = [
            {"name": "", "prompt": _strip_identity_backstop(ch["prompt"])}
            for ch in characters
        ]

    # Speech was asked for but the model dropped it (empty, or styling with
    # no words). Re-ask for just this field rather than shipping no text.
    if include_speech and not dialogue:
        dialogue = _clean_dialogue(
            _repair_dialogue(model, base_tags, scene, characters, bubble, text_position)
        )
    if include_speech and dialogue:
        dialogue = _apply_bubble_mode(dialogue, bubble)

    tail = " ".join(x for x in (scene, dialogue) if x)
    base_full = f"{base_tags}, {tail}" if tail else base_tags

    return {
        "base_prompt": base_full,
        "base_tags": base_tags,
        "scene_description": scene,
        "dialogue": dialogue,
        "characters": characters,
        "model": model,
        "mode": mode,
        "include_speech": include_speech,
        "strip_identity": strip_identity,
        "invent_background": invent_background,
        "enrich_background": enrich_background,
        "secs": round(time.time() - t0, 1),
    }


def nai_compose(idea: str, model: str | None = None) -> dict[str, Any]:
    """Author a full NAI V4.5 prompt (base + characters + dialogue) from a
    free-text idea — memes, koma comics, anything. Creative temperature."""
    model = model or DEFAULT_MODEL
    t0 = time.time()
    with httpx.Client(timeout=httpx.Timeout(420.0, connect=5.0)) as c:
        r = c.post(
            f"{settings.LLAMA_SWAP_URL}/v1/chat/completions",
            json={
                "model": model,
                "temperature": 0.8,
                "max_tokens": 2048,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "nai_prompt", "schema": _SPLIT_SCHEMA},
                },
                "messages": [
                    {"role": "system", "content": NAI_COMPOSE_SYSTEM},
                    {"role": "user", "content": f"Idea:\n{idea}"},
                ],
            },
        )
    r.raise_for_status()
    body = r.json()
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError):
        raise LlmOutputError("unexpected response shape from the LLM backend")
    if choice.get("finish_reason") == "length":
        raise LlmOutputError("the model hit the output length limit — try a shorter idea")
    try:
        out = json.loads(choice["message"]["content"])
    except (json.JSONDecodeError, KeyError, TypeError):
        raise LlmOutputError("the model did not return valid JSON")

    base_tags = out.get("base_prompt", "").strip().rstrip(",")
    scene = out.get("scene_description", "").strip()
    dialogue = out.get("dialogue", "").strip()
    tail = " ".join(x for x in (scene, dialogue) if x)
    return {
        "base_prompt": f"{base_tags}, {tail}" if tail else base_tags,
        "base_tags": base_tags,
        "scene_description": scene,
        "dialogue": dialogue,
        "characters": [
            {"name": ch.get("name", ""), "prompt": ch.get("prompt", "")}
            for ch in out.get("characters", [])
            if ch.get("prompt", "").strip()
        ],
        "model": model,
        "mode": "compose",
        "include_speech": bool(dialogue),
        "strip_identity": False,
        "secs": round(time.time() - t0, 1),
    }

"""Interactive prompt builder endpoints."""

from __future__ import annotations

import random
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from .. import db, settings
from ..ingest.scene_exclude import filter_scene_line_text
from ..models import Image, SceneLine, Source


router = APIRouter()

# Buckets a coherent scene roll must cover; composition/extras are sparse in
# the corpus and come along only when the picked image has them.
CORE_BUCKETS = ("outfit", "pose", "expression", "background")


class RollIn(BaseModel):
    buckets: list[str] = Field(
        default_factory=lambda: ["outfit", "pose", "expression", "background"]
    )
    locked: dict[str, str] = Field(default_factory=dict)  # bucket -> exact tag_text to keep
    source_ids: list[int] = Field(default_factory=list)
    origin: Optional[str] = None  # 'local' | 'booru' | None
    ratings: list[str] = Field(default_factory=list)
    nai_models: list[str] = Field(default_factory=list)
    score_min: Optional[int] = None
    # Only draw from images whose raw prompt has multi-character count tags.
    multi_char: bool = False
    # Exclusive subject-count filter. '' = off; otherwise one of:
    # solo | 1girl_1boy | 2girls | 3plus_girls | multi
    subjects: str = ""
    # All unlocked buckets from ONE image, so the combo is guaranteed coherent.
    coherent: bool = False
    # Probability limiter for e.g. plain backgrounds: a roll whose background
    # contains any cap tag is only accepted cap_percent% of the time.
    cap_tags: list[str] = Field(default_factory=list)
    cap_percent: int = Field(default=100, ge=0, le=100)
    # Tags stripped from every rolled bucket text (not from locked text).
    exclude_tags: list[str] = Field(default_factory=list)
    # Only roll from images whose raw prompt contains ALL of these tags
    # (whole-tag match, "beach" won't match "beach_house").
    require_tags: list[str] = Field(default_factory=list)
    # Batch size: the candidate scan dominates roll cost, so rolling N scenes
    # in one request is ~as cheap as one. The frontend prefetch pool uses this.
    count: int = Field(default=1, ge=1, le=10)


def _canon(tag: str) -> str:
    # SceneLine text is stored space-form; users paste Danbooru underscore
    # form — compare both in one canonical shape.
    return tag.strip().lower().replace("_", " ")


def _norm_tags(tags: list[str]) -> set[str]:
    return {c for c in (_canon(t) for t in tags) if c}


def _tokens(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def _contains_any(text: str, tags: set[str]) -> bool:
    return any(_canon(t) in tags for t in _tokens(text))


def _strip_tags(text: str, excl: set[str]) -> str:
    if not excl:
        return text
    return ", ".join(t for t in _tokens(text) if _canon(t) not in excl)


def _tag_boundary_like(t: str):
    """Whole-tag LIKE against a comma-normalized raw_prompt.

    Substring contains() is wrong for subject filters — 'solo' would match
    'solo_focus'. Wrap the prompt in commas, collapse ', ' to ',', and match
    ',tag,' with LIKE wildcards escaped.
    """
    from sqlalchemy import literal

    wrapped = (
        literal(",")
        .concat(func.replace(Image.raw_prompt, ", ", ","))
        .concat(literal(","))
    )
    escaped = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return wrapped.like(f"%,{escaped},%", escape="\\")


def _has(t: str):
    return _tag_boundary_like(t)


def _hasnt(t: str):
    return ~_tag_boundary_like(t)


def _subjects_clause(key: str):
    """Exclusive subject-count conditions on raw_prompt.

    'Exclusively 2girls' means the count tags say two girls and nobody
    else — co-implied tags (multiple_girls with 2girls) stay allowed.
    """
    if key == "solo":
        # Danbooru tags POV shots solo + 1boy (one character *depicted*) —
        # exclude the other counts for genuinely-alone girls.
        return and_(
            _has("1girl"),
            _has("solo"),
            *[_hasnt(t) for t in ("1boy", "1other", "2girls", "multiple_girls")],
        )
    if key == "1girl_1boy":
        return and_(
            _has("1girl"),
            _has("1boy"),
            *[_hasnt(t) for t in ("2girls", "3girls", "2boys", "3boys",
                                   "multiple_girls", "multiple_boys", "2others")],
        )
    if key == "2girls":
        return and_(
            _has("2girls"),
            *[_hasnt(t) for t in ("1boy", "2boys", "3girls", "4girls", "5girls",
                                   "6+girls", "1other", "multiple_boys")],
        )
    if key == "3plus_girls":
        return and_(
            or_(*[_has(t) for t in ("3girls", "4girls", "5girls", "6+girls")]),
            *[_hasnt(t) for t in ("1boy", "2boys", "multiple_boys", "1other")],
        )
    if key == "multi":
        return or_(
            *[_has(t) for t in settings.MULTI_CHAR_TAGS],
            *[and_(_has(a), _has(b)) for a, b in settings.MULTI_CHAR_PAIRS],
        )
    return None


def _needs_image_join(body: RollIn, origin_kinds) -> bool:
    return bool(
        body.source_ids
        or body.ratings
        or body.nai_models
        or body.score_min is not None
        or body.multi_char
        or body.subjects
        or body.require_tags
        or origin_kinds
    )


def _apply_image_filters(q, body: RollIn, origin_kinds):
    if origin_kinds:
        q = q.join(Source, Source.id == Image.source_id).where(
            Source.kind.in_(origin_kinds)  # type: ignore[arg-type]
        )
    if body.source_ids:
        q = q.where(Image.source_id.in_(body.source_ids))  # type: ignore[arg-type]
    if body.ratings:
        q = q.where(Image.rating.in_(body.ratings))  # type: ignore[arg-type]
    if body.nai_models:
        q = q.where(Image.nai_model.in_(body.nai_models))  # type: ignore[arg-type]
    if body.score_min is not None:
        q = q.where(Image.score >= body.score_min)
    # Raw prompts store underscore-form tags; accept either form from the UI.
    for t in body.require_tags:
        canon = t.strip().lower().replace(" ", "_")
        if canon:
            q = q.where(_has(canon))
    clause = _subjects_clause(body.subjects or ("multi" if body.multi_char else ""))
    if clause is not None:
        q = q.where(clause)
    return q


def _roll_independent(
    s: Session, body: RollIn, origin_kinds, caps: set[str], tickets: list[bool], excl: set[str]
) -> list[dict[str, str]]:
    """Roll len(tickets) independent bucket combos.

    One random-sample query per bucket serves the whole batch (the scan is
    the cost; extra rows are nearly free), consumed across rolls so a batch
    doesn't repeat lines.
    """
    n = len(tickets)
    outs: list[dict[str, str]] = [dict() for _ in range(n)]
    for bucket in body.buckets:
        if bucket in body.locked:
            for out in outs:
                out[bucket] = body.locked[bucket]
            continue

        q = select(SceneLine).where(SceneLine.bucket == bucket)
        if _needs_image_join(body, origin_kinds):
            q = q.join(Image, Image.id == SceneLine.image_id)
            q = _apply_image_filters(q, body, origin_kinds)
        rows = iter(s.exec(q.order_by(func.random()).limit(12 * n)).all())

        for out, allow_capped in zip(outs, tickets):
            # Try a few random rows until we get a line that still has tags
            # after stripping. For the background bucket, candidates matching
            # the cap tags are only accepted when this roll drew the
            # allow_capped ticket.
            picked = ""
            capped_fallback = ""
            for _ in range(12):
                row = next(rows, None)
                if row is None or not row.tag_text:
                    break
                # Strip exclusions BEFORE acceptance: a line that is entirely
                # excluded tags must count as empty so the retry budget finds
                # a surviving alternative, and the cap must judge the text
                # that will actually be returned.
                filtered = _strip_tags(
                    filter_scene_line_text(row.tag_text, use_scene_heuristics=True),
                    excl,
                )
                if not filtered:
                    continue
                if (
                    bucket == "background"
                    and caps
                    and not allow_capped
                    and _contains_any(filtered, caps)
                ):
                    capped_fallback = capped_fallback or filtered
                    continue
                picked = filtered
                break
            # Better a plain background than an empty one if the corpus
            # offered nothing else within the retry budget.
            out[bucket] = picked or capped_fallback
    return outs


def _roll_coherent(
    s: Session, body: RollIn, origin_kinds, caps: set[str], tickets: list[bool], excl: set[str]
) -> list[tuple[dict[str, str], Optional[Image]]]:
    """Roll up to len(tickets) coherent scenes, each from a distinct image.

    The candidate GROUP BY scan dominates the cost and is shared by the
    whole batch. If the corpus can't fill the batch, fewer (at least one,
    possibly empty like the single-roll behavior) results come back.
    """
    n = len(tickets)
    wanted = [b for b in body.buckets if b not in body.locked]
    locked_out = {b: body.locked[b] for b in body.buckets if b in body.locked}
    if not wanted:
        return [(dict(locked_out), None)]

    core = [b for b in wanted if b in CORE_BUCKETS] or wanted
    q = select(SceneLine.image_id).where(SceneLine.bucket.in_(core))  # type: ignore[arg-type]
    # Only join when an image-side filter exists: the unfiltered case then
    # runs entirely inside the (bucket, image_id) covering index instead of
    # probing the image table 2M times.
    if _needs_image_join(body, origin_kinds):
        q = q.join(Image, Image.id == SceneLine.image_id)
        q = _apply_image_filters(q, body, origin_kinds)
    q = (
        q.group_by(SceneLine.image_id)  # type: ignore[arg-type]
        .having(func.count(func.distinct(SceneLine.bucket)) == len(core))
        .order_by(func.random())
        .limit(30 * n)
    )
    candidate_ids = list(s.exec(q).all())

    # Memoized: cap-rejected candidates get revisited by later batch slots.
    _lines_cache: dict[int, dict[str, str]] = {}

    def lines_for(image_id: int) -> dict[str, str]:
        if image_id in _lines_cache:
            return _lines_cache[image_id]
        rows = s.exec(
            select(SceneLine).where(
                SceneLine.image_id == image_id,
                SceneLine.bucket.in_(wanted),  # type: ignore[arg-type]
            )
        ).all()
        vals: dict[str, str] = {}
        for r in rows:
            # Exclusions applied up front so the core-coverage check and the
            # cap both judge the text that will actually be returned.
            filtered = _strip_tags(
                filter_scene_line_text(r.tag_text or "", use_scene_heuristics=True),
                excl,
            )
            if filtered:
                vals[r.bucket] = filtered
        _lines_cache[image_id] = vals
        return vals

    results: list[tuple[dict[str, str], Optional[Image]]] = []
    used: set[int] = set()
    for allow_capped in tickets:
        picked_id: Optional[int] = None
        picked_vals: dict[str, str] = {}
        fallback: tuple[Optional[int], dict[str, str]] = (None, {})
        for image_id in candidate_ids:
            if image_id in used:
                continue
            vals = lines_for(image_id)
            if any(b not in vals for b in core):
                used.add(image_id)  # unusable for every slot — never retry
                continue  # filtering emptied a required bucket
            bg = vals.get("background", "")
            if caps and not allow_capped and bg and _contains_any(bg, caps):
                if fallback[0] is None:
                    fallback = (image_id, vals)
                continue
            picked_id, picked_vals = image_id, vals
            break
        if picked_id is None:
            picked_id, picked_vals = fallback
        if picked_id is None:
            # Candidates exhausted — emit the single-roll "not found" shape
            # once (all-empty buckets, no image) and stop; later slots would
            # only repeat it.
            if not results:
                out = dict(locked_out)
                for b in wanted:
                    out[b] = ""
                results.append((out, None))
            break
        used.add(picked_id)
        out = dict(locked_out)
        for b in wanted:
            out[b] = picked_vals.get(b, "")
        results.append((out, s.get(Image, picked_id)))
    return results


def _image_info(s: Session, image: Optional[Image]) -> Optional[dict[str, Any]]:
    if image is None:
        return None
    src = s.get(Source, image.source_id)
    return {
        "id": image.id,
        "external_id": image.external_id,
        "rating": image.rating,
        "score": image.score,
        # e.g. "2girls · 1boy" — the buckets strip count tags, so
        # this is the only place the user sees the head count.
        "subjects": settings.subject_summary(image.raw_prompt),
        "origin": (
            "local"
            if src and src.kind in settings.ORIGIN_LOCAL
            else "booru"
            if src and src.kind in settings.ORIGIN_BOORU
            else "other"
        ),
    }


@router.post("/roll")
def roll(body: RollIn) -> dict[str, Any]:
    origin_kinds = settings.origin_kinds(body.origin)
    caps = _norm_tags(body.cap_tags)
    excl = _norm_tags(body.exclude_tags)
    # One ticket per roll: cap_percent% of rolls may show a capped background.
    tickets = [random.random() * 100 < body.cap_percent for _ in range(body.count)]

    with db.session_scope() as s:
        if body.coherent:
            rolls = [
                {"buckets": out, "image": _image_info(s, image)}
                for out, image in _roll_coherent(s, body, origin_kinds, caps, tickets, excl)
            ]
        else:
            rolls = [
                {"buckets": out, "image": None}
                for out in _roll_independent(s, body, origin_kinds, caps, tickets, excl)
            ]
        # Top-level buckets/image mirror the first roll (pre-batch response
        # shape); `rolls` carries the whole batch for the prefetch pool.
        return {"buckets": rolls[0]["buckets"], "image": rolls[0]["image"], "rolls": rolls}

"""Re-classification endpoints (stage 2 + stage 3 + scene_line rebuild)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import db, jobs
from ..models import (
    Image,
    ImageTag,
    Job,
    LlmBatch,
    SceneLine,
    Tag,
    TagClassificationHistory,
)
from ..ingest.runner import (
    _bucket_groups,
    _persist_scene_lines,
    _rebuild_booru_character_scene_lines,
    _rebuild_scene_lines,
)
from ..ingest.tag_categorizer import SCENE_BUCKETS, classify_tag
from ..ingest.classify_queue import get_classify_queue_stats
from ..ingest.scene_exclude import is_eye_color_tag
from ..ingest.tag_ratings import infer_image_rating
from ..ingest.tag_history import record_change


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/queue")
def classify_queue(
    replace_below_confidence: float = 0.85,
    reset_below_confidence: float = 0.65,
    touch_other: bool = True,
    max_tags: Optional[int] = None,
    rating_only_missing: bool = True,
) -> dict[str, Any]:
    """Pre-flight counts for Smart classify (tags left per pass)."""
    return get_classify_queue_stats(
        replace_below_confidence=replace_below_confidence,
        reset_below_confidence=reset_below_confidence,
        touch_other=touch_other,
        max_tags=max_tags,
        rating_only_missing=rating_only_missing,
    )


class Stage2Body(BaseModel):
    model_name: str = "mixedbread-ai/mxbai-embed-large-v1"
    threshold: float = 0.55
    batch_size: int = 256
    rebuild_scenes: bool = True
    device: str = "auto"  # 'auto' | 'cuda' | 'cuda:0' | 'cuda:1' | 'cpu'


class Stage3Body(BaseModel):
    # Blank/absent = use whatever Settings → LLM providers is pointed at.
    # These used to default to OpenAI + gpt-4o-mini, which silently overrode
    # that setting for every manual run from the Tags page.
    provider: Optional[str] = None  # 'openai' | 'anthropic' | 'echo' | ''
    model: Optional[str] = None
    batch_size: int = 50
    max_tags: Optional[int] = None
    rebuild_scenes: bool = True
    concurrency: int = 6  # parallel LLM requests (clamped to 1..12)
    use_batch_api: bool = False  # submit via OpenAI Batch API instead of live calls


@router.post("/stage2")
def stage2(body: Stage2Body, bg: BackgroundTasks) -> dict[str, Any]:
    job_id = jobs.create_job("classify_stage2", "embedding NN", body.model_dump())
    bg.add_task(_run_stage2, job_id, body.model_dump())
    return {"job_id": job_id}


class Stage2ResetBody(BaseModel):
    # Reset embed-relabelled tags whose confidence is BELOW this cap. Default
    # 0.65 strips the noisy 0.55-0.65 band so a re-run can do better.
    below_confidence: float = 0.65
    rebuild_scenes: bool = True


class Stage1ReclassifyBody(BaseModel):
    """Options for the Stage 1 re-classification pass.

    Re-runs the deterministic Stage 1 classifier on existing Tag rows and
    upgrades buckets where the new rule produces something better than what's
    currently stored. Useful when a Stage 1 rule is added (e.g. the new
    franchise-suffix rule) and you want it to take effect on tags that have
    already been embed-routed somewhere wrong.
    """

    # Replace embed/llm relabels whose confidence is at or below this cap if
    # Stage 1 now produces a deterministic-rule match.
    replace_below_confidence: float = 0.85
    # Also touch tags currently classified as ``other``/``unknown`` (these
    # are always safe to upgrade since the new Stage 1 result is better).
    touch_other: bool = True
    # Reset tags whose bucket came from the franchise-suffix guess when that
    # rule no longer claims them, so a narrowed rule does not leave stale
    # ``character`` labels behind.
    release_stale: bool = True
    rebuild_scenes: bool = True


@router.post("/stage1/reclassify")
def stage1_reclassify(body: Stage1ReclassifyBody, bg: BackgroundTasks) -> dict[str, Any]:
    """Apply Stage 1 deterministic rules to existing tags.

    Use after adding a new Stage 1 rule (anthro / franchise-suffix / etc.)
    so it takes effect on tags that were already routed somewhere by Stage
    2. Each upgrade is recorded as a ``to_source='stage1_rule'`` row in
    the audit log.
    """
    job_id = jobs.create_job(
        "classify_stage1_reclassify",
        f"re-apply stage-1 rules (replace embed conf <= {body.replace_below_confidence:.2f})",
        body.model_dump(),
    )
    bg.add_task(_run_stage1_reclassify, job_id, body.model_dump())
    return {"job_id": job_id}


@router.post("/stage2/reset")
def stage2_reset(body: Stage2ResetBody, bg: BackgroundTasks) -> dict[str, Any]:
    """Return embed-labelled tags with low confidence back to 'other'.

    Use this after raising the threshold or adding new active buckets (e.g.
    ``extras``) so a re-run of Stage 2 / Stage 3 can re-route them. Every
    reverted tag is recorded as a ``to_source='reset'`` row in the audit
    log, with ``from_*`` capturing the embed label being undone, so you can
    still trace what was reverted later.
    """
    job_id = jobs.create_job(
        "classify_stage2_reset",
        f"reset embed relabels below {body.below_confidence:.2f}",
        body.model_dump(),
    )
    bg.add_task(_run_stage2_reset, job_id, body.model_dump())
    return {"job_id": job_id}


@router.post("/stage3")
def stage3(body: Stage3Body, bg: BackgroundTasks) -> dict[str, Any]:
    if body.use_batch_api:
        # A blank provider means "whatever Settings says" — resolve it before
        # judging. Comparing the raw field rejected every batch run once the
        # UI stopped sending an explicit provider.
        from .. import llm_config

        effective = body.provider or llm_config.get_target("stage3").kind
        if effective != "openai":
            raise HTTPException(
                400,
                "Batch API mode is OpenAI-only, but Stage 3 resolves to "
                f"'{effective}' — switch it to OpenAI under Settings → LLM "
                "providers or uncheck 'Use OpenAI Batch API'.",
            )
    label = f"{body.provider or 'configured endpoint'}/{body.model or 'configured model'}"
    job_id = jobs.create_job("classify_stage3", f"LLM ({label})", body.model_dump())
    bg.add_task(_run_stage3, job_id, body.model_dump())
    return {"job_id": job_id}


@router.get("/llm-batches")
def llm_batches() -> list[dict[str, Any]]:
    """List OpenAI Batch API jobs, refreshing non-finalized statuses first."""
    from ..ingest.stage3_llm import refresh_llm_batches

    return refresh_llm_batches()


@router.post("/llm-batches/{batch_id}/apply")
def llm_batch_apply(batch_id: int, bg: BackgroundTasks) -> dict[str, Any]:
    """Download a completed OpenAI batch's results and apply them to tags."""
    with db.session_scope() as s:
        row = s.get(LlmBatch, batch_id)
        if row is None:
            raise HTTPException(404, f"LLM batch {batch_id} not found")
        if row.applied:
            raise HTTPException(409, "batch results were already applied")
        in_flight = s.exec(
            select(Job)
            .where(Job.kind == "classify_stage3_apply")
            .where(Job.status.in_(["pending", "running"]))  # type: ignore[attr-defined]
        ).all()
        for j in in_flight:
            detail = json.loads(j.detail_json or "{}")
            if detail.get("batch_id") == batch_id:
                raise HTTPException(409, f"apply already running (job #{j.id})")
    job_id = jobs.create_job(
        "classify_stage3_apply", "Apply OpenAI batch", {"batch_id": batch_id}
    )
    bg.add_task(_run_apply_llm_batch, job_id, batch_id)
    return {"job_id": job_id}


class RebuildScenesBody(BaseModel):
    # Keep ``False`` so booru character tags stay in ``scene_line[character]`` for
    # export. The combined ``scene`` bucket never includes character either way.
    drop_character_tags: bool = False
    # Rebuild only the character bucket for booru images (fast fix after an
    # older rebuild wiped character lines).
    booru_character_only: bool = False


@router.post("/rebuild-scenes")
def rebuild(body: RebuildScenesBody, bg: BackgroundTasks) -> dict[str, Any]:
    label = (
        "rebuild character scene_line (booru only)"
        if body.booru_character_only
        else "rebuild scene_line"
    )
    job_id = jobs.create_job("classify_rebuild", label, body.model_dump())
    bg.add_task(_run_rebuild, job_id, body.model_dump())
    return {"job_id": job_id}


class RatingsBody(BaseModel):
    only_missing: bool = True  # only touch images with rating_source IS NULL
    overwrite_inferred: bool = False  # if False, leave 'provided' rows alone


@router.post("/ratings")
def classify_ratings(body: RatingsBody, bg: BackgroundTasks) -> dict[str, Any]:
    job_id = jobs.create_job(
        "classify_ratings", "infer scene ratings", body.model_dump()
    )
    bg.add_task(_run_classify_ratings, job_id, body.model_dump())
    return {"job_id": job_id}


# ---------------------------------------------------------------------------


def _run_stage2(job_id: int, body: dict[str, Any]) -> None:
    try:
        from ..ingest.stage2_embed import reclassify_residuals, _resolve_device

        device_req = body.get("device", "auto")
        dev, gpu_name = _resolve_device(device_req)
        jobs.update_job(
            job_id,
            status="running",
            progress=0.1,
            message=(
                f"loading {body['model_name']} on {dev}"
                + (f" ({gpu_name})" if gpu_name else "")
            ),
        )
        result = reclassify_residuals(
            model_name=body["model_name"],
            threshold=body["threshold"],
            batch_size=body["batch_size"],
            device=device_req,
            job_id=job_id,
        )
        if body.get("rebuild_scenes", True):
            jobs.update_job(job_id, progress=0.85, message="rebuilding scene_line")
            _rebuild_scene_lines()
        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=f"relabelled {result.relabelled}/{result.embedded_residuals} residuals",
            detail={
                "embedded_residuals": result.embedded_residuals,
                "relabelled": result.relabelled,
                "by_bucket": result.by_bucket,
                "elapsed_sec": result.elapsed_sec,
            },
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


def _run_stage1_reclassify(job_id: int, body: dict[str, Any]) -> None:
    """Walk every Tag and upgrade its bucket if Stage 1 now does better."""
    cap = float(body.get("replace_below_confidence", 0.85))
    touch_other = bool(body.get("touch_other", True))
    release_stale = bool(body.get("release_stale", True))
    released = 0
    try:
        jobs.update_job(
            job_id, status="running", progress=0.05, message="scanning tag taxonomy"
        )

        with db.session_scope() as s:
            tag_rows = list(s.exec(select(Tag.id, Tag.name)).all())

        total = len(tag_rows)
        upgraded = 0
        by_new_source: dict[str, int] = {}
        by_from_bucket: dict[str, int] = {}

        for i, (tag_id, tag_name) in enumerate(tag_rows, start=1):
            assignment = classify_tag(tag_name)
            # Stage 1 has nothing to say. Normally that means leave the tag
            # alone — but a bucket STAMPED by the franchise-suffix guess is
            # only as good as that guess, and the rule has since been
            # narrowed (a `_(meme)` tag names a joke, `_(mtf)` a
            # transformation; neither is a character). Release those back to
            # the residual pool so Stage 2/3 can route them properly.
            #
            # Deliberately limited to franchise_suffix: the same treatment
            # applied to scene_exclude would drag `high_detail` and friends
            # out of quality_meta and into scene wildcards, and applied to
            # tag_tree would dump 90k references of anatomy into the queue.
            if assignment.bucket == "other" or assignment.bucket_source == "unknown":
                if release_stale:
                    with db.session_scope() as s:
                        tag = s.get(Tag, tag_id)
                        if (
                            tag is not None
                            and not tag.locked
                            and tag.bucket_source == "franchise_suffix"
                            and tag.bucket != "other"
                        ):
                            record_change(
                                s,
                                tag,
                                new_bucket="other",
                                new_source="unknown",
                                new_confidence=0.0,
                                model=None,
                                job_id=job_id,
                            )
                            by_from_bucket[tag.bucket] = (
                                by_from_bucket.get(tag.bucket, 0) + 1
                            )
                            by_new_source["released"] = (
                                by_new_source.get("released", 0) + 1
                            )
                            tag.bucket = "other"
                            tag.bucket_source = "unknown"
                            tag.confidence = 0.0
                            tag.updated_at = datetime.utcnow()
                            s.add(tag)
                            released += 1
                if i % 2000 == 0 or i == total:
                    jobs.update_job(
                        job_id,
                        progress=0.05 + 0.8 * (i / total),
                        message=f"scanning {i:,}/{total:,} ({upgraded:,} upgraded)",
                    )
                continue

            with db.session_scope() as s:
                tag = s.get(Tag, tag_id)
                if tag is None or tag.locked:
                    continue

                src = tag.bucket_source or ""
                eligible = False
                # Force-remove tags that belong in the main prompt, not scene buckets.
                if (
                    assignment.bucket_source == "scene_exclude"
                    and tag.bucket in SCENE_BUCKETS
                ):
                    eligible = True
                elif is_eye_color_tag(tag_name) and tag.bucket in SCENE_BUCKETS:
                    eligible = True
                # Always safe to upgrade unclassified residuals.
                elif touch_other and tag.bucket == "other":
                    eligible = True
                # Replace embed/llm calls where new Stage 1 rule is at least
                # as confident AND the existing call wasn't strongly held.
                elif src in ("embed", "llm") and tag.confidence <= cap:
                    eligible = True
                # Re-apply rules on top of older rule-based labels too.
                elif src in ("unknown", "tag_tree") and tag.confidence < assignment.confidence:
                    eligible = True
                # Deterministic rule sources whose ANSWER changed. These carry
                # the same confidence before and after, so the comparison above
                # can never catch them — which left every tag mis-bucketed by a
                # rule bug (wrong tag-tree parent, a hairstyle caught by the
                # anatomy regex, a non-franchise qualifier) permanently stuck.
                elif src in (
                    "tag_tree",
                    "franchise_suffix",
                    "qualifier_rule",
                    "scene_exclude",
                ) and (
                    tag.bucket != assignment.bucket
                    or tag.bucket_source != assignment.bucket_source
                ):
                    eligible = True

                # No-op if Stage 1 produces the exact same bucket+source.
                if (
                    eligible
                    and tag.bucket == assignment.bucket
                    and tag.bucket_source == assignment.bucket_source
                ):
                    eligible = False

                if not eligible:
                    if i % 2000 == 0 or i == total:
                        jobs.update_job(
                            job_id,
                            progress=0.05 + 0.8 * (i / total),
                            message=f"scanning {i:,}/{total:,} ({upgraded:,} upgraded)",
                        )
                    continue

                by_from_bucket[tag.bucket] = by_from_bucket.get(tag.bucket, 0) + 1
                by_new_source[assignment.bucket_source] = (
                    by_new_source.get(assignment.bucket_source, 0) + 1
                )

                record_change(
                    s,
                    tag,
                    new_bucket=assignment.bucket,
                    new_source=assignment.bucket_source,
                    new_confidence=assignment.confidence,
                    model=None,
                    job_id=job_id,
                )
                tag.bucket = assignment.bucket
                tag.bucket_source = assignment.bucket_source
                tag.confidence = assignment.confidence
                tag.category = assignment.category
                tag.updated_at = datetime.utcnow()
                s.add(tag)
                upgraded += 1

            if i % 1000 == 0 or i == total:
                jobs.update_job(
                    job_id,
                    progress=0.05 + 0.8 * (i / total),
                    message=f"scanning {i:,}/{total:,} ({upgraded:,} upgraded)",
                )

        if body.get("rebuild_scenes", True):
            jobs.update_job(job_id, progress=0.90, message="rebuilding scene_line")
            _rebuild_scene_lines()

        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=(
                f"upgraded {upgraded:,}/{total:,} tags via stage-1 rules"
                + (f", released {released:,}" if released else "")
            ),
            detail={
                "scanned": total,
                "upgraded": upgraded,
                "released": released,
                "replace_below_confidence": cap,
                "touch_other": touch_other,
                "release_stale": release_stale,
                "by_new_source": by_new_source,
                "by_from_bucket": by_from_bucket,
            },
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


def _run_stage2_reset(job_id: int, body: dict[str, Any]) -> None:
    """Revert sub-threshold embed relabels to ``bucket='other'`` + log it."""
    try:
        cap = float(body.get("below_confidence", 0.65))
        jobs.update_job(
            job_id,
            status="running",
            progress=0.05,
            message=f"finding embed relabels below confidence {cap:.2f}",
        )

        with db.session_scope() as s:
            targets = list(
                s.exec(
                    select(Tag)
                    .where(Tag.bucket_source == "embed")
                    .where(Tag.confidence < cap)
                    .where(Tag.locked == False)  # noqa: E712
                ).all()
            )
            total = len(targets)
            if total == 0:
                jobs.update_job(
                    job_id,
                    status="done",
                    progress=1.0,
                    message="no embed relabels below threshold",
                    detail={"reverted": 0, "threshold": cap},
                    finished=True,
                )
                return

            reverted = 0
            by_from_bucket: dict[str, int] = {}
            now = datetime.utcnow()
            for tag in targets:
                by_from_bucket[tag.bucket] = by_from_bucket.get(tag.bucket, 0) + 1
                record_change(
                    s,
                    tag,
                    new_bucket="other",
                    new_source="reset",
                    new_confidence=0.0,
                    model=None,
                    job_id=job_id,
                )
                tag.bucket = "other"
                tag.bucket_source = "unknown"
                tag.confidence = 0.0
                tag.updated_at = now
                s.add(tag)
                reverted += 1
                if reverted % 1000 == 0:
                    jobs.update_job(
                        job_id,
                        progress=0.1 + 0.7 * (reverted / total),
                        message=f"reverted {reverted:,}/{total:,}",
                    )

        if body.get("rebuild_scenes", True):
            jobs.update_job(job_id, progress=0.85, message="rebuilding scene_line")
            _rebuild_scene_lines()

        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=f"reset {reverted:,} embed relabels (conf < {cap:.2f})",
            detail={
                "reverted": reverted,
                "threshold": cap,
                "by_from_bucket": by_from_bucket,
            },
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


def _run_stage3(job_id: int, body: dict[str, Any]) -> None:
    try:
        from ..ingest.stage3_llm import reclassify_residuals, submit_batch_job

        if body.get("use_batch_api", False):
            # Async path: submit to the OpenAI Batch API and finish — results
            # are pulled in later via the /llm-batches apply endpoint, so no
            # scene rebuild happens here.
            jobs.update_job(
                job_id,
                status="running",
                progress=0.1,
                message="submitting OpenAI batch…",
            )
            detail = submit_batch_job(
                model=body.get("model") or "",
                batch_size=body["batch_size"],
                max_tags=body.get("max_tags"),
                job_id=job_id,
            )
            message = (
                f"submitted OpenAI batch {detail['openai_batch_id']} "
                f"({detail['tag_count']} tags)"
                if detail["tag_count"]
                else "no uncached residual tags — nothing submitted"
            )
            jobs.update_job(
                job_id,
                status="done",
                progress=1.0,
                message=message,
                detail=detail,
                finished=True,
            )
            return

        jobs.update_job(
            job_id,
            status="running",
            progress=0.1,
            message=f"calling {body.get('provider') or 'the configured endpoint'}…",
        )
        result = reclassify_residuals(
            provider=body.get("provider") or "",
            model=body.get("model") or "",
            batch_size=body["batch_size"],
            max_tags=body.get("max_tags"),
            concurrency=body.get("concurrency", 6),
            job_id=job_id,
        )
        rebuild_note = ""
        if body.get("rebuild_scenes", True):
            jobs.update_job(job_id, progress=0.85, message="rebuilding scene_line")
            try:
                # Delta rebuild: only images referencing relabelled tags.
                _rebuild_scene_lines(
                    changed_tag_ids=result.changed_tag_ids,
                    job_id=job_id,
                    progress_range=(0.85, 0.99),
                )
            except Exception as exc:
                # Relabels are already committed — don't let a rebuild
                # failure hide that. Point at the manual full rebuild.
                rebuild_note = (
                    f" · scene rebuild FAILED ({exc}) — run Rebuild scene "
                    f"lines from the Tags page"
                )
        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=(
                f"relabelled {result.relabelled}/{result.requested} residuals"
                + (f" · {result.failed_batches} batches skipped (will retry next run)" if result.failed_batches else "")
                + rebuild_note
            ),
            detail={
                "requested": result.requested,
                "relabelled": result.relabelled,
                "by_bucket": result.by_bucket,
                "elapsed_sec": result.elapsed_sec,
                "cache_path": result.cache_path,
                "failed_batches": result.failed_batches,
            },
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


def _run_apply_llm_batch(job_id: int, batch_id: int) -> None:
    """Apply a completed OpenAI batch's results, then rebuild scene_line."""
    try:
        from ..ingest.stage3_llm import apply_batch

        jobs.update_job(
            job_id,
            status="running",
            progress=0.1,
            message="retrieving OpenAI batch…",
        )
        result = apply_batch(batch_id, job_id=job_id)
        rebuild_note = ""
        jobs.update_job(job_id, progress=0.85, message="rebuilding scene_line")
        try:
            _rebuild_scene_lines(
                changed_tag_ids=result.get("changed_tag_ids"),
                job_id=job_id,
                progress_range=(0.85, 0.99),
            )
        except Exception as exc:
            rebuild_note = (
                f" · scene rebuild FAILED ({exc}) — run Rebuild scene lines "
                f"from the Tags page"
            )
        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=(
                f"relabelled {result['relabelled']} residuals from OpenAI batch"
                + (
                    f" · {result['failed_lines']} result lines unparsable"
                    if result["failed_lines"]
                    else ""
                )
                + rebuild_note
            ),
            detail=result,
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


def _run_rebuild(job_id: int, body: dict[str, Any]) -> None:
    drop_character = bool(body.get("drop_character_tags", False))
    booru_character_only = bool(body.get("booru_character_only", False))
    try:
        jobs.update_job(job_id, status="running", progress=0.1, message="scanning images")
        if booru_character_only:
            rebuilt = _rebuild_booru_character_scene_lines()
        else:
            rebuilt = _rebuild_scene_lines(
                drop_character_tags=drop_character,
                job_id=job_id,
                progress_range=(0.1, 0.95),
            )
        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=f"rebuilt scene_line for {rebuilt} images",
            detail={"images": rebuilt},
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


def _run_classify_ratings(job_id: int, body: dict[str, Any]) -> None:
    """Walk Images and (re)infer rating from their tag set."""
    only_missing = bool(body.get("only_missing", True))
    overwrite_inferred = bool(body.get("overwrite_inferred", False))
    try:
        jobs.update_job(
            job_id, status="running", progress=0.05, message="scanning images"
        )
        image_ids = _select_image_ids_for_rating(only_missing)
        total = len(image_ids)
        if total == 0:
            jobs.update_job(
                job_id,
                status="done",
                progress=1.0,
                message="no images needed rating inference",
                detail={"updated": 0, "total": 0},
                finished=True,
            )
            return

        updated = 0
        for i, img_id in enumerate(image_ids, start=1):
            with db.session_scope() as s:
                img = s.get(Image, img_id)
                if img is None:
                    continue
                if (
                    not overwrite_inferred
                    and img.rating_source == "provided"
                ):
                    continue
                tag_rows = s.exec(
                    select(Tag.name)
                    .join(ImageTag, ImageTag.tag_id == Tag.id)
                    .where(ImageTag.image_id == img_id)
                ).all()
                inferred, evidence = infer_image_rating(list(tag_rows))
                img.rating = inferred
                img.rating_source = "inferred"
                img.rating_evidence = ",".join(evidence[:16]) or None
                s.add(img)
                updated += 1
            if i % 200 == 0 or i == total:
                jobs.update_job(
                    job_id,
                    progress=i / total,
                    message=f"{i:,}/{total:,} images ({updated:,} updated)",
                    detail={"total": total, "processed": i, "updated": updated},
                )

        jobs.update_job(
            job_id,
            status="done",
            progress=1.0,
            message=f"inferred rating for {updated:,}/{total:,} images",
            detail={"total": total, "updated": updated},
            finished=True,
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        jobs.update_job(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            finished=True,
        )


def _select_image_ids_for_rating(only_missing: bool) -> list[int]:
    with db.session_scope() as s:
        q = select(Image.id)
        if only_missing:
            q = q.where(Image.rating_source.is_(None))  # type: ignore[attr-defined]
        return list(s.exec(q).all())



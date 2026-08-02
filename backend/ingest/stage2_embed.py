"""Stage-2 classifier: embedding-based nearest-bucket centroid.

For every tag the Stage-1 classifier left in ``bucket = 'other'`` /
``bucket_source = 'unknown'``, we:

1. Compute per-bucket centroids from the embeddings of tags already labelled
   by Stage 1 (excluding ``other`` and the drop-buckets).
2. Embed each residual tag.
3. If the cosine similarity to the nearest centroid exceeds the threshold
   (default 0.55), assign that bucket with ``bucket_source = 'embed'`` and
   ``confidence = cos_similarity``.

This module is intentionally optional -- it only imports
``sentence-transformers`` and ``numpy`` lazily so the base install works
without those heavy dependencies.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlmodel import select

from .. import db, settings
from ..models import Tag
from .tag_categorizer import BUCKETS
from .tag_history import record_change


logger = logging.getLogger(__name__)

# Buckets that participate in centroid-NN (we never want to re-label tags as
# these drop buckets via embedding similarity). ``extras`` is included so
# common objects / weapons / food / animals end up there instead of being
# force-fit into composition or background.
ACTIVE_BUCKETS = (
    "outfit",
    "pose",
    "expression",
    "background",
    "composition",
    "accessory",
    "extras",
)


@dataclass
class Stage2Result:
    threshold: float
    embedded_residuals: int
    relabelled: int
    by_bucket: dict[str, int]
    elapsed_sec: float


def _try_import():
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "stage-2 requires `pip install -e .[embed]` (sentence-transformers + numpy)"
        ) from exc
    return SentenceTransformer, np


def _resolve_device(requested: str | None) -> tuple[str, str | None]:
    """Pick the best device for embedding.

    Returns ``(device, gpu_name)``. ``requested`` may be ``'auto'`` (default),
    ``'cuda'``, ``'cuda:0'``, ``'cuda:1'``, or ``'cpu'``. We import torch
    lazily so the base install (no embedding extras) still works.
    """
    try:
        import torch  # type: ignore
    except ImportError:
        # No torch at all - tag_torch=False is impossible since
        # sentence-transformers requires torch, but be defensive.
        return "cpu", None

    want = (requested or "auto").lower()
    if want == "cpu":
        return "cpu", None

    if not torch.cuda.is_available():
        if want in ("cuda", "auto") or want.startswith("cuda"):
            logger.warning(
                "stage-2: CUDA requested but torch.cuda.is_available() is False. "
                "Either your torch install is CPU-only (reinstall with the cu121 "
                "wheel from pytorch.org) or no NVIDIA driver is visible. "
                "Falling back to CPU."
            )
        return "cpu", None

    if want == "auto" or want == "cuda":
        device = "cuda:0"
    else:
        device = want  # honour explicit "cuda:1" / "cuda:0"

    # Parse the index OUTSIDE the try: a malformed 'cuda:x' used to raise
    # inside it, skip the device-count clamp, and reach SentenceTransformer
    # verbatim as a RuntimeError from torch.
    try:
        idx = int(device.split(":", 1)[1]) if ":" in device else 0
    except ValueError:
        logger.warning("stage-2: unparseable device %r; using cuda:0", device)
        device, idx = "cuda:0", 0

    try:
        if idx >= torch.cuda.device_count():
            logger.warning(
                "stage-2: requested %s but only %d CUDA device(s) visible; using cuda:0",
                device,
                torch.cuda.device_count(),
            )
            device = "cuda:0"
            idx = 0
        gpu_name = torch.cuda.get_device_name(idx)
    except Exception:
        gpu_name = None

    return device, gpu_name


def _tag_text(name: str) -> str:
    """Convert canonical tag to a human-readable phrase for embedding."""
    return name.replace("_", " ")


def reclassify_residuals(
    *,
    model_name: str = "mixedbread-ai/mxbai-embed-large-v1",
    threshold: float = 0.55,
    batch_size: int = 256,
    device: str | None = None,
    job_id: int | None = None,
) -> Stage2Result:
    """Embed labelled tags + residuals, reclassify the latter to nearest centroid.

    ``device`` may be ``'auto'`` (default — picks first CUDA device if available),
    ``'cuda'``, ``'cuda:0'``, ``'cuda:1'``, or ``'cpu'``.

    ``job_id`` is recorded on every ``TagClassificationHistory`` row so the
    audit panel can show which classification run produced each relabel.
    """
    SentenceTransformer, np = _try_import()
    start = datetime.utcnow()
    resolved_device, gpu_name = _resolve_device(device)
    logger.info(
        "loading sentence-transformer %s on device=%s%s",
        model_name,
        resolved_device,
        f" ({gpu_name})" if gpu_name else "",
    )
    model = SentenceTransformer(model_name, device=resolved_device)

    with db.session_scope() as s:
        # Centroids come from trusted labels only. Including bucket_source
        # 'embed' fed this stage its own prior guesses, so each run's noise
        # dragged the next run's centroids further off.
        labelled = [
            (t.bucket, t.name)
            for t in s.exec(
                select(Tag)
                .where(Tag.bucket.in_(ACTIVE_BUCKETS))  # type: ignore[arg-type]
                .where(Tag.bucket_source != "embed")
            ).all()
        ]
        residual_pairs = [
            (t.id, t.name)
            for t in s.exec(
                select(Tag)
                .where(Tag.bucket == "other")
                .where(Tag.bucket_source.in_(("unknown", "tag_tree")))  # type: ignore[arg-type]
                .where(Tag.locked == False)  # noqa: E712
            ).all()
        ]

    if not labelled:
        raise RuntimeError("no stage-1 labels available; run an ingest first")

    by_bucket: dict[str, list[str]] = {b: [] for b in ACTIVE_BUCKETS}
    for bucket, name in labelled:
        by_bucket.setdefault(bucket, []).append(_tag_text(name))

    logger.info(
        "labelled tags: %s; residuals: %d",
        {k: len(v) for k, v in by_bucket.items()},
        len(residual_pairs),
    )

    centroids: dict[str, "np.ndarray"] = {}
    for bucket, texts in by_bucket.items():
        if not texts:
            continue
        emb = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        centroids[bucket] = np.mean(emb, axis=0)
        centroids[bucket] /= np.linalg.norm(centroids[bucket]) + 1e-12

    if not centroids or not residual_pairs:
        return Stage2Result(threshold, 0, 0, {}, 0.0)

    bucket_names = list(centroids.keys())
    centroid_matrix = np.stack([centroids[b] for b in bucket_names])

    residual_texts = [_tag_text(name) for _tid, name in residual_pairs]
    residual_emb = model.encode(
        residual_texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    sims = residual_emb @ centroid_matrix.T  # (N, B)
    best_idx = sims.argmax(axis=1)
    best_sim = sims.max(axis=1)

    relabelled = 0
    bucket_counts: dict[str, int] = {}
    with db.session_scope() as s:
        for (tag_id, _name), idx, sim in zip(residual_pairs, best_idx, best_sim):
            if sim < threshold:
                continue
            db_tag = s.get(Tag, tag_id)
            # Re-check eligibility, not just `locked`. The residual set was
            # snapshotted minutes ago (embedding ~80k texts takes a while);
            # a Stage 3 run or a manual edit in that window would otherwise
            # be silently overwritten by a stale cosine verdict.
            if (
                db_tag is None
                or db_tag.locked
                or db_tag.bucket != "other"
                or db_tag.bucket_source not in ("unknown", "tag_tree")
            ):
                continue
            new_bucket = bucket_names[int(idx)]
            # Audit row first, *before* we overwrite the from_* state.
            record_change(
                s,
                db_tag,
                new_bucket=new_bucket,
                new_source="embed",
                new_confidence=float(sim),
                model=model_name,
                job_id=job_id,
            )
            db_tag.bucket = new_bucket
            db_tag.bucket_source = "embed"
            db_tag.confidence = float(sim)
            db_tag.updated_at = datetime.utcnow()
            s.add(db_tag)
            relabelled += 1
            bucket_counts[new_bucket] = bucket_counts.get(new_bucket, 0) + 1

    elapsed = (datetime.utcnow() - start).total_seconds()
    logger.info(
        "stage-2 done: relabelled %d/%d residuals in %.1fs",
        relabelled,
        len(residual_pairs),
        elapsed,
    )
    return Stage2Result(
        threshold=threshold,
        embedded_residuals=len(residual_pairs),
        relabelled=relabelled,
        by_bucket=bucket_counts,
        elapsed_sec=elapsed,
    )

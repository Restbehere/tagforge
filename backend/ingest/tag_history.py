"""Write-side helper for ``TagClassificationHistory``.

Every callable that mutates ``Tag.bucket`` should call ``record_change``
*before* it writes the new value, so the ``from_*`` snapshot reflects the
pre-change state. Reads happen via the ``/api/tags/history`` route.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from ..models import Tag, TagClassificationHistory


def record_change(
    session: Session,
    tag: Tag,
    *,
    new_bucket: str,
    new_source: str,
    new_confidence: float,
    model: Optional[str] = None,
    job_id: Optional[int] = None,
) -> Optional[TagClassificationHistory]:
    """Insert a history row describing the impending bucket change.

    Returns the inserted row (not yet flushed) or ``None`` if the change is
    a no-op (same bucket + same source). Skipping no-ops keeps the audit
    log focused on actual relabels — e.g. a manual override that just
    reasserts the existing bucket won't produce noise.
    """
    if tag.id is None:
        return None
    if tag.bucket == new_bucket and tag.bucket_source == new_source:
        return None

    history = TagClassificationHistory(
        tag_id=tag.id,
        tag_name=tag.name,
        from_bucket=tag.bucket,
        from_source=tag.bucket_source,
        from_confidence=tag.confidence,
        to_bucket=new_bucket,
        to_source=new_source,
        to_confidence=new_confidence,
        model=model,
        job_id=job_id,
    )
    session.add(history)
    return history

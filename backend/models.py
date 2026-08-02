"""SQLModel ORM definitions for the Tag Forge database."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Column, Field, SQLModel, String, UniqueConstraint


def _utcnow() -> datetime:
    return datetime.utcnow()


class Source(SQLModel, table=True):
    """A logical batch of data we ingested from somewhere."""

    __tablename__ = "source"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # 'metadata_file' | 'danbooru' | 'aibooru'
    label: str = Field(index=True)
    fetched_at: datetime = Field(default_factory=_utcnow)
    note: Optional[str] = None
    filters_json: Optional[str] = None  # JSON-encoded filters used at fetch time
    # Stable hash over the identifying inputs of this source. Re-running an
    # ingest with the same key reuses the existing row instead of creating a
    # duplicate (so re-importing the same metadata.txt path or re-fetching
    # ``popular date=YYYY-MM-DD`` is idempotent at the source level).
    dedup_key: Optional[str] = Field(
        sa_column=Column("dedup_key", String, unique=True, index=True, nullable=True)
    )
    image_count: int = 0


class Image(SQLModel, table=True):
    """A single image / prompt instance from a source."""

    __tablename__ = "image"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_image_source_external"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source.id", index=True)
    external_id: str = Field(index=True)
    rating: Optional[str] = Field(default=None, index=True)  # g/s/q/e
    # How `rating` was set: 'provided' (booru API), 'inferred' (tag classifier),
    # or NULL (legacy / unclassified).
    rating_source: Optional[str] = Field(default=None, index=True)
    # Comma-joined evidence tags used by the inference (debug aid).
    rating_evidence: Optional[str] = None
    score: Optional[int] = Field(default=None, index=True)
    fav_count: Optional[int] = None
    nai_model: Optional[str] = None
    software: Optional[str] = None  # 'NovelAI' / 'Stable Diffusion' / ...
    width: Optional[int] = None
    height: Optional[int] = None
    raw_prompt: str = ""
    raw_negative: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    # When the post was published on the source site (e.g. Danbooru created_at).
    # NULL for local metadata or legacy rows ingested before this field existed.
    external_created_at: Optional[datetime] = Field(default=None, index=True)


class Tag(SQLModel, table=True):
    """A canonical Danbooru-style tag and its assigned bucket."""

    __tablename__ = "tag"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column("name", String, unique=True, index=True))
    display: str = ""
    bucket: str = Field(default="other", index=True)
    bucket_source: str = "unknown"  # tag_tree / dataset_category / embed / llm / manual / unknown
    confidence: float = 0.0
    post_count: int = 0
    category: int = 0  # 0=general, 1=artist, 3=copyright, 4=character, 5=meta
    locked: bool = False  # user manual override should not be overwritten
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ImageTag(SQLModel, table=True):
    """Many-to-many between Image and Tag with original ordering preserved."""

    __tablename__ = "image_tag"

    image_id: int = Field(foreign_key="image.id", primary_key=True)
    tag_id: int = Field(foreign_key="tag.id", primary_key=True)
    order_idx: int = 0


class SceneLine(SQLModel, table=True):
    """A per-image bucket slice ready for wildcard export.

    One row per (image_id, bucket). The combined ``scene`` bucket concatenates
    outfit + pose + expression + background so the user can use it as a complete
    coherent scene with their own character prompt.
    """

    __tablename__ = "scene_line"
    __table_args__ = (
        UniqueConstraint("image_id", "bucket", name="uq_scene_image_bucket"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    image_id: int = Field(foreign_key="image.id", index=True)
    bucket: str = Field(index=True)
    tag_text: str
    tag_count: int = 0


class ExportSet(SQLModel, table=True):
    """A named export job."""

    __tablename__ = "export_set"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    filters_json: Optional[str] = None
    output_dir: str = ""
    file_count: int = 0
    line_count: int = 0


class Job(SQLModel, table=True):
    """A background job (ingest, scrape, classify, export)."""

    __tablename__ = "job"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # 'ingest_metadata' | 'fetch_booru' | ...
    label: str = ""
    status: str = "pending"  # pending / running / done / error / cancelled
    progress: float = 0.0  # 0..1
    message: str = ""
    detail_json: Optional[str] = None  # arbitrary JSON payload
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None


class TagBucketOverride(SQLModel, table=True):
    """User-supplied manual overrides for tag bucketing.

    Kept separate from Tag.bucket so the source of truth is auditable and
    overrides survive re-classification.
    """

    __tablename__ = "tag_bucket_override"

    tag_name: str = Field(primary_key=True)
    bucket: str
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class TagClassificationHistory(SQLModel, table=True):
    """Audit log: one row per tag bucket reclassification event.

    Written whenever Stage 2 (embedding), Stage 3 (LLM), or a manual
    override changes a tag's bucket. ``from_*`` fields capture the
    pre-change state so reviewers can diff and roll back suspect relabels
    even after the Tag row itself has been overwritten.
    """

    __tablename__ = "tag_classification_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    tag_id: int = Field(foreign_key="tag.id", index=True)
    # Denormalized so the history row stays meaningful if the tag is renamed
    # (we don't currently support rename, but it's cheap insurance).
    tag_name: str = Field(index=True)
    from_bucket: Optional[str] = Field(default=None, index=True)
    from_source: Optional[str] = None
    from_confidence: Optional[float] = None
    to_bucket: str = Field(index=True)
    to_source: str = Field(index=True)  # 'embed' | 'llm' | 'manual' | 'backfill'
    to_confidence: float = 0.0
    # The model used for the change (mxbai/bge/gpt-4o-mini/...). NULL for manual.
    model: Optional[str] = None
    # FK to the Job that produced the change, if any. NULL for manual edits
    # made through the UI / API.
    job_id: Optional[int] = Field(default=None, foreign_key="job.id", index=True)
    at: datetime = Field(default_factory=_utcnow, index=True)


class DenyList(SQLModel, table=True):
    """A user-named deny list saved for reuse across exports."""

    __tablename__ = "deny_list"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column("name", String, unique=True, index=True))
    tags_json: str = "[]"  # JSON-encoded list[str]
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Preset(SQLModel, table=True):
    """A named snapshot of a form configuration (booru fetch, export, ...).

    ``kind`` scopes the namespace ('fetch' | 'export'); ``data_json`` holds
    the serialized form fields. Credentials are never stored here.
    """

    __tablename__ = "preset"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_preset_kind_name"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    name: str = Field(index=True)
    data_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class DecompItem(SQLModel, table=True):
    """One image queued for See-through layer decomposition.

    Runs as a subprocess of the external see-through repo (own conda env);
    a single worker thread drains the queue in order. Artifacts live under
    ``data/decompose/out/<id>/``: the layered .psd, a depth .psd, and a
    folder of per-layer transparent PNGs (+ reconstruction/src previews).
    """

    __tablename__ = "decomp_item"

    id: Optional[int] = Field(default=None, primary_key=True)
    original_name: str = ""
    input_path: str = ""
    params_json: str = "{}"
    status: str = Field(default="queued", index=True)
    # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    psd_path: Optional[str] = None
    depth_psd_path: Optional[str] = None
    layers_dir: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class FeatureLog(SQLModel, table=True):
    """A record of a character being featured on a channel (X post,
    Patreon poll). Powers the Trends "last featured" column so the user can
    pace their publishing without repeats."""

    __tablename__ = "feature_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    character: str = Field(index=True)
    channel: str = Field(index=True)  # 'x' | 'patreon'
    at: datetime = Field(default_factory=_utcnow, index=True)


class LlmBatch(SQLModel, table=True):
    """A submitted OpenAI Batch API job for Stage-3 tag classification.

    Rows track the async lifecycle: submitted -> (validating/in_progress/
    finalizing) -> completed/failed/expired/cancelled -> applied.
    """

    __tablename__ = "llm_batch"

    id: Optional[int] = Field(default=None, primary_key=True)
    openai_batch_id: str = Field(
        sa_column=Column("openai_batch_id", String, unique=True, index=True)
    )
    status: str = Field(default="submitted", index=True)
    model: str = ""
    tag_count: int = 0
    request_count: int = 0
    submitted_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    applied: bool = False
    error: Optional[str] = None
    job_id: Optional[int] = Field(default=None, foreign_key="job.id")

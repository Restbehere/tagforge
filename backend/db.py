"""SQLite engine + session helpers."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from . import settings


logger = logging.getLogger(__name__)


# Columns we've added to existing tables after the original schema shipped.
# ``init_db`` runs these as ``ALTER TABLE ... ADD COLUMN`` for any database
# that already has the table but is missing the column. SQLModel.create_all()
# only creates *missing* tables; it never touches existing tables, so without
# this we'd silently drift and queries against new columns would 500.
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (table, column, sql column type)
    ("image", "rating_source", "VARCHAR"),
    ("image", "rating_evidence", "VARCHAR"),
    ("source", "dedup_key", "VARCHAR"),
    ("image", "external_created_at", "DATETIME"),
)

# Indexes that should exist on those new columns (mirrors the ``index=True``
# flags on the SQLModel fields). ``CREATE INDEX IF NOT EXISTS`` is a no-op
# when the index already exists.
_INDEX_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (index_name, table, column(s))
    ("ix_image_rating_source", "image", "rating_source"),
    ("ix_source_dedup_key", "source", "dedup_key"),
    ("ix_image_external_created_at", "image", "external_created_at"),
    # Covering index for the builder's coherent-roll GROUP BY: the query
    # becomes index-only, so it stays fast even when the OS file cache is
    # cold (model loads evict the DB pages; the 1.1GB table re-read was the
    # "10s roll after idle" bug). Measured 4x faster warm, far more cold.
    ("ix_scene_line_bucket_image", "scene_line", "bucket, image_id"),
    # image_tag.tag_id had no index, so the per-tag usage counts on every
    # /api/tags page load full-scanned 11.5M rows (measured 5.3s/request).
    # Covering (tag_id, image_id) keeps the count index-only.
    ("ix_image_tag_tag_id", "image_tag", "tag_id, image_id"),
)


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings.ensure_dirs()
        url = f"sqlite:///{settings.DB_PATH.as_posix()}"
        _engine = create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _on_connect(dbapi_conn, _connection_record):  # pragma: no cover - hook
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.execute("PRAGMA cache_size=-65536")  # ~64MB
            cur.close()

    return _engine


def init_db() -> None:
    """Create all tables and run lightweight column/index migrations.

    Idempotent — safe to call on every startup. ``create_all`` makes any
    missing tables; ``_migrate_columns`` then adds any columns that were
    introduced after the original schema (SQLite only supports a tiny subset
    of ALTER TABLE, but ADD COLUMN works fine).
    """
    # Import models so SQLModel metadata is populated before create_all.
    from . import models  # noqa: F401

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _migrate_columns(engine)


def _migrate_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    alter_statements: list[str] = []
    for table, column, sql_type in _COLUMN_MIGRATIONS:
        if table not in existing_tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column in cols:
            continue
        alter_statements.append(
            f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
        )

    if not alter_statements and not _INDEX_MIGRATIONS:
        return

    with engine.begin() as conn:
        for stmt in alter_statements:
            logger.info("schema migration: %s", stmt)
            conn.execute(text(stmt))
        for index_name, table, column in _INDEX_MIGRATIONS:
            if table not in existing_tables:
                continue
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column})"
                )
            )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed session that commits on exit, rolls back on error."""
    session = Session(get_engine())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session

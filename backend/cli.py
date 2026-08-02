"""Command-line helpers for the backend (init-db, ingest, export, seed-tags)."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import urllib.request
from pathlib import Path
from typing import Optional

import typer

from . import db, settings
from .ingest.exporter import build_export, DEFAULT_BUCKETS
from .ingest.metadata_parser import count_records, preview_metadata_file
from .ingest.runner import run_metadata_ingest, run_booru_fetch
from .ingest.tag_categorizer import reload_caches


logger = logging.getLogger("tagforge.cli")
app = typer.Typer(help="Tag Forge backend CLI")


@app.command()
def init_db() -> None:
    """Create the SQLite schema."""
    settings.ensure_dirs()
    db.init_db()
    typer.echo(f"db ready: {settings.DB_PATH}")


@app.command("reset-db")
def reset_db(yes: bool = typer.Option(False, "--yes", "-y", help="skip confirmation")) -> None:
    """Drop and recreate the SQLite schema. Destructive - wipes all ingested data."""
    if settings.DB_PATH.exists() and not yes:
        typer.confirm(
            f"Wipe {settings.DB_PATH} and all ingested data?", abort=True
        )
    # Drop sidecar files too (WAL / SHM) so we don't leave inconsistent state.
    for suffix in ("", "-wal", "-shm"):
        p = settings.DB_PATH.with_name(settings.DB_PATH.name + suffix)
        if p.exists():
            p.unlink()
    db.init_db()
    typer.echo(f"reset complete: {settings.DB_PATH}")


@app.command("classify-ratings")
def classify_ratings_cmd(
    only_missing: bool = typer.Option(
        True, "--only-missing/--all", help="restrict to images with no rating yet"
    ),
    overwrite_inferred: bool = typer.Option(
        False,
        "--overwrite-inferred",
        help="also re-infer images whose current rating came from the classifier",
    ),
) -> None:
    """Backfill Image.rating from the curated tag-rating dict."""
    db.init_db()
    from . import jobs as jobs_mod
    from .routes.classify import _run_classify_ratings

    job_id = jobs_mod.create_job(
        "classify_ratings",
        "infer scene ratings",
        {"only_missing": only_missing, "overwrite_inferred": overwrite_inferred},
    )
    typer.echo(f"job id: {job_id}")
    _run_classify_ratings(
        job_id,
        {"only_missing": only_missing, "overwrite_inferred": overwrite_inferred},
    )
    job = jobs_mod.get_job(job_id)
    typer.echo(f"status: {job.status if job else 'unknown'}")
    if job and job.detail_json:
        typer.echo(job.detail_json)


@app.command("fetch-booru")
def fetch_booru_cmd(
    site: str = typer.Option("danbooru", help="danbooru | aibooru"),
    mode: str = typer.Option("popular", help="popular | rank | score | tag_search"),
    date: Optional[str] = typer.Option(None, help="single day for popular (YYYY-MM-DD, default today)"),
    date_min: Optional[str] = typer.Option(None, help="range start — with --date-max iterates per day (popular/rank)"),
    date_max: Optional[str] = typer.Option(None, help="range end"),
    scale: str = typer.Option("day", help="popular scale: day | week | month"),
    rating: Optional[str] = typer.Option(None, help="e.g. s or g,s"),
    score_min: Optional[int] = typer.Option(None, help="score floor"),
    tags: str = typer.Option("", help="space/comma-separated tags (tag_search mode)"),
    limit: int = typer.Option(200),
    pages: int = typer.Option(1),
    label: Optional[str] = typer.Option(None),
    classify_after: bool = typer.Option(
        False,
        "--classify-after",
        help="chain Stage-3 GPT classification of new tags + scene rebuild",
    ),
) -> None:
    """Fetch booru posts headlessly — Task Scheduler friendly.

    Example (nightly backfill of the last week's popular posts):
        python -m backend.cli fetch-booru --mode popular
        python -m backend.cli fetch-booru --mode rank --date-min 2026-07-01 --date-max 2026-07-07
    """
    db.init_db()
    from . import jobs as jobs_mod

    params = {
        "site": site,
        "mode": mode,
        "date": date,
        "date_min": date_min,
        "date_max": date_max,
        "scale": scale,
        "rating": rating,
        "score_min": score_min,
        "tags": [t for t in tags.replace(",", " ").split() if t],
        "limit": limit,
        "pages": pages,
        "label": label,
        "classify_after": classify_after,
    }
    job_id = jobs_mod.create_job("fetch_booru", label or f"{site}:{mode} (cli)", params)
    typer.echo(f"job id: {job_id}")
    run_booru_fetch(job_id=job_id, params=params)
    job = jobs_mod.get_job(job_id)
    typer.echo(f"status: {job.status if job else 'unknown'}")
    if job:
        typer.echo(job.message or "")
        if job.error:
            typer.echo(f"error: {job.error}", err=True)
            raise typer.Exit(1)


@app.command()
def seed_tag_tree(
    url: str = typer.Option(
        "https://raw.githubusercontent.com/KohakuBlueleaf/danbooru-tag-tree/main/tag_tree.json",
        help="Source URL for tag_tree.json",
    ),
) -> None:
    """Download tag_tree.json from KohakuBlueleaf/danbooru-tag-tree."""
    settings.ensure_dirs()
    typer.echo(f"downloading {url} -> {settings.TAG_TREE_PATH}")
    req = urllib.request.Request(url, headers={"User-Agent": settings.DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    settings.TAG_TREE_PATH.write_bytes(data)
    reload_caches()
    typer.echo(f"saved {len(data):,} bytes")


@app.command()
def ingest_metadata(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    label: Optional[str] = typer.Option(None, "--label", "-l"),
    drop_artist: bool = typer.Option(True, "--drop-artist/--keep-artist"),
    drop_quality: bool = typer.Option(True, "--drop-quality/--keep-quality"),
    drop_character: bool = typer.Option(False, "--drop-character/--keep-character"),
) -> None:
    """Ingest a metadata.txt file synchronously (no UI)."""
    db.init_db()
    from . import jobs as jobs_mod

    label = label or f"cli:{path.stem}"
    job_id = jobs_mod.create_job(
        "ingest_metadata", label, {"path": str(path), "via": "cli"}
    )
    typer.echo(f"job id: {job_id}")
    run_metadata_ingest(
        job_id=job_id,
        path=path,
        label=label,
        drop_artist_tags=drop_artist,
        drop_quality_tags=drop_quality,
        drop_character_tags=drop_character,
    )
    job = jobs_mod.get_job(job_id)
    typer.echo(f"status: {job.status if job else 'unknown'}")
    if job and job.detail_json:
        typer.echo(job.detail_json)


@app.command()
def preview_metadata(path: Path, sample: int = 10) -> None:
    """Print a small parsed preview of a metadata.txt file."""
    data = preview_metadata_file(path, sample_size=sample)
    typer.echo(json.dumps(data, indent=2)[:8000])


@app.command()
def export(
    name: str,
    output_dir: Optional[Path] = typer.Option(None, "--out", "-o"),
    origin: Optional[str] = typer.Option(
        None, "--origin", help="local | booru — restrict by source provenance"
    ),
    rating: Optional[str] = typer.Option(None, "--rating"),
    score_min: Optional[int] = typer.Option(None, "--score-min"),
    max_rating: Optional[str] = typer.Option(
        None,
        "--max-rating",
        help="g|s|q|e — strip tags above this severity from every line",
    ),
    file_prefix: str = typer.Option("", "--file-prefix"),
    min_tag_count: int = typer.Option(2, "--min-tags"),
) -> None:
    """Run an export with default buckets to the chosen directory."""
    db.init_db()
    out = Path(output_dir).expanduser() if output_dir else (
        settings.EXPORTS_DIR / name
    )
    manifest = build_export(
        name=name,
        output_dir=out,
        buckets=list(DEFAULT_BUCKETS),
        origin=origin,
        ratings=[rating] if rating else [],
        score_min=score_min,
        max_rating=max_rating,
        file_prefix=file_prefix,
        min_tag_count=min_tag_count,
    )
    typer.echo(json.dumps(manifest, indent=2))


@app.command()
def serve(
    host: str = settings.API_HOST,
    port: int = settings.API_PORT,
    reload: bool = False,
) -> None:
    """Run the FastAPI server with uvicorn."""
    import uvicorn

    db.init_db()
    uvicorn.run("backend.app:app", host=host, port=port, reload=reload)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app()


if __name__ == "__main__":
    main()

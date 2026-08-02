"""Smoke test: end-to-end ingest of a small slice + export verification."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import db, settings
from backend.ingest.exporter import build_export
from backend.ingest.metadata_parser import iter_metadata_records
from backend.ingest.runner import run_metadata_ingest
from backend import jobs as jobs_mod


def make_slice(src: Path, dst: Path, n: int = 80) -> int:
    """Copy first ``n`` records into ``dst`` preserving the file format."""
    SEP = "=" * 80
    with src.open("r", encoding="utf-8", errors="replace") as fin, dst.open(
        "w", encoding="utf-8"
    ) as fout:
        count = 0
        current_record: list[str] = []
        in_record = False
        prev_was_sep = False
        for line in fin:
            stripped = line.rstrip("\n").rstrip("\r")
            if stripped == SEP:
                if in_record:
                    # close current record (this is the trailing SEP before next File:)
                    # actually NAI format wraps with 3 sep lines surrounding File: ; the parser
                    # treats first sep => header, file line, second sep => start body, next first sep => flush
                    # So we just write everything verbatim and trust ``iter_metadata_records`` later.
                    pass
            current_record.append(line)
            if stripped.startswith("File: "):
                if count >= n:
                    break
                count += 1
        fout.writelines(current_record)
    return count


def main() -> None:
    # Path to a real metadata.txt dump: first CLI arg, else the configured
    # TAGFORGE_METADATA_FILE hint.
    src = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else settings.DEFAULT_METADATA_FILE
    )
    if src is None:
        sys.exit(
            "no metadata file: pass a path argument or set TAGFORGE_METADATA_FILE"
        )
    if not src.exists():
        sys.exit(f"missing: {src}")

    with tempfile.TemporaryDirectory(prefix="pf_smoke_") as tmp:
        tmp_path = Path(tmp)
        slice_file = tmp_path / "metadata_slice.txt"
        made = make_slice(src, slice_file, n=80)
        print(f"sliced {made} records -> {slice_file} ({slice_file.stat().st_size / 1024:.1f} KB)")

        # Wipe + rebuild the DB so this test is reproducible. Destructive:
        # requires --wipe so running the smoke test cannot silently destroy
        # a real corpus (set TAGFORGE_DB_PATH to point at a scratch file
        # if you want an isolated run instead).
        if settings.DB_PATH.exists():
            if "--wipe" not in sys.argv:
                sys.exit(
                    f"refusing to delete the existing database at {settings.DB_PATH}\n"
                    f"pass --wipe to confirm, or set TAGFORGE_DB_PATH to a scratch file"
                )
            for suffix in ("", "-wal", "-shm"):
                p = settings.DB_PATH.with_name(settings.DB_PATH.name + suffix)
                p.unlink(missing_ok=True)
        db.init_db()

        # Run the ingest synchronously
        job_id = jobs_mod.create_job("ingest_metadata", "smoke", {"path": str(slice_file)})
        run_metadata_ingest(
            job_id=job_id,
            path=slice_file,
            label="smoke",
            drop_artist_tags=True,
            drop_quality_tags=True,
            drop_character_tags=False,
        )
        job = jobs_mod.get_job(job_id)
        print("ingest job:", job.status, "-", job.message)

        # Export
        export_dir = tmp_path / "wildcards"
        manifest = build_export(
            name="smoke",
            output_dir=export_dir,
            buckets=["outfit", "pose", "expression", "background", "composition", "scene"],
            min_tag_count=1,
            deduplicate=True,
        )
        print("\nexport manifest:")
        print(f"  output_dir: {manifest['output_dir']}")
        for b, fp in manifest["files"].items():
            line_count = manifest["line_counts"].get(b, 0)
            print(f"  {b:>11} : {line_count} lines  ({fp})")
            # show first 3 lines of each file
            if Path(fp).exists() and line_count > 0:
                first = Path(fp).read_text(encoding="utf-8").splitlines()[:3]
                for ln in first:
                    print(f"        | {ln[:120]}")

        # Bucket coverage stats
        from sqlalchemy import func
        from sqlmodel import select
        from backend.models import Tag

        with db.session_scope() as s:
            total = s.exec(select(func.count(Tag.id))).one()
            other = s.exec(select(func.count(Tag.id)).where(Tag.bucket == "other")).one()
            by_bucket = s.exec(
                select(Tag.bucket, func.count(Tag.id)).group_by(Tag.bucket)
            ).all()
            print(f"\ntags: total={total} other={other} ({100*other/total:.1f}%)")
            for b, c in sorted(by_bucket, key=lambda x: -x[1]):
                print(f"  {b:>13} : {c}")


if __name__ == "__main__":
    main()

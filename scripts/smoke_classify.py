"""Verify stage-3 (echo provider) + scene_line rebuild flow without API keys."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func
from sqlmodel import select

from backend import db
from backend.ingest.stage3_llm import reclassify_residuals
from backend.models import SceneLine, Tag
from backend.routes.classify import _rebuild_scene_lines


def main() -> None:
    db.init_db()
    with db.session_scope() as s:
        before = s.exec(select(func.count(Tag.id)).where(Tag.bucket == "other")).one()
        scenes_before = s.exec(select(func.count(SceneLine.id))).one()
    print(f"before:  other={before}  scenes={scenes_before}")

    # Echo provider always returns 'other' so this exercises the cache+DB
    # write path without touching any external service. The expected
    # relabelled-count is 0 (every tag stays in 'other').
    result = reclassify_residuals(provider="echo", max_tags=10, batch_size=10)
    print(f"echo result: {result}")

    rebuilt = _rebuild_scene_lines()
    with db.session_scope() as s:
        after = s.exec(select(func.count(Tag.id)).where(Tag.bucket == "other")).one()
        scenes_after = s.exec(select(func.count(SceneLine.id))).one()
    print(f"after:   other={after}  scenes={scenes_after}  images_rebuilt={rebuilt}")


if __name__ == "__main__":
    main()

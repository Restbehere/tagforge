"""Standalone script: download tag_tree.json once and verify it loads.

Usage::

    python -m scripts.seed_tag_tree
    # or:
    python scripts/seed_tag_tree.py
"""

from __future__ import annotations

import json
import logging
import sys
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR.parent))

from backend import settings  # noqa: E402
from backend.ingest.tag_categorizer import _load_tag_tree, reload_caches  # noqa: E402


URL = "https://raw.githubusercontent.com/KohakuBlueleaf/danbooru-tag-tree/main/tag_tree.json"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings.ensure_dirs()
    dst = settings.TAG_TREE_PATH
    print(f"downloading {URL}\n  -> {dst}")
    req = urllib.request.Request(URL, headers={"User-Agent": settings.DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    dst.write_bytes(data)
    print(f"saved {len(data):,} bytes")

    reload_caches()
    mapping = _load_tag_tree()
    print(f"loaded {len(mapping):,} tag->bucket entries")
    buckets: dict[str, int] = {}
    for b in mapping.values():
        buckets[b] = buckets.get(b, 0) + 1
    for b, c in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"  {b:>15} : {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

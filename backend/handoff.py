"""Last split result, held for pickup by a browser userscript.

The NAI bridge userscript (running on novelai.net) polls
``GET /api/llm/handoff`` against this local server and injects new results
into the NovelAI prompt box. Process stores here automatically, so the
user's flow is: click Process in Tag Forge, watch the text appear in the
NAI tab.

In-memory only and deliberately tiny: one slot, monotonically increasing
sequence number so the poller can tell "new" from "seen". Restarting the
backend resets it, which is fine — a stale prompt is worthless anyway.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

_lock = threading.Lock()
_seq = 0
_payload: Optional[dict[str, Any]] = None
_stored_at: Optional[str] = None


def store(result: dict[str, Any]) -> int:
    """Keep the parts a browser-side consumer can use; return the new seq."""
    global _seq, _payload, _stored_at
    with _lock:
        _seq += 1
        _payload = {
            "base_prompt": result.get("base_prompt", ""),
            "characters": [
                {"name": c.get("name", ""), "prompt": c.get("prompt", "")}
                for c in (result.get("characters") or [])
            ],
            "mode": result.get("mode", ""),
            "include_speech": bool(result.get("include_speech")),
        }
        _stored_at = datetime.now(timezone.utc).isoformat()
        return _seq


def current() -> dict[str, Any]:
    with _lock:
        return {"seq": _seq, "stored_at": _stored_at, "result": _payload}

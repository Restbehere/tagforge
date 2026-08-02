"""Stage-3 classifier: LLM-assisted bucket assignment for hard residuals.

The classifier accepts either:

- ``provider='openai'`` (uses ``OPENAI_API_KEY``), OR
- ``provider='anthropic'`` (uses ``ANTHROPIC_API_KEY``), OR
- ``provider='echo'``    (a no-LLM dry-run that just labels everything ``other``;
                          handy for tests).

Results are persisted to ``backend/data/tag_classification_cache.json`` so a
single tag is never re-paid for. The DB row's ``bucket_source = 'llm'``.

Batched few-shot prompt template -- the model returns JSON
``{"tag_name": "bucket_name"}`` so we can fan that out cheaply.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from .. import db, jobs, settings
from ..models import LlmBatch, Tag
from .tag_history import record_change


logger = logging.getLogger(__name__)

ALLOWED = (
    "outfit",
    "pose",
    "expression",
    "background",
    "composition",
    "accessory",
    "extras",
    "other",
)

FEW_SHOT = [
    {"input": "serafuku", "bucket": "outfit"},
    {"input": "hand on hip", "bucket": "pose"},
    {"input": "wariza", "bucket": "pose"},
    {"input": "blush", "bucket": "expression"},
    {"input": "from above", "bucket": "composition"},
    {"input": "depth of field", "bucket": "composition"},
    {"input": "cherry blossoms", "bucket": "background"},
    {"input": "indoors", "bucket": "background"},
    {"input": "round glasses", "bucket": "accessory"},
    # extras = props / objects / food / animals / weapons / vehicles the
    # character is interacting with or that share the frame as an item
    {"input": "holding cup of tea", "bucket": "extras"},
    {"input": "katana", "bucket": "extras"},
    {"input": "robot dog", "bucket": "extras"},
    {"input": "cake", "bucket": "extras"},
    {"input": "motorcycle", "bucket": "extras"},
    {"input": "1girl", "bucket": "other"},
    {"input": "mature female", "bucket": "other"},
]


@dataclass
class Stage3Result:
    requested: int
    relabelled: int
    by_bucket: dict[str, int]
    elapsed_sec: float
    cache_path: str
    failed_batches: int = 0
    # Tag ids whose bucket changed — lets callers run a delta scene rebuild.
    changed_tag_ids: list[int] = field(default_factory=list)


def _cache_bak_path() -> Any:
    return settings.CLASSIFICATION_CACHE_PATH.with_suffix(".json.bak")


def _load_cache() -> dict[str, str]:
    """Load the LLM verdict cache; fall back to the .bak on corruption.

    A silently-empty cache would re-pay the LLM for every previously
    classified tag, so an unparseable file (with no usable backup) raises
    instead of returning {}.
    """
    path = settings.CLASSIFICATION_CACHE_PATH
    bak = _cache_bak_path()
    if not path.exists():
        if bak.exists():
            logger.warning("cache missing, restoring from %s", bak)
            data = json.loads(bak.read_text(encoding="utf-8"))
            _save_cache(data)
            return data
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if bak.exists():
            try:
                data = json.loads(bak.read_text(encoding="utf-8"))
            except Exception:
                pass
            else:
                logger.warning("cache corrupt (%s), restored from backup", exc)
                _save_cache(data)
                return data
        raise RuntimeError(
            f"classification cache is corrupt and no valid backup exists "
            f"({path}): {exc}. Fix or delete the file before re-running "
            f"(deleting re-pays the LLM for every cached tag)."
        ) from exc


_CACHE_LOCK = threading.Lock()


def _save_cache(cache: dict[str, str]) -> None:
    """Atomic write: temp file + os.replace, keeping the previous version
    as .bak so a crash can never leave a truncated cache behind.

    Merges with whatever is on disk under a lock: two concurrent classify
    jobs each hold their own snapshot, so a plain overwrite would silently
    discard the other's paid LLM verdicts."""
    path = settings.CLASSIFICATION_CACHE_PATH
    with _CACHE_LOCK:
        merged = dict(cache)
        if path.exists():
            try:
                disk = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                disk = {}
            if isinstance(disk, dict):
                # Our in-memory entries win; anything only on disk survives.
                merged = {**disk, **cache}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8"
        )
        if path.exists():
            os.replace(path, _cache_bak_path())
        os.replace(tmp, path)
    cache.update(merged)


def _system_prompt() -> str:
    examples = "\n".join(f"  {e['input']} -> {e['bucket']}" for e in FEW_SHOT)
    return (
        "You assign Danbooru-style tags to one of these buckets:\n"
        + ", ".join(ALLOWED)
        + "\n\nExamples:\n"
        + examples
        + "\n\nRespond with a single JSON object mapping tag name to bucket name. "
        + "Do not include any commentary."
    )


def _get_openai_client() -> Any:
    """Create one OpenAI client (thread-safe httpx pool, built-in retries)."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Stage 3 (openai) needs the `[llm]` extras. Install with:\n"
            "    .venv\\Scripts\\python -m pip install -e .\\backend[llm]\n"
            "(installs `openai` + `anthropic`)"
        ) from exc

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to Tag Forge/.env "
            "(copy .env.example and fill the key) or `set OPENAI_API_KEY=...` "
            "in your shell before launching the backend."
        )
    return OpenAI(api_key=api_key, timeout=90.0)


def _call_openai(model: str, tags: list[str], client: Any = None) -> dict[str, str]:
    if client is None:
        client = _get_openai_client()
    msg = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": "Tags: " + ", ".join(tags)},
    ]
    resp = client.chat.completions.create(
        model=model,
        messages=msg,  # type: ignore[arg-type]
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


def _get_anthropic_client() -> Any:
    """Create one Anthropic client. Raises on missing package/key so config
    errors abort on the main thread instead of failing every worker batch."""
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Stage 3 (anthropic) needs the `[llm]` extras. Install with:\n"
            "    .venv\\Scripts\\python -m pip install -e .\\backend[llm]"
        ) from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to Tag Forge/.env "
            "or set it in your shell before launching the backend."
        )
    return Anthropic(api_key=api_key)


def _call_anthropic(model: str, tags: list[str], client: Any = None) -> dict[str, str]:
    if client is None:
        client = _get_anthropic_client()
    msg = client.messages.create(
        model=model,
        system=_system_prompt(),
        max_tokens=4096,
        temperature=0,
        messages=[{"role": "user", "content": "Tags: " + ", ".join(tags)}],
    )
    text = "".join(part.text for part in msg.content if getattr(part, "type", "") == "text")
    # Anthropic doesn't have a JSON-object response_format; the system prompt
    # tells it to emit raw JSON. Find the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    return json.loads(text[start : end + 1])


def _echo(_model: str, tags: list[str]) -> dict[str, str]:
    return {t: "other" for t in tags}


_DISPATCH = {
    "openai": _call_openai,
    "anthropic": _call_anthropic,
    "echo": _echo,
}


def _select_residual_pairs(
    created_after: datetime | None = None,
) -> list[tuple[int, str]]:
    """(tag_id, name) for every unlocked tag still sitting in bucket=other.

    ``created_after`` narrows to tags created since that moment — used by
    the post-ingest chain so it only classifies tags the ingest introduced
    instead of the whole global backlog.
    """
    with db.session_scope() as s:
        stmt = (
            select(Tag)
            .where(Tag.bucket == "other")
            .where(Tag.locked == False)  # noqa: E712
        )
        if created_after is not None:
            stmt = stmt.where(Tag.created_at >= created_after)
        return [
            (t.id, t.name)
            for t in s.exec(stmt).all()
            if not _is_junk_tag_name(t.name)  # never pay the LLM for junk rows
        ]  # type: ignore[misc]


def _is_junk_tag_name(name: str) -> bool:
    """True for strings that cannot be real booru tag names — full prompt
    fragments, sentences, URLs — which occasionally end up as Tag rows via
    malformed metadata. They must never be sent to the LLM or cached: the
    cache ships in the repo, and prompt fragments in it are the owner's
    prompt history."""
    return "," in name or " " in name or "://" in name or len(name) > 70


def _merge_result_into_cache(cache: dict[str, str], result: dict[str, str]) -> None:
    """Re-key by canonical name (underscores) + clamp buckets to ALLOWED."""
    for raw_name, bucket in result.items():
        canon = str(raw_name).strip().lower().replace(" ", "_")
        if _is_junk_tag_name(canon):
            continue
        if bucket not in ALLOWED:
            bucket = "other"
        cache[canon] = bucket


def _apply_cache_to_tags(
    cache: dict[str, str],
    residual_pairs: list[tuple[int, str]],
    provider_model_label: str,
    job_id: int | None = None,
) -> tuple[int, dict[str, int], int, list[int]]:
    """Write cached bucket decisions onto Tag rows.

    Returns ``(relabelled, by_bucket, still_other, changed_tag_ids)`` —
    ``changed_tag_ids`` feeds the delta scene-line rebuild so only affected
    images get rebuilt.
    """
    relabelled = 0
    still_other = 0
    by_bucket: dict[str, int] = {}
    changed_tag_ids: list[int] = []
    with db.session_scope() as s:
        for tag_id, name in residual_pairs:
            new_bucket = cache.get(name)
            if not new_bucket:
                continue
            db_tag = s.get(Tag, tag_id)
            if db_tag is None or db_tag.locked:
                continue
            if new_bucket == "other":
                # GPT confirmed this tag has no useful bucket — mark it so the
                # queue stats can exclude it from "unprocessed" counts, but
                # don't move it out of other or record a history entry.
                still_other += 1
                if db_tag.bucket_source != "llm":
                    db_tag.bucket_source = "llm"
                    db_tag.updated_at = datetime.now(timezone.utc)
                    s.add(db_tag)
                continue
            record_change(
                s,
                db_tag,
                new_bucket=new_bucket,
                new_source="llm",
                new_confidence=0.85,
                model=provider_model_label,
                job_id=job_id,
            )
            db_tag.bucket = new_bucket
            db_tag.bucket_source = "llm"
            db_tag.confidence = 0.85
            db_tag.updated_at = datetime.now(timezone.utc)
            s.add(db_tag)
            relabelled += 1
            changed_tag_ids.append(tag_id)
            by_bucket[new_bucket] = by_bucket.get(new_bucket, 0) + 1
    return relabelled, by_bucket, still_other, changed_tag_ids


def reclassify_residuals(
    *,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    batch_size: int = 50,
    max_tags: int | None = None,
    concurrency: int = 6,
    job_id: int | None = None,
    created_after: datetime | None = None,
) -> Stage3Result:
    """Run the LLM over residual tags (those still bucket=other after stage 1+2)."""
    start = datetime.now(timezone.utc)
    if provider not in _DISPATCH:
        raise ValueError(f"unknown LLM provider: {provider}")
    concurrency = max(1, min(12, int(concurrency)))

    cache = _load_cache()

    residual_pairs = _select_residual_pairs(created_after=created_after)
    if max_tags is not None:
        residual_pairs = residual_pairs[:max_tags]

    to_query = [(tid, name) for tid, name in residual_pairs if name not in cache]
    logger.info(
        "stage-3 %s: %d residuals total, %d cached, %d to query (concurrency %d)",
        provider,
        len(residual_pairs),
        len(residual_pairs) - len(to_query),
        len(to_query),
        concurrency,
    )

    # Precompute display-name batches up front so workers stay pure.
    batches: list[list[str]] = [
        [name.replace("_", " ") for _tid, name in to_query[i : i + batch_size]]
        for i in range(0, len(to_query), batch_size)
    ]

    failed_batches = 0
    total = len(batches)
    if total:
        # One client per run — validated on the main thread so config errors
        # (missing package/key) abort the job with a clear message instead of
        # silently failing every worker batch. Both SDK clients are
        # thread-safe (shared httpx pool, built-in retries).
        client = None
        if provider == "openai":
            client = _get_openai_client()
        elif provider == "anthropic":
            client = _get_anthropic_client()

        def _worker(names: list[str]) -> dict[str, str]:
            # Workers ONLY call the API and return the parsed dict (or raise);
            # cache/tally mutation happens on the main thread in the drain loop.
            if provider == "openai":
                return _call_openai(model, names, client=client)
            if provider == "anthropic":
                return _call_anthropic(model, names, client=client)
            return _DISPATCH[provider](model, names)

        done = 0
        last_error: str | None = None
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(_worker, names): idx for idx, names in enumerate(batches)
            }
            for fut in as_completed(futures):
                done += 1
                try:
                    result = fut.result()
                except Exception as exc:
                    # Failed batches are not cached so they will be retried
                    # automatically on the next run.
                    last_error = str(exc)
                    logger.warning(
                        "LLM batch %d failed, skipping: %s", futures[fut], exc
                    )
                    failed_batches += 1
                else:
                    _merge_result_into_cache(cache, result)
                if done % 10 == 0:
                    _save_cache(cache)
                if job_id is not None:
                    jobs.update_job(
                        job_id,
                        progress=0.1 + 0.7 * (done / total),
                        message=f"LLM batch {done}/{total} ({failed_batches} failed)",
                    )
        _save_cache(cache)

        # Every single batch failing means a permanent per-call error (bad
        # model name, revoked key, ...) — surface it as a red job instead of
        # a green "0 relabelled, N skipped".
        if failed_batches == total:
            raise RuntimeError(
                f"all {total} LLM batches failed; last error: {last_error}"
            )

    relabelled, by_bucket, _still_other, changed_tag_ids = _apply_cache_to_tags(
        cache, residual_pairs, f"{provider}:{model}", job_id
    )

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    return Stage3Result(
        requested=len(residual_pairs),
        relabelled=relabelled,
        by_bucket=by_bucket,
        elapsed_sec=elapsed,
        cache_path=str(settings.CLASSIFICATION_CACHE_PATH),
        failed_batches=failed_batches,
        changed_tag_ids=changed_tag_ids,
    )


# ---------------------------------------------------------------------------
# OpenAI Batch API (async, ~50% cheaper, 24h completion window)
# ---------------------------------------------------------------------------

_TERMINAL_BATCH_STATES = {"completed", "failed", "expired", "cancelled"}


def _batch_completed_at(batch: Any) -> datetime | None:
    """OpenAI batches carry unix timestamps; convert to naive-UTC datetime."""
    ts = getattr(batch, "completed_at", None)
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _serialize_llm_batch(row: LlmBatch) -> dict[str, Any]:
    return {
        "id": row.id,
        "openai_batch_id": row.openai_batch_id,
        "status": row.status,
        "model": row.model,
        "tag_count": row.tag_count,
        "request_count": row.request_count,
        # Stored naive-UTC; attach the offset so new Date() in the frontend
        # parses as UTC instead of local time.
        "submitted_at": (
            row.submitted_at.replace(tzinfo=timezone.utc).isoformat()
            if row.submitted_at
            else None
        ),
        "completed_at": (
            row.completed_at.replace(tzinfo=timezone.utc).isoformat()
            if row.completed_at
            else None
        ),
        "applied": row.applied,
        "error": row.error,
    }


def submit_batch_job(
    *,
    model: str = "gpt-4o-mini",
    batch_size: int = 50,
    max_tags: int | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Submit residual tags to the OpenAI Batch API and record an LlmBatch row.

    Same residual selection + cache filtering as :func:`reclassify_residuals`;
    results are pulled in later via :func:`apply_batch`.
    """
    cache = _load_cache()
    residual_pairs = _select_residual_pairs()
    if max_tags is not None:
        residual_pairs = residual_pairs[:max_tags]
    to_query = [(tid, name) for tid, name in residual_pairs if name not in cache]

    if not to_query:
        logger.info("stage-3 batch: nothing to submit (all residuals cached)")
        return {
            "batch_id": None,
            "openai_batch_id": None,
            "tag_count": 0,
            "request_count": 0,
        }

    system = _system_prompt()
    lines: list[str] = []
    for i in range(0, len(to_query), batch_size):
        names = [name.replace("_", " ") for _tid, name in to_query[i : i + batch_size]]
        lines.append(
            json.dumps(
                {
                    "custom_id": f"b{i // batch_size}",
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": "Tags: " + ", ".join(names)},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0,
                    },
                }
            )
        )
    n_batches = len(lines)

    client = _get_openai_client()
    buf = io.BytesIO(("\n".join(lines) + "\n").encode("utf-8"))
    upload = client.files.create(file=("stage3.jsonl", buf), purpose="batch")
    batch = client.batches.create(
        input_file_id=upload.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    logger.info(
        "stage-3 batch: submitted %s (%d tags in %d requests)",
        batch.id,
        len(to_query),
        n_batches,
    )

    with db.session_scope() as s:
        row = LlmBatch(
            openai_batch_id=batch.id,
            status=getattr(batch, "status", None) or "submitted",
            model=model,
            tag_count=len(to_query),
            request_count=n_batches,
            job_id=job_id,
        )
        s.add(row)
        s.flush()
        row_id = row.id

    return {
        "batch_id": row_id,
        "openai_batch_id": batch.id,
        "tag_count": len(to_query),
        "request_count": n_batches,
    }


def refresh_llm_batches() -> list[dict[str, Any]]:
    """Sync every non-finalized LlmBatch row with OpenAI, then serialize all."""
    client: Any = None
    out: list[dict[str, Any]] = []
    with db.session_scope() as s:
        rows = list(
            s.exec(select(LlmBatch).order_by(LlmBatch.id.desc())).all()  # type: ignore[attr-defined]
        )
        for row in rows:
            # Only non-terminal rows can still change state — terminal rows
            # (applied or not) would cost one OpenAI round-trip per poll
            # forever for a status that can never move.
            if row.status not in _TERMINAL_BATCH_STATES:
                try:
                    if client is None:
                        client = _get_openai_client()
                    batch = client.batches.retrieve(row.openai_batch_id)
                except Exception:
                    # Offline / missing key / API hiccup — keep stored status.
                    logger.warning(
                        "could not refresh OpenAI batch %s", row.openai_batch_id
                    )
                else:
                    row.status = getattr(batch, "status", None) or row.status
                    completed = _batch_completed_at(batch)
                    if completed is not None:
                        row.completed_at = completed
                    if row.status == "failed":
                        errs = getattr(batch, "errors", None)
                        if errs is not None:
                            data = getattr(errs, "data", None) or []
                            msgs = [
                                getattr(e, "message", None) or str(e) for e in data
                            ]
                            row.error = "; ".join(m for m in msgs if m) or str(errs)
                    s.add(row)
            out.append(_serialize_llm_batch(row))
    return out


def apply_batch(batch_db_id: int, job_id: int | None = None) -> dict[str, Any]:
    """Download a completed OpenAI batch's output and apply it to Tag rows."""
    with db.session_scope() as s:
        row = s.get(LlmBatch, batch_db_id)
        if row is None:
            raise ValueError(f"LlmBatch {batch_db_id} not found")
        openai_batch_id = row.openai_batch_id
        row_model = row.model

    client = _get_openai_client()
    batch = client.batches.retrieve(openai_batch_id)
    status = getattr(batch, "status", None) or "unknown"
    output_file_id = getattr(batch, "output_file_id", None)
    if status != "completed" or not output_file_id:
        raise RuntimeError(
            f"OpenAI batch {openai_batch_id} is not ready to apply "
            f"(status={status}, output file {'present' if output_file_id else 'missing'})"
        )

    if job_id is not None:
        jobs.update_job(job_id, progress=0.2, message="downloading batch output")
    content = client.files.content(output_file_id).text

    cache = _load_cache()
    parsed_lines = 0
    failed_lines = 0
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            body = obj["response"]["body"]
            raw = body["choices"][0]["message"]["content"] or "{}"
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("batch line did not decode to a JSON object")
        except Exception:
            failed_lines += 1
            continue
        _merge_result_into_cache(cache, result)
        parsed_lines += 1
    _save_cache(cache)

    if job_id is not None:
        jobs.update_job(
            job_id,
            progress=0.4,
            message=f"applying {parsed_lines} result lines ({failed_lines} failed)",
        )

    residual_pairs = _select_residual_pairs()
    relabelled, by_bucket, _still_other, changed_tag_ids = _apply_cache_to_tags(
        cache, residual_pairs, f"openai-batch:{row_model}", job_id
    )

    with db.session_scope() as s:
        row = s.get(LlmBatch, batch_db_id)
        if row is not None:
            row.applied = True
            row.status = status
            completed = _batch_completed_at(batch)
            if completed is not None:
                row.completed_at = completed
            s.add(row)

    return {
        "relabelled": relabelled,
        "by_bucket": by_bucket,
        "parsed_lines": parsed_lines,
        "failed_lines": failed_lines,
        "changed_tag_ids": changed_tag_ids,
    }

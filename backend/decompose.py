"""See-through layer-decomposition service.

Wraps the external see-through repo (own conda env) behind a small FIFO
queue: items are DecompItem rows, a single daemon worker drains them by
running the repo's inference scripts as subprocesses and parsing their
tqdm output into progress. Artifacts land under ``data/decompose/out/<id>/``
— the pipeline itself writes the layered .psd plus a folder of per-layer
transparent PNGs, which the UI serves directly.
"""

from __future__ import annotations

import codecs
import json
import logging
import re
import subprocess
import threading
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from . import db, settings
from .models import DecompItem, Preset

logger = logging.getLogger("tagforge.decompose")

INPUTS_DIR = settings.DECOMPOSE_DIR / "inputs"
OUT_DIR = settings.DECOMPOSE_DIR / "out"

# Params accepted from the UI (everything the inference scripts expose).
DEFAULT_PARAMS: dict[str, Any] = {
    "pipeline": "full",  # full | quantized | blockswap
    "resolution": 1280,
    "resolution_depth": 768,
    "inference_steps": 30,
    "inference_steps_depth": -1,  # -1 = model default (full pipeline only)
    "seed": 42,
    "tblr_split": False,
    "group_offload": False,
    "gpu": 0,
}

_ALLOWED_STATUS = {"queued", "running", "done", "error", "cancelled"}

_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_running_procs: dict[int, subprocess.Popen] = {}
_procs_lock = threading.Lock()

# Files in the layers dir that are not semantic layers.
_NON_LAYER_FILES = {"src_img.png", "src_head.png", "reconstruction.png", "info.json"}


# ---------------------------------------------------------------------------
# Config (paths) — stored in the generic preset table like backup mirrors.
# ---------------------------------------------------------------------------


def _config_defaults() -> dict[str, str]:
    return {
        "python_path": str(settings.SEETHROUGH_PYTHON),
        "repo_dir": str(settings.SEETHROUGH_REPO_DIR),
        "layerdiff_dir": str(settings.SEETHROUGH_LAYERDIFF_DIR),
        "depth_dir": str(settings.SEETHROUGH_DEPTH_DIR),
    }


def load_config() -> dict[str, str]:
    cfg = _config_defaults()
    with db.session_scope() as s:
        row = s.exec(
            select(Preset).where(Preset.kind == "config", Preset.name == "decompose")
        ).first()
        if row is not None:
            try:
                stored = json.loads(row.data_json or "{}")
            except Exception:
                stored = {}
            for key in cfg:
                v = stored.get(key)
                if isinstance(v, str) and v.strip():
                    cfg[key] = v.strip()
    return cfg


def save_config(update: dict[str, Any]) -> dict[str, str]:
    defaults = _config_defaults()
    cfg = load_config()
    for key in defaults:
        v = update.get(key)
        if isinstance(v, str):
            # A cleared field resets that path back to the built-in default.
            cfg[key] = v.strip() or defaults[key]
    with db.session_scope() as s:
        row = s.exec(
            select(Preset).where(Preset.kind == "config", Preset.name == "decompose")
        ).first()
        payload = json.dumps(cfg)
        if row is None:
            s.add(Preset(kind="config", name="decompose", data_json=payload))
        else:
            row.data_json = payload
            row.updated_at = datetime.utcnow()
            s.add(row)
    return cfg


def env_status() -> dict[str, Any]:
    """Health checks the UI shows as status dots."""
    cfg = load_config()
    repo = Path(cfg["repo_dir"])
    script = repo / "inference" / "scripts" / "inference_psd.py"
    layerdiff = Path(cfg["layerdiff_dir"])
    depth = Path(cfg["depth_dir"])
    with db.session_scope() as s:
        queued = len(s.exec(select(DecompItem).where(DecompItem.status == "queued")).all())
        running = s.exec(select(DecompItem).where(DecompItem.status == "running")).first()
        # Read the id inside the session — session_scope expires attributes
        # on commit, so touching it afterwards raises DetachedInstanceError.
        running_id = running.id if running else None
    return {
        "python_ok": Path(cfg["python_path"]).is_file(),
        "repo_ok": script.is_file(),
        "layerdiff_ok": (layerdiff / "unet").is_dir() and (layerdiff / "trans_vae").is_dir(),
        "depth_ok": (depth / "unet").is_dir(),
        "queued": queued,
        "running_item_id": running_id,
        "config": cfg,
    }


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def sanitize_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    p = dict(DEFAULT_PARAMS)
    raw = raw or {}
    if raw.get("pipeline") in {"full", "quantized", "blockswap"}:
        p["pipeline"] = raw["pipeline"]
    for key, lo, hi in (
        ("resolution", 256, 2048),
        ("resolution_depth", -1, 2048),
        ("inference_steps", 1, 200),
        ("inference_steps_depth", -1, 200),
        ("seed", 0, 2**31 - 1),
        ("gpu", 0, 8),
    ):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            p[key] = int(max(lo, min(hi, int(v))))
    for key in ("tblr_split", "group_offload"):
        if isinstance(raw.get(key), bool):
            p[key] = raw[key]
    return p


def enqueue(input_path: str, original_name: str, params: dict[str, Any]) -> DecompItem:
    with db.session_scope() as s:
        item = DecompItem(
            original_name=original_name,
            input_path=str(input_path),
            params_json=json.dumps(sanitize_params(params)),
            status="queued",
            message="waiting in queue",
        )
        s.add(item)
        s.flush()
        s.refresh(item)
        s.expunge(item)
    ensure_worker()
    return item


def cancel_item(item_id: int) -> str:
    """Cancel a queued item, or kill the subprocess of a running one.

    Commits 'cancelled' *before* killing: the kill unblocks the worker's
    read loop, and its post-wait check must already see the cancelled row
    or it would mislabel the kill as a pipeline error.
    """
    with db.session_scope() as s:
        item = s.get(DecompItem, item_id)
        if item is None:
            raise ValueError("item not found")
        status = item.status
        if status in {"queued", "running"}:
            item.status = "cancelled"
            item.message = "cancelled"
            item.finished_at = datetime.utcnow()
            s.add(item)
            status = "cancelled"
    if status == "cancelled":
        with _procs_lock:
            proc = _running_procs.get(item_id)
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    return status


def ensure_worker() -> None:
    """Start the single queue-drain thread if it isn't running."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_worker_loop, name="decompose-worker", daemon=True
        )
        _worker_thread.start()


def recover_orphans() -> None:
    """Items stuck 'running' after a backend restart can never finish."""
    with db.session_scope() as s:
        for item in s.exec(
            select(DecompItem).where(DecompItem.status == "running")
        ).all():
            with _procs_lock:
                if item.id in _running_procs:
                    continue
            item.status = "error"
            item.error = "backend restarted while this item was running — requeue it"
            item.finished_at = datetime.utcnow()
            s.add(item)


def _next_queued_id() -> Optional[int]:
    with db.session_scope() as s:
        nxt = s.exec(
            select(DecompItem)
            .where(DecompItem.status == "queued")
            .order_by(DecompItem.id)  # type: ignore[arg-type]
        ).first()
        return nxt.id if nxt else None


def _worker_loop() -> None:
    global _worker_thread
    recover_orphans()
    while True:
        next_id = _next_queued_id()
        if next_id is None:
            # Re-check under the lock before exiting: an enqueue that just
            # committed may have seen this thread still alive and skipped
            # starting a replacement. Clearing _worker_thread inside the
            # same critical section makes ensure_worker's liveness check
            # race-free.
            with _worker_lock:
                next_id = _next_queued_id()
                if next_id is None:
                    if _worker_thread is threading.current_thread():
                        _worker_thread = None
                    return
        try:
            _process(next_id)
        except Exception as exc:
            logger.exception("decompose item %s crashed", next_id)
            _finish(next_id, status="error", error=str(exc))


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------


def _update(item_id: int, **fields: Any) -> None:
    with db.session_scope() as s:
        item = s.get(DecompItem, item_id)
        if item is None:
            return
        for k, v in fields.items():
            setattr(item, k, v)
        s.add(item)


def _finish(
    item_id: int, *, status: str, error: str | None = None, **fields: Any
) -> None:
    with db.session_scope() as s:
        item = s.get(DecompItem, item_id)
        if item is None:
            return
        # A user cancel is final — a racing done/error from the worker
        # must not overwrite it.
        if item.status == "cancelled" and status != "cancelled":
            return
        item.status = status
        item.error = error
        item.finished_at = datetime.utcnow()
        for k, v in fields.items():
            setattr(item, k, v)
        s.add(item)


def _build_command(
    cfg: dict[str, str], params: dict[str, Any], input_path: str, out_dir: Path
) -> list[str]:
    pipeline = params["pipeline"]
    script = {
        "full": "inference/scripts/inference_psd.py",
        "quantized": "inference/scripts/inference_psd_quantized.py",
        "blockswap": "inference/scripts/inference_psd_blockswap.py",
    }[pipeline]
    cmd = [
        cfg["python_path"],
        script,
        "--srcp", input_path,
        "--save_dir", str(out_dir),
        "--seed", str(params["seed"]),
        "--resolution", str(params["resolution"]),
        "--resolution_depth", str(params["resolution_depth"]),
        "--save_to_psd",
    ]
    if params["tblr_split"]:
        cmd.append("--tblr_split")

    if pipeline == "full":
        cmd += [
            "--inference_steps", str(params["inference_steps"]),
            "--inference_steps_depth", str(params["inference_steps_depth"]),
            "--repo_id_layerdiff", cfg["layerdiff_dir"],
            "--repo_id_depth", cfg["depth_dir"],
        ]
        if params["group_offload"]:
            cmd.append("--group_offload")
    elif pipeline == "quantized":
        # NF4 repos are the script's own defaults (auto-download on first
        # use); group_offload defaults ON there, so pass the negation.
        cmd += ["--num_inference_steps", str(params["inference_steps"])]
        if not params["group_offload"]:
            cmd.append("--no_group_offload")
    else:  # blockswap — depth repo is not overridable in that script
        cmd += [
            "--num_inference_steps", str(params["inference_steps"]),
            "--repo_id_layerdiff", cfg["layerdiff_dir"],
        ]
    return cmd


# tqdm writes "\r 47%|####      | 14/30 [00:35<00:40, ...]". The n/total
# part lets us tell the actual diffusion loop apart from the many short
# model-loading bars (checkpoint shards etc.) that also reach 100%.
_TQDM_RE = re.compile(r"(\d{1,3})%\|.*?\|\s*(\d+)/(\d+)")

# (marker, progress_at_marker, stage_span) — stage percents interpolate.
_STAGES = (
    ("running layerdiff", 0.05, 0.55),
    ("running marigold", 0.62, 0.24),
)


def _process(item_id: int) -> None:
    cfg = load_config()
    with db.session_scope() as s:
        item = s.get(DecompItem, item_id)
        if item is None or item.status != "queued":
            return
        input_path = item.input_path
        params = sanitize_params(json.loads(item.params_json or "{}"))
        item.status = "running"
        item.started_at = datetime.utcnow()
        item.message = "starting pipeline…"
        item.progress = 0.02
        s.add(item)

    out_dir = OUT_DIR / str(item_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = _build_command(cfg, params, input_path, out_dir)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(params["gpu"])
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")

    logger.info("decompose #%s: %s", item_id, " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cfg["repo_dir"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        _finish(item_id, status="error", error=f"failed to launch pipeline: {exc}")
        return

    with _procs_lock:
        _running_procs[item_id] = proc

    # A cancel may have landed between the 'running' commit and the proc
    # registration above — it had nothing to kill then, so honor it now.
    with db.session_scope() as s:
        row = s.get(DecompItem, item_id)
        if row is not None and row.status == "cancelled":
            try:
                proc.kill()
            except Exception:
                pass

    tail: list[str] = []
    stage_idx = -1
    last_written = 0.0
    # Binary pipe + read1: returns as soon as any output exists. A text-mode
    # read(n) would block until n chars accumulate, freezing progress across
    # the pipeline's quiet phases (model loading etc.).
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        assert proc.stdout is not None
        buf = ""
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            buf += decoder.decode(chunk)
            # tqdm separates updates with \r; real lines with \n.
            *parts, buf = re.split(r"[\r\n]", buf)
            for line in parts:
                line = line.strip()
                if not line:
                    continue
                tail.append(line)
                if len(tail) > 60:
                    tail.pop(0)
                low = line.lower()
                for i, (marker, base, _span) in enumerate(_STAGES):
                    if marker in low and i > stage_idx:
                        stage_idx = i
                        _update(
                            item_id,
                            progress=base,
                            message=marker.replace("running ", "") + "…",
                        )
                m = _TQDM_RE.search(line)
                if m and stage_idx >= 0:
                    pct, total = int(m.group(1)), int(m.group(3))
                    # Layerdiff: only trust the bar whose total matches the
                    # configured step count. Marigold's totals vary with the
                    # model defaults, so just skip trivially short bars.
                    if stage_idx == 0 and total != params["inference_steps"]:
                        continue
                    if stage_idx == 1 and total < 4:
                        continue
                    base, span = _STAGES[stage_idx][1], _STAGES[stage_idx][2]
                    frac = base + span * min(100, pct) / 100
                    if frac - last_written >= 0.01:
                        last_written = frac
                        stage_name = _STAGES[stage_idx][0].replace("running ", "")
                        _update(
                            item_id,
                            progress=frac,
                            message=f"{stage_name} {pct}%",
                        )
        proc.wait()
    finally:
        with _procs_lock:
            _running_procs.pop(item_id, None)
        # If the read loop died (e.g. a locked-DB write raised), don't leak
        # a live GPU pipeline the next queue item would then race against.
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass

    # A cancel may have killed the process and already marked the row.
    with db.session_scope() as s:
        current = s.get(DecompItem, item_id)
        if current is not None and current.status == "cancelled":
            return

    stem = Path(input_path).stem
    layers = out_dir / stem

    if proc.returncode != 0:
        _finish(
            item_id,
            status="error",
            error="pipeline exited with code "
            f"{proc.returncode}:\n"
            + _explain_failure(layers, "\n".join(tail[-12:])),
        )
        return

    # Locate artifacts: <out>/<stem>.psd + <out>/<stem>/ layer PNGs.
    psd = out_dir / f"{stem}.psd"
    depth_psd = out_dir / f"{stem}_depth.psd"
    if not psd.is_file() or not layers.is_dir():
        _finish(
            item_id,
            status="error",
            error=(
                "pipeline finished but expected outputs were not found "
                f"({psd.name}, {layers.name}/)"
            ),
            # Keep whatever layer PNGs exist browsable even without a PSD.
            layers_dir=str(layers) if layers.is_dir() else None,
        )
        return

    _finish(
        item_id,
        status="done",
        progress=1.0,
        message="done",
        psd_path=str(psd),
        depth_psd_path=str(depth_psd) if depth_psd.is_file() else None,
        layers_dir=str(layers),
    )


def _explain_failure(layers_dir: Path, tail: str) -> str:
    """Prefix a raw pipeline traceback with a plain-English cause.

    The model's own stack traces are opaque — an empty layer used to
    surface as a bare OpenCV ``!ssize.empty()`` assertion. This reads the
    pipeline's own stdout markers plus which artefacts reached disk, so it
    needs no image library (the backend venv has no Pillow).
    """
    if not layers_dir.is_dir():
        return (
            "The pipeline stopped before writing any layers — usually a model "
            "or VRAM problem rather than the image itself.\n\n" + tail
        )

    written = {p.stem for p in layers_dir.glob("*.png")}
    body = sorted(written - {"src_img", "src_head"})
    if not body:
        return "The pipeline started but produced no layers.\n\n" + tail

    notes: list[str] = []
    # The patched pipeline announces a skipped head pass on stdout.
    if "no head found" in tail or "head crop came out empty" in tail:
        notes.append(
            "No head was found in this image (a fully draped or occluded "
            "subject, a back view, or a crop above the shoulders), so the "
            "head-detail stage was skipped."
        )
    elif "src_head" not in written and "head" in written:
        notes.append(
            "The run stopped during the head-detail stage — the body layers "
            "were written but the head crop was never produced."
        )
    notes.append(f"Layers written before the failure: {', '.join(body)}.")
    return "\n".join(notes) + "\n\n" + tail


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def list_layers(item: DecompItem) -> dict[str, Any]:
    """Enumerate the per-layer PNGs the pipeline saved (README-style pieces)."""
    out: dict[str, Any] = {"layers": [], "has_reconstruction": False, "has_src": False}
    if not item.layers_dir:
        return out
    root = Path(item.layers_dir)
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.png")):
        name = p.name
        if name in _NON_LAYER_FILES or name.endswith("_depth.png"):
            continue
        out["layers"].append({"name": p.stem, "file": name})
    out["has_reconstruction"] = (root / "reconstruction.png").is_file()
    out["has_src"] = (root / "src_img.png").is_file()
    return out


def resolve_asset(item: DecompItem, filename: str) -> Path | None:
    """Safely resolve a filename inside the item's layers dir (no traversal)."""
    if not item.layers_dir:
        return None
    root = Path(item.layers_dir)
    if not root.is_dir():
        return None
    # Exact basename match against the actual directory listing.
    if filename not in {p.name for p in root.iterdir() if p.is_file()}:
        return None
    return root / filename


# ---------------------------------------------------------------------------
# Repo update indicator
# ---------------------------------------------------------------------------


def _git(repo: str, *args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        res = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return res.returncode, (res.stdout or res.stderr or "").strip()
    except Exception as exc:
        return 1, str(exc)


def repo_status(fetch: bool = False) -> dict[str, Any]:
    repo = load_config()["repo_dir"]
    rc, commit = _git(repo, "rev-parse", "--short", "HEAD")
    if rc != 0:
        return {"ok": False, "error": f"not a git repo? {commit}"}
    _, branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    fetch_error = None
    if fetch:
        rc, out = _git(repo, "fetch", "--quiet", timeout=45)
        if rc != 0:
            fetch_error = out or "git fetch failed"
    rc, behind = _git(repo, "rev-list", "--count", "HEAD..@{upstream}")
    behind_n = int(behind) if rc == 0 and behind.isdigit() else None
    return {
        "ok": True,
        "commit": commit,
        "branch": branch,
        "behind": behind_n,
        "fetched": fetch and fetch_error is None,
        "fetch_error": fetch_error,
        "checked_at": datetime.utcnow().isoformat() + "+00:00",
    }


def repo_update() -> dict[str, Any]:
    repo = load_config()["repo_dir"]
    rc, out = _git(repo, "pull", "--ff-only", timeout=120)
    status = repo_status(fetch=False)
    return {"ok": rc == 0, "output": out[-2000:], **{"status": status}}

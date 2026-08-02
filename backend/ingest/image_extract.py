"""Read generation metadata straight out of image files.

Replaces the old two-step workflow (run a separate extractor tool, then
import its ``metadata.txt``) — this yields the same
:class:`MetadataRecord` objects the text parser produces, so everything
downstream is unchanged.

Two sources are tried per image:

1. **LSB steganography** — NovelAI hides a gzipped JSON blob in the least
   significant bits of the alpha channel ("stealth PNG"). Survives EXIF
   stripping, so it is tried first.
2. **PNG text chunks / EXIF** — NovelAI's plain ``Description``/``Comment``
   fields, and the Stable Diffusion WebUI ``parameters`` string.

Partial reads are treated as failure rather than salvaged: a truncated
LSB stream yields garbage that looks like data, and a half-parsed record
would poison the corpus. An image we cannot read cleanly is simply
reported as having no metadata.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterator, Optional

from .metadata_parser import MetadataRecord
from .prompt_cleaner import extract_pipe_block_options


logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"})

# Magic headers written by the various stealth-PNG implementations.
_LSB_MAGICS: tuple[bytes, ...] = (
    b"stealth_pngcomp",
    b"stealth_png",
    b"stealthpng",
    b"novelai",
    b"sd_metadata",
)

# A stealth payload is a few KB; anything past this is a misread, not data.
_MAX_LSB_PAYLOAD = 4 * 1024 * 1024


class _BitsExhausted(RuntimeError):
    """Ran off the end of the pixel data mid-value."""


class LSBExtractor:
    """Reads bytes out of the alpha channel's least significant bits.

    Column-major, matching the stealth-PNG writers: walk down each column
    before moving right.
    """

    def __init__(self, data: Any) -> None:
        self.data = data
        self.rows, self.cols, self.dim = data.shape
        self.bits = 0
        self.byte = 0
        self.row = 0
        self.col = 0

    def reset(self) -> None:
        self.bits = self.byte = self.row = self.col = 0

    def _extract_next_bit(self) -> None:
        # The original silently did nothing once the cursor ran past the
        # image, so get_one_byte() spun forever waiting for an eighth bit
        # (the old tool worked around it with a per-image timeout). Failing
        # loudly is both correct and faster.
        if self.row >= self.rows or self.col >= self.cols:
            raise _BitsExhausted
        bit = int(self.data[self.row, self.col, self.dim - 1]) & 1
        self.bits += 1
        self.byte = (self.byte << 1) | bit
        self.row += 1
        if self.row == self.rows:
            self.row = 0
            self.col += 1

    def get_one_byte(self) -> int:
        while self.bits < 8:
            self._extract_next_bit()
        byte = self.byte
        self.bits = 0
        self.byte = 0
        return byte

    def get_next_n_bytes(self, n: int) -> bytes:
        return bytes(self.get_one_byte() for _ in range(n))

    def read_32bit_integer(self) -> int:
        return int.from_bytes(self.get_next_n_bytes(4), byteorder="big")


def extract_metadata_lsb(path: Path) -> Optional[dict]:
    """The gzipped JSON blob hidden in the alpha channel, if present."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:  # pragma: no cover - dependency guard
        logger.warning("Pillow/numpy missing — LSB extraction unavailable")
        return None

    try:
        with Image.open(path) as img:
            if img.mode != "RGBA":
                return None  # no alpha channel, nothing to hide data in
            arr = np.array(img)
    except Exception:
        return None

    if arr.ndim != 3 or arr.shape[-1] != 4:
        return None

    reader = LSBExtractor(arr)
    for magic in _LSB_MAGICS:
        reader.reset()
        try:
            if reader.get_next_n_bytes(len(magic)) != magic:
                continue
            length = reader.read_32bit_integer() // 8
            if not 0 < length <= _MAX_LSB_PAYLOAD:
                continue
            payload = reader.get_next_n_bytes(length)
        except _BitsExhausted:
            # Header matched but the image is too small to hold the payload
            # it claims — a coincidence, not data.
            continue

        for decode in (lambda b: gzip.decompress(b).decode("utf-8"),
                       lambda b: b.decode("utf-8")):
            try:
                data = json.loads(decode(payload))
            except Exception:
                continue
            if isinstance(data, dict):
                return data
    return None


def extract_metadata_chunks(path: Path) -> Optional[dict]:
    """PNG text chunks / EXIF — NovelAI's plain fields and SD's parameters."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - dependency guard
        return None
    try:
        with Image.open(path) as img:
            info: dict[str, Any] = {}
            for key, value in (img.info or {}).items():
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", errors="ignore")
                    except Exception:
                        value = str(value)
                info[key] = value
            info.setdefault("_width", img.width)
            info.setdefault("_height", img.height)
            return info or None
    except Exception:
        return None


_SD_PATTERNS = {
    "steps": (r"Steps:\s*(\d+)", int),
    "cfg_scale": (r"CFG scale:\s*([\d.]+)", float),
    "seed": (r"Seed:\s*(\d+)", int),
    "model": (r"Model:\s*([^,\n]+)", str),
}
_SD_SIZE_RE = re.compile(r"Size:\s*(\d+)x(\d+)")


def _apply_sd_parameters(rec: MetadataRecord, params_text: str) -> None:
    """Parse the Stable Diffusion WebUI ``parameters`` blob into the record."""
    negative_at = params_text.find("Negative prompt:")
    settings_at = params_text.find("Steps:")

    if negative_at != -1:
        rec.prompt = params_text[:negative_at].strip()
        end = settings_at if settings_at > negative_at else len(params_text)
        rec.negative = params_text[negative_at + len("Negative prompt:"): end].strip()
    elif settings_at != -1:
        rec.prompt = params_text[:settings_at].strip()
    else:
        rec.prompt = params_text.strip()

    tail = params_text[settings_at:] if settings_at != -1 else ""
    for field, (pattern, cast) in _SD_PATTERNS.items():
        m = re.search(pattern, tail)
        if m:
            try:
                setattr(rec, field, cast(m.group(1).strip()))
            except ValueError:
                pass
    m = _SD_SIZE_RE.search(tail)
    if m:
        rec.width = rec.width or int(m.group(1))
        rec.height = rec.height or int(m.group(2))


def _apply_nai_comment(rec: MetadataRecord, data: dict) -> None:
    """Pull the NovelAI generation parameters out of the Comment JSON.

    Mirrors metadata_parser._extract_record so a directly-extracted record
    is identical to one imported from a metadata.txt dump — notably
    preferring V4's resolved ``actual_prompts`` caption over the template
    and appending every ``|| a | b ||`` option so wildcards keep the range.
    """
    rec.raw_json = data

    template_prompt = data.get("prompt", "") or ""
    actual_caption = (
        ((data.get("actual_prompts") or {}).get("prompt", {}) or {}).get("base_caption", "")
        or ""
    ).strip()
    if actual_caption:
        options = extract_pipe_block_options(template_prompt)
        rec.prompt = actual_caption + (", " + ", ".join(options) if options else "")
    elif template_prompt:
        rec.prompt = template_prompt

    rec.negative = rec.negative or data.get("uc") or data.get("negative_prompt")
    rec.width = rec.width or data.get("width")
    rec.height = rec.height or data.get("height")
    rec.steps = rec.steps if rec.steps is not None else data.get("steps")
    rec.cfg_scale = rec.cfg_scale if rec.cfg_scale is not None else data.get("scale")
    rec.seed = rec.seed if rec.seed is not None else data.get("seed")


def record_from_image(path: Path) -> Optional[MetadataRecord]:
    """A :class:`MetadataRecord` for one image, or None if it carries none."""
    meta = extract_metadata_lsb(path) or extract_metadata_chunks(path)
    if not meta:
        return None

    rec = MetadataRecord(filename=path.name)
    rec.software = (meta.get("Software") or meta.get("software") or "").strip() or None
    rec.model = (meta.get("Source") or meta.get("source") or "").strip() or None
    if rec.model and "NovelAI" in (rec.software or ""):
        rec.nai_model = rec.model.replace("NovelAI Diffusion ", "").strip()

    width, height = meta.get("_width"), meta.get("_height")
    if isinstance(width, int):
        rec.width, rec.height = width, height

    # NovelAI: the Comment field carries the real generation parameters.
    comment = meta.get("Comment") or meta.get("comment")
    if isinstance(comment, str):
        try:
            comment = json.loads(comment)
        except Exception:
            comment = None
    if isinstance(comment, dict):
        _apply_nai_comment(rec, comment)
    elif "prompt" in meta and isinstance(meta.get("prompt"), (str, dict)):
        # Some stealth payloads are the parameter dict itself.
        _apply_nai_comment(rec, meta)

    # Stable Diffusion WebUI stores everything in one text blob.
    if not rec.prompt:
        params = meta.get("parameters") or meta.get("Parameters")
        if isinstance(params, str) and params.strip():
            _apply_sd_parameters(rec, params)
            rec.raw_json = rec.raw_json or {"parameters": params}

    # Last resort: NovelAI's plain Description field.
    if not rec.prompt:
        rec.prompt = (meta.get("Description") or meta.get("description") or "").strip()

    return rec if rec.prompt.strip() else None


def iter_image_records(
    folder: Path, recursive: bool = True
) -> Iterator[tuple[Path, Optional[MetadataRecord]]]:
    """Walk a folder yielding (path, record-or-None) for every image."""
    walker = folder.rglob("*") if recursive else folder.glob("*")
    for path in sorted(walker):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            yield path, record_from_image(path)
        except Exception:
            logger.exception("unreadable image %s", path)
            yield path, None


def count_images(folder: Path, recursive: bool = True) -> int:
    walker = folder.rglob("*") if recursive else folder.glob("*")
    return sum(
        1 for p in walker if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )

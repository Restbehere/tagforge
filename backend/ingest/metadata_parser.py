"""Stream-parse the giant ``metadata.txt`` file produced by the user's scraper.

Each record looks like::

    ================================================================================
    File: 12345_p0.png
    ================================================================================

    [DESCRIPTION/PROMPT]
    ...

    [SOFTWARE]
    NovelAI

    [MODEL]
    NovelAI Diffusion V4.5 4BDE2A90

    [GENERATION PARAMETERS]
    Prompt: ...
    Negative Prompt: ...
    Steps: 28
    Size: 832x1216
    ...

    [FULL METADATA JSON]
    { ... raw JSON ... }

The records are delimited by lines that are exactly 80 ``=`` characters with
``File: <name>.png`` between two such lines. We use a small state machine to
avoid loading the multi-GB file into memory.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from .prompt_cleaner import CleanedPrompt, clean_prompt, extract_pipe_block_options
from .tag_categorizer import classify_tag
from .tag_ratings import infer_image_rating


# Section markers
SEP = "=" * 80
_FILE_RE = re.compile(r"^File:\s*(.+\.(?:png|jpg|jpeg|webp))\s*$", re.IGNORECASE)
_SECTION_RE = re.compile(r"^\[(.+?)\]\s*$")
_SIZE_RE = re.compile(r"^Size:\s*(\d+)x(\d+)", re.IGNORECASE)
_SCORE_RE = re.compile(r"^(?:Steps|Scale|CFG Scale):\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass
class MetadataRecord:
    """A single parsed image record."""

    filename: str
    software: Optional[str] = None
    model: Optional[str] = None
    nai_model: Optional[str] = None
    prompt: str = ""
    negative: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    steps: Optional[int] = None
    cfg_scale: Optional[float] = None
    seed: Optional[int] = None
    raw_json: dict = field(default_factory=dict)

    def external_id(self) -> str:
        """A stable id derived from the source filename."""
        # filenames look like ``115842093_p2.png``; strip extension.
        return self.filename.rsplit(".", 1)[0]


def _section_lines(path: Path) -> Iterator[tuple[str, list[str]]]:
    """Yield (filename, lines) for each record in the file."""
    current_file: str | None = None
    buffer: list[str] = []
    prev_sep = False  # last non-empty line was the 80-= separator
    expecting_file_line = False

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n").rstrip("\r")

            if line == SEP:
                if expecting_file_line:
                    # second separator after File line -> we're now in body
                    expecting_file_line = False
                    continue
                # First separator: flush previous record if we had one
                if current_file is not None:
                    yield current_file, buffer
                    buffer = []
                    current_file = None
                prev_sep = True
                expecting_file_line = True  # next non-empty line should be "File: ..."
                continue

            if expecting_file_line:
                m = _FILE_RE.match(line)
                if m:
                    current_file = m.group(1).strip()
                # If it's not a File: line, keep waiting for one (rare junk lines)
                continue

            if current_file is not None:
                buffer.append(line)
            prev_sep = False

        if current_file is not None:
            yield current_file, buffer


def _parse_sections(lines: list[str]) -> dict[str, str]:
    """Group lines under their ``[SECTION NAME]`` headers."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1).strip().upper()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _parse_gen_params(text: str) -> dict[str, str]:
    """Extract ``Key: value`` lines from GENERATION PARAMETERS block."""
    out: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in text.split("\n"):
        if ":" in line and not line.startswith(" "):
            head, _, rest = line.partition(":")
            head = head.strip()
            # Heuristic: keys are short capitalised tokens.
            if head and len(head) < 40 and head[0].isalpha() and "  " not in head:
                if current_key is not None:
                    out[current_key] = "\n".join(current_lines).strip()
                current_key = head
                current_lines = [rest.strip()]
                continue
        current_lines.append(line)
    if current_key is not None:
        out[current_key] = "\n".join(current_lines).strip()
    return out


def _extract_record(filename: str, lines: list[str]) -> MetadataRecord:
    rec = MetadataRecord(filename=filename)
    sections = _parse_sections(lines)

    rec.software = sections.get("SOFTWARE", "").strip() or None
    rec.model = sections.get("MODEL", "").strip() or None
    if rec.model and "NovelAI" in (rec.software or ""):
        rec.nai_model = rec.model.replace("NovelAI Diffusion ", "").strip()

    # Try [FULL METADATA JSON] first: most authoritative for NAI
    raw_json_text = sections.get("FULL METADATA JSON", "")
    if raw_json_text:
        try:
            data = json.loads(raw_json_text)
            rec.raw_json = data

            # NAI V4 stores the resolved prompt (dynamic choices already made)
            # in actual_prompts.prompt.base_caption.  Use that as the base so
            # we get the tag that was *actually* used during generation, then
            # append every option from every || ... || block in the template so
            # the wildcard files cover the full range of choices in the batch.
            template_prompt: str = data.get("prompt", "") or ""
            actual_caption: str = (
                (data.get("actual_prompts") or {})
                .get("prompt", {})
                .get("base_caption", "")
                or ""
            ).strip()

            if actual_caption:
                pipe_options = extract_pipe_block_options(template_prompt)
                rec.prompt = (
                    actual_caption + (", " + ", ".join(pipe_options) if pipe_options else "")
                )
            else:
                # Older format or non-NAI: fall back to template; the cleaner
                # will expand || blocks into all options via _expand_pipe_blocks.
                rec.prompt = template_prompt

            rec.negative = data.get("uc") or data.get("negative_prompt")
            rec.width = data.get("width")
            rec.height = data.get("height")
            rec.steps = data.get("steps")
            rec.cfg_scale = data.get("scale")
            rec.seed = data.get("seed")
        except Exception:
            pass

    if not rec.prompt:
        rec.prompt = sections.get("DESCRIPTION/PROMPT", "") or sections.get(
            "PROMPT", ""
        )

    # Fill missing fields from GENERATION PARAMETERS
    gp_text = sections.get("GENERATION PARAMETERS", "")
    if gp_text:
        params = _parse_gen_params(gp_text)
        if not rec.prompt and "Prompt" in params:
            rec.prompt = params["Prompt"]
        if rec.negative is None and "Negative Prompt" in params:
            rec.negative = params["Negative Prompt"]
        if "Size" in params:
            m = _SIZE_RE.match("Size: " + params["Size"])
            if m:
                rec.width = rec.width or int(m.group(1))
                rec.height = rec.height or int(m.group(2))
        if rec.steps is None and "Steps" in params:
            try:
                rec.steps = int(params["Steps"])
            except ValueError:
                pass
        if rec.cfg_scale is None and "Scale" in params:
            try:
                rec.cfg_scale = float(params["Scale"])
            except ValueError:
                pass
        if rec.seed is None and "Seed" in params:
            try:
                rec.seed = int(params["Seed"])
            except ValueError:
                pass

    return rec


def iter_metadata_records(path: Path) -> Iterator[MetadataRecord]:
    """Stream :class:`MetadataRecord` objects from a metadata.txt file."""
    for filename, lines in _section_lines(path):
        try:
            yield _extract_record(filename, lines)
        except Exception as exc:  # pragma: no cover - parsing should never crash a job
            # Best-effort: yield a minimal record so callers can log + continue.
            yield MetadataRecord(filename=filename, prompt="", raw_json={"_error": str(exc)})


def count_records(path: Path) -> int:
    """Cheap count of records (== number of ``File:`` lines)."""
    n = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if _FILE_RE.match(line.strip()):
                n += 1
    return n


def preview_metadata_file(path: Path, sample_size: int = 20) -> dict:
    """Quick parse of the first ``sample_size`` records for the UI preview.

    Returns the cleaned canonical tags grouped per bucket so the UI can show
    what would actually land in each wildcard file after the full ingest.
    """
    samples = []
    bytes_total = path.stat().st_size
    for i, rec in enumerate(iter_metadata_records(path)):
        if i >= sample_size:
            break
        cleaned: CleanedPrompt = clean_prompt(rec.prompt)
        by_bucket: dict[str, list[str]] = {}
        for name in cleaned.tags:
            assign = classify_tag(name)
            by_bucket.setdefault(assign.bucket, []).append(name)

        scene = (
            by_bucket.get("outfit", [])
            + by_bucket.get("pose", [])
            + by_bucket.get("expression", [])
            + by_bucket.get("background", [])
        )
        if scene:
            by_bucket["scene"] = scene

        inferred_rating, evidence = infer_image_rating(cleaned.tags)

        samples.append(
            {
                "filename": rec.filename,
                "software": rec.software,
                "nai_model": rec.nai_model,
                "width": rec.width,
                "height": rec.height,
                "raw_prompt_excerpt": (rec.prompt or "")[:240],
                "canonical_tags": cleaned.tags[:40],
                "tag_count": len(cleaned.tags),
                "artist_tag_count": len(cleaned.artist_tags),
                "buckets": {
                    b: [t.replace("_", " ") for t in tags]
                    for b, tags in by_bucket.items()
                    if b not in {"artist", "quality_meta"}
                },
                "dropped_count": (
                    len(by_bucket.get("other", []))
                    + len(by_bucket.get("character", []))
                ),
                "inferred_rating": inferred_rating,
                "rating_evidence": [t.replace("_", " ") for t in evidence[:8]],
            }
        )
    return {
        "path": str(path),
        "size_bytes": bytes_total,
        "samples": samples,
    }

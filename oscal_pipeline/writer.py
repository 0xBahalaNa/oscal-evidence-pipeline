"""Stage 5 — write a validated SAR to a timestamped evidence file."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from oscal_pipeline.assembler import OscalAssessmentResults, serialize_sar

__all__ = ["write"]

_FILENAME_PREFIX = "assessment-results-"


def _atomic_write_bytes(final_path: Path, data: bytes) -> None:
    """Write ``data`` to ``final_path`` atomically (temp -> fsync -> rename)."""
    temp_path = final_path.parent / f".{final_path.name}.tmp"
    try:
        with temp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, final_path)
    except BaseException:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def _timestamp_from_sar(sar: OscalAssessmentResults) -> datetime:
    """Return the SAR run timestamp used for the evidence filename."""
    timestamp = sar.metadata.last_modified.__root__
    if not isinstance(timestamp, datetime):
        raise TypeError(
            "SAR metadata.last-modified must be a datetime for evidence naming"
        )
    return timestamp


def _build_output_path(output_dir: Path, timestamp: datetime) -> Path:
    """Build ``assessment-results-YYYY-MM-DD-HHMMSS.json`` under ``output_dir``."""
    stamp = timestamp.strftime("%Y-%m-%d-%H%M%S")
    return output_dir / f"{_FILENAME_PREFIX}{stamp}.json"


def write(sar: OscalAssessmentResults, output_dir: Path) -> Path:
    """Write ``sar`` to a timestamped JSON file under ``output_dir``.

    Creates ``output_dir`` when missing. Refuses to overwrite an existing
    file at the computed path (AU-9 — protection of audit information).
    Returns the path of the written evidence file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = _build_output_path(output_dir, _timestamp_from_sar(sar))
    if final_path.exists():
        raise FileExistsError(
            f"refusing to overwrite existing SAR evidence file: {final_path}"
        )
    _atomic_write_bytes(final_path, serialize_sar(sar))
    return final_path

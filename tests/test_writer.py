"""Tests for Stage 5 SAR output writer (issue #6)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import oscal_pipeline
from oscal_pipeline.adapters.secret_scanner import SecretScannerAdapter
from oscal_pipeline.assembler import (
    OscalAssessmentResults,
    RunMetadata,
    assemble,
    serialize_sar,
)
from oscal_pipeline.writer import write

_FIXTURE = Path(__file__).parent / "fixtures" / "secret_scanner_mixed.json"


@pytest.fixture
def run_metadata() -> RunMetadata:
    return RunMetadata(
        run_timestamp=datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc),
        source_tool="secret-scanner",
        operator_name="oscal-evidence-pipeline operator",
        pipeline_version=oscal_pipeline.__version__,
    )


@pytest.fixture
def assembled_sar(run_metadata: RunMetadata) -> OscalAssessmentResults:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = SecretScannerAdapter().transform(raw)
    return assemble(result.observations, result.findings, run_metadata)


def test_write_succeeds(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    """Written file exists, matches timestamp naming, and parses as OSCAL SAR."""
    output_path = write(assembled_sar, tmp_path)
    assert output_path.exists()
    assert output_path.name == "assessment-results-2026-06-05-120000.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "assessment-results" in payload
    assert payload["assessment-results"]["uuid"]


def test_write_round_trips_through_trestle(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    """Emitted bytes must parse through the same Trestle model ``assemble()`` uses."""
    from trestle.oscal.assessment_results import Model as TrestleSARModel

    output_path = write(assembled_sar, tmp_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    wrapper = TrestleSARModel.parse_obj(payload)
    assert wrapper.assessment_results is not None
    assert wrapper.assessment_results.uuid


def test_written_bytes_match_serialize_sar(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    """Disk bytes must be byte-identical to the assembler's canonical serializer."""
    output_path = write(assembled_sar, tmp_path)
    assert output_path.read_bytes() == serialize_sar(assembled_sar)


def test_write_creates_output_dir(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    """``write`` must create nested ``output_dir`` when it does not exist."""
    nested = tmp_path / "evidence" / "runs"
    assert not nested.exists()
    output_path = write(assembled_sar, nested)
    assert nested.is_dir()
    assert output_path.parent == nested


def test_write_refuses_overwrite(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    """AU-9 — a second write to the same timestamp path must raise."""
    first = write(assembled_sar, tmp_path)
    assert first.exists()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write(assembled_sar, tmp_path)


def test_atomic_write_cleans_up_on_failure(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    """Failed rename must not leave a partial final file or stale temp file."""
    final_path = tmp_path / "assessment-results-2026-06-05-120000.json"
    temp_path = tmp_path / f".{final_path.name}.tmp"

    with patch("oscal_pipeline.writer.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            write(assembled_sar, tmp_path)

    assert not final_path.exists()
    assert not temp_path.exists()


def test_serialize_preserves_oscal_field_order(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    """Inner SAR keys must follow pydantic model order, not alphabetical sort."""
    output_path = write(assembled_sar, tmp_path)
    text = output_path.read_text(encoding="utf-8")

    # SAR root fields per oscal-pydantic: uuid before metadata before import-ap.
    uuid_index = text.index('"uuid"')
    metadata_index = text.index('"metadata"')
    assert uuid_index < metadata_index


def test_write_returns_resolved_path(
    tmp_path: Path,
    assembled_sar: OscalAssessmentResults,
) -> None:
    output_path = write(assembled_sar, tmp_path)
    assert output_path == tmp_path / "assessment-results-2026-06-05-120000.json"

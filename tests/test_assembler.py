"""Tests for Stage 4 SAR assembly (issue #5)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import oscal_pipeline
from oscal_pipeline.adapters.secret_scanner import SecretScannerAdapter
from oscal_pipeline.assembler import (
    OSCAL_VERSION,
    RunMetadata,
    SarValidationError,
    assemble,
)
from oscal_pipeline.adapters.uuid import deterministic_uuid

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
def transformed_fixture() -> tuple[tuple, tuple]:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = SecretScannerAdapter().transform(raw)
    return result.observations, result.findings


def test_assemble_secret_scanner_fixture_passes_trestle(
    transformed_fixture: tuple[tuple, tuple],
    run_metadata: RunMetadata,
) -> None:
    observations, findings = transformed_fixture
    sar = assemble(observations, findings, run_metadata)
    assert sar.uuid
    assert sar.results[0].observations is not None
    assert len(sar.results[0].observations) == 4
    assert sar.results[0].findings is not None
    assert len(sar.results[0].findings) == 3


def test_assemble_metadata_fields(
    transformed_fixture: tuple[tuple, tuple],
    run_metadata: RunMetadata,
) -> None:
    observations, findings = transformed_fixture
    sar = assemble(observations, findings, run_metadata)
    metadata = sar.metadata
    assert metadata.title == "Evidence Pipeline Run 2026-06-05"
    assert metadata.last_modified.__root__ == run_metadata.run_timestamp
    assert metadata.version.__root__ == oscal_pipeline.__version__
    assert metadata.oscal_version.__root__ == OSCAL_VERSION
    assert metadata.parties is not None
    assert metadata.parties[0].name == run_metadata.operator_name


def test_assemble_one_result_entry(
    transformed_fixture: tuple[tuple, tuple],
    run_metadata: RunMetadata,
) -> None:
    observations, findings = transformed_fixture
    sar = assemble(observations, findings, run_metadata)
    assert len(sar.results) == 1
    result = sar.results[0]
    assert result.observations is not None
    assert result.findings is not None
    assert len(result.observations) == len(observations)
    assert len(result.findings) == len(findings)


def test_assemble_uuid_determinism(
    transformed_fixture: tuple[tuple, tuple],
    run_metadata: RunMetadata,
) -> None:
    observations, findings = transformed_fixture
    first = assemble(observations, findings, run_metadata)
    second = assemble(observations, findings, run_metadata)
    assert first.uuid == second.uuid
    assert first.results[0].uuid == second.results[0].uuid
    # SAR uuid identity tuple is (run_timestamp, source_tool) so two
    # parallel adapter runs at the same instant do not collapse onto a
    # single SAR UUID — see the CM-3 namespacing comment in assembler.py.
    expected_sar_uuid = deterministic_uuid(
        "sar",
        run_metadata.run_timestamp.isoformat(),
        run_metadata.source_tool,
    )
    expected_result_uuid = deterministic_uuid(
        "result",
        run_metadata.source_tool,
        run_metadata.run_timestamp.isoformat(),
    )
    assert first.uuid == expected_sar_uuid
    assert first.results[0].uuid == expected_result_uuid


def test_assemble_sar_uuid_scopes_by_source_tool(
    transformed_fixture: tuple[tuple, tuple],
) -> None:
    """Different source_tool values at the same timestamp must produce distinct SAR UUIDs."""
    observations, findings = transformed_fixture
    ts = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    meta_a = RunMetadata(
        run_timestamp=ts,
        source_tool="secret-scanner",
        operator_name="op",
        pipeline_version="0.1.0",
    )
    meta_b = RunMetadata(
        run_timestamp=ts,
        source_tool="s3-audit",
        operator_name="op",
        pipeline_version="0.1.0",
    )
    sar_a = assemble(observations, findings, meta_a)
    sar_b = assemble(observations, findings, meta_b)
    assert sar_a.uuid != sar_b.uuid


def test_assemble_handles_all_pass_run(run_metadata: RunMetadata) -> None:
    """All-PASS runs (observations only, zero findings) must produce a valid SAR.

    Continuous-monitoring evidence (CA-7) frequently produces "no
    findings this cycle" runs; that case must round-trip cleanly rather
    than crashing pydantic's ``min_items=1`` constraint on
    ``AssessmentResult.findings``.
    """
    raw = {
        "scan_metadata": {"timestamp": "2026-06-05T12:00:00+00:00"},
        "findings": [
            {
                "file_path": "clean-scan.log",
                "line_number": 1,
                "finding_type": "No secrets in scope",
                "pattern_matched": "N/A",
                "severity": "INFO",  # classifies as PASS — no Finding emitted
                "control_ids": ["SC-28"],
            }
        ],
        "summary": {},
    }
    transformed = SecretScannerAdapter().transform(raw)
    assert len(transformed.observations) == 1
    assert len(transformed.findings) == 0

    sar = assemble(transformed.observations, transformed.findings, run_metadata)
    assert sar.results[0].observations is not None
    assert len(sar.results[0].observations) == 1
    # The findings kwarg is omitted on all-PASS runs so pydantic
    # ``min_items=1`` is not triggered; the attribute should be ``None``,
    # not an empty list.
    assert sar.results[0].findings is None


def test_assemble_merges_multiple_adapter_outputs(
    run_metadata: RunMetadata,
) -> None:
    """Assembler must accept heterogeneous observations from multiple adapters."""
    base = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    altered_findings = [
        {**finding, "file_path": f"alt-{finding['file_path']}"}
        for finding in base["findings"]
    ]
    raw_alt = {**base, "findings": altered_findings}

    result_a = SecretScannerAdapter().transform(base)
    result_b = SecretScannerAdapter().transform(raw_alt)

    merged_observations = result_a.observations + result_b.observations
    merged_findings = result_a.findings + result_b.findings

    sar = assemble(merged_observations, merged_findings, run_metadata)
    assert sar.results[0].observations is not None
    assert len(sar.results[0].observations) == 8  # 4 + 4
    assert sar.results[0].findings is not None
    assert len(sar.results[0].findings) == 6  # 3 + 3 (INFO → no finding)


def test_assemble_invalid_timestamp_raises(
    transformed_fixture: tuple[tuple, tuple],
) -> None:
    observations, findings = transformed_fixture
    metadata = RunMetadata(
        run_timestamp=datetime(2026, 6, 5, 12, 0, 0),
        source_tool="secret-scanner",
        operator_name="operator",
        pipeline_version="0.1.0",
    )
    with pytest.raises(ValueError, match="TZ-aware"):
        assemble(observations, findings, metadata)


def test_validate_raises_on_broken_sar(
    transformed_fixture: tuple[tuple, tuple],
    run_metadata: RunMetadata,
) -> None:
    """Mocked-validator path — exercises the ``SarValidationError`` plumbing."""
    observations, findings = transformed_fixture

    def _fail_validation(_sar: object) -> None:
        raise SarValidationError("forced validation failure")

    with patch(
        "oscal_pipeline.assembler._validate_via_trestle_models",
        side_effect=_fail_validation,
    ):
        with pytest.raises(SarValidationError, match="forced validation failure"):
            assemble(observations, findings, run_metadata)


def test_validate_rejects_duplicate_observation_uuids(
    run_metadata: RunMetadata,
) -> None:
    """Real Trestle path — DuplicatesValidator must catch UUID collisions.

    Complements the mocked test above by exercising the actual Trestle
    AllValidator chain rather than mocking ``_validate_via_trestle_models``.
    A regression that breaks structural validation (e.g., dropping the
    include-all/include-controls normalization) would slip past the
    mocked test but trip this one.
    """
    from oscal_pydantic.assessment_results import IdentifiesTheSubject, Observation

    ts = run_metadata.run_timestamp
    dup_uuid = "11111111-1111-4111-8111-111111111111"

    def _obs(title: str, subject_uuid: str) -> Observation:
        return Observation(
            uuid=dup_uuid,  # intentional collision
            description=title,
            methods=["EXAMINE"],
            collected=ts,
            subjects=[
                IdentifiesTheSubject(
                    subject_uuid=subject_uuid,
                    type="software",
                    title=title,
                )
            ],
        )

    obs_a = _obs("first", "22222222-2222-4222-8222-222222222222")
    obs_b = _obs("second", "33333333-3333-4333-8333-333333333333")

    with pytest.raises(SarValidationError, match="duplicate"):
        assemble([obs_a, obs_b], [], run_metadata)

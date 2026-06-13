"""Regression tests for committed examples/ artifacts (issue #8)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from oscal_pipeline.adapters.secret_scanner import SecretScannerAdapter
from oscal_pipeline.adapters.uuid import deterministic_uuid
from oscal_pipeline.assembler import OSCAL_VERSION, RunMetadata, assemble, serialize_sar

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# EXACTLY the RunMetadata the committed example was generated with.
PINNED = RunMetadata(
    run_timestamp=datetime(2026, 5, 15, 9, 31, 0, tzinfo=timezone.utc),
    source_tool="secret-scanner",
    operator_name="ci-evidence-bot",
    pipeline_version="1.0.0",
    assessment_plan_href="#fedramp-high-evidence-plan",
)


def _drop_install_dependent(doc: dict[str, object]) -> dict[str, object]:
    # oscal-version tracks the compliance-trestle pin — assert it separately.
    assessment_results = doc["assessment-results"]
    assert isinstance(assessment_results, dict)
    metadata = assessment_results.get("metadata")
    assert isinstance(metadata, dict)
    metadata.pop("oscal-version", None)
    return doc


def test_sample_input_reproduces_committed_sar() -> None:
    raw = json.loads((EXAMPLES / "sample-secret-scanner-input.json").read_text(encoding="utf-8"))
    result = SecretScannerAdapter().transform(raw)
    sar = assemble(result.observations, result.findings, PINNED)

    produced = json.loads(serialize_sar(sar))
    committed = json.loads((EXAMPLES / "sample-assessment-results.json").read_text(encoding="utf-8"))

    assert _drop_install_dependent(produced) == _drop_install_dependent(committed)


def test_committed_sar_oscal_version_matches_trestle() -> None:
    committed = json.loads((EXAMPLES / "sample-assessment-results.json").read_text(encoding="utf-8"))
    assert committed["assessment-results"]["metadata"]["oscal-version"] == OSCAL_VERSION


def test_observation_uuids_are_deterministic() -> None:
    expected = deterministic_uuid("observation", "src/config/deploy.tf", "12", "AKIA[0-9A-Z]{16}")
    committed = json.loads((EXAMPLES / "sample-assessment-results.json").read_text(encoding="utf-8"))
    obs = committed["assessment-results"]["results"][0]["observations"]
    assert expected in {o["uuid"] for o in obs}

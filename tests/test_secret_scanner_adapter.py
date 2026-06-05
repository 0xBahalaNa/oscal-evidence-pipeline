"""Tests for the secret-scanner adapter (issue #3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oscal_pydantic.assessment_results import ObjectiveStatusState

from oscal_pipeline.adapters import find_adapter
from oscal_pipeline.adapters.registry import register_adapter
from oscal_pipeline.adapters.secret_scanner import SecretScannerAdapter
from oscal_pipeline.adapters.uuid import deterministic_uuid

_FIXTURE = Path(__file__).parent / "fixtures" / "secret_scanner_mixed.json"


@pytest.fixture
def raw_fixture() -> dict[str, object]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data


@pytest.fixture
def adapter() -> SecretScannerAdapter:
    register_adapter("secret-scanner")(SecretScannerAdapter)
    return SecretScannerAdapter()


def test_matches_secret_scanner_fixture(raw_fixture: dict[str, object]) -> None:
    adapter = SecretScannerAdapter()
    assert adapter.matches(raw_fixture) is True


def test_find_adapter_resolves_secret_scanner(raw_fixture: dict[str, object]) -> None:
    register_adapter("secret-scanner")(SecretScannerAdapter)
    resolved = find_adapter(raw_fixture)
    assert isinstance(resolved, SecretScannerAdapter)


def test_transform_produces_four_observations_and_three_findings(
    adapter: SecretScannerAdapter, raw_fixture: dict[str, object]
) -> None:
    result = adapter.transform(raw_fixture)
    assert len(result.observations) == 4
    assert len(result.findings) == 3


def test_transform_uuid_is_deterministic(
    adapter: SecretScannerAdapter, raw_fixture: dict[str, object]
) -> None:
    first = adapter.transform(raw_fixture)
    second = adapter.transform(raw_fixture)
    assert [o.uuid for o in first.observations] == [o.uuid for o in second.observations]


def test_observation_uuid_derivation(adapter: SecretScannerAdapter) -> None:
    identity = "bad-config.json|4|AKIA[0-9A-Z]{16}"
    expected = deterministic_uuid(identity)
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = adapter.transform(raw)
    first_obs = result.observations[0]
    assert first_obs.uuid == expected
    assert first_obs.description == "AWS Access Key ID"
    prop_names = {p.name for p in first_obs.props or []}
    assert prop_names == {"severity", "file_path", "line_number", "pattern_matched"}
    assert first_obs.subjects is not None
    assert first_obs.subjects[0].title == "bad-config.json"


def test_finding_links_observation_and_carries_control_ids(
    adapter: SecretScannerAdapter, raw_fixture: dict[str, object]
) -> None:
    result = adapter.transform(raw_fixture)
    fail_finding = result.findings[0]
    fail_obs = result.observations[0]
    assert fail_finding.related_observations is not None
    assert fail_finding.related_observations[0].observation_uuid == fail_obs.uuid
    assert fail_finding.target.status.state == ObjectiveStatusState.not_satisfied
    control_values = {p.value for p in fail_finding.props or [] if p.name == "control-id"}
    assert control_values == {"IA-5(7)", "SC-12", "SC-28"}


def test_pass_severity_produces_observation_without_finding(
    adapter: SecretScannerAdapter, raw_fixture: dict[str, object]
) -> None:
    result = adapter.transform(raw_fixture)
    pass_obs = result.observations[3]
    assert pass_obs.description == "No secrets in scope"
    pass_findings = [
        f
        for f in result.findings
        if f.related_observations
        and f.related_observations[0].observation_uuid == pass_obs.uuid
    ]
    assert pass_findings == []


def test_matches_rejects_extra_top_level_keys() -> None:
    adapter = SecretScannerAdapter()
    raw = {"scan_metadata": {}, "findings": [], "summary": {}, "extra": 1}
    assert adapter.matches(raw) is False

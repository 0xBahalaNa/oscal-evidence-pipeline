"""Tests for the secret-scanner adapter (issue #3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oscal_pydantic.assessment_results import ObjectiveStatusState

from oscal_pipeline.adapters import find_adapter
from oscal_pipeline.adapters.registry import register_adapter
from oscal_pipeline.adapters.secret_scanner import (
    SecretScannerAdapter,
    UnknownSeverityError,
)
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
    # The observation identity is namespace-prefixed with "observation" to
    # keep its UUID space disjoint from subject / finding / target spaces;
    # without the prefix, a file literally named "subject" / "finding" /
    # "target" would collide with that sibling's UUID under the same
    # joined-with-"|" name. Match the adapter's exact derivation here.
    expected = deterministic_uuid(
        "observation", "bad-config.json", "4", "AKIA[0-9A-Z]{16}"
    )
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = adapter.transform(raw)
    first_obs = result.observations[0]
    assert first_obs.uuid == expected
    assert first_obs.description == "AWS Access Key ID"
    # Assert prop VALUES (not just the set of names) — catches the silent
    # regression where a refactor accidentally swaps the field bound to a
    # prop name (e.g. ``_prop("severity", finding_type)`` would still pass
    # a name-set assertion but ship corrupted evidence).
    prop_values = {p.name: p.value for p in first_obs.props or []}
    assert prop_values == {
        "source-tool": "secret-scanner",
        "severity": "CRITICAL",
        "file_path": "bad-config.json",
        "line_number": "4",
        "pattern_matched": "AKIA[0-9A-Z]{16}",
    }
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


# --- Malformed-input loud-failure coverage (B1.1, B1.4, B1.5, B1.6) ---------
#
# Each test exercises one ``raise`` branch the adapter documents. Builds
# inline dict fixtures rather than file-based ones so the assertion sits
# next to the input that triggers it.


def _well_formed_finding() -> dict[str, object]:
    """Return a known-good finding to base malformed-variant tests on."""
    return {
        "file_path": "bad.json",
        "line_number": 1,
        "finding_type": "AWS Access Key ID",
        "pattern_matched": "AKIA[0-9A-Z]{16}",
        "severity": "HIGH",
        "control_ids": ["IA-5"],
    }


def _envelope(finding: dict[str, object]) -> dict[str, object]:
    return {
        "scan_metadata": {"timestamp": "2026-06-04T12:00:00+00:00"},
        "findings": [finding],
        "summary": {},
    }


def test_transform_raises_on_non_string_severity(
    adapter: SecretScannerAdapter,
) -> None:
    finding = _well_formed_finding()
    finding["severity"] = None  # non-string
    with pytest.raises(UnknownSeverityError, match="must be a string"):
        adapter.transform(_envelope(finding))


def test_transform_raises_on_unknown_severity_string(
    adapter: SecretScannerAdapter,
) -> None:
    finding = _well_formed_finding()
    finding["severity"] = "BANANA"  # outside the documented vocabulary
    with pytest.raises(UnknownSeverityError, match="severity unknown"):
        adapter.transform(_envelope(finding))


def test_transform_rejects_bool_line_number(
    adapter: SecretScannerAdapter,
) -> None:
    """``bool`` is a subclass of ``int`` in Python; the int-required guard must reject it."""
    finding = _well_formed_finding()
    finding["line_number"] = True  # bool, not int
    with pytest.raises(ValueError, match="line_number must be an integer"):
        adapter.transform(_envelope(finding))


def test_transform_rejects_tz_naive_timestamp(
    adapter: SecretScannerAdapter,
) -> None:
    """OSCAL ``collected`` requires a TZ-aware ISO 8601 timestamp."""
    raw: dict[str, object] = {
        "scan_metadata": {"timestamp": "2026-06-04T12:00:00"},  # no TZ
        "findings": [],
        "summary": {},
    }
    with pytest.raises(ValueError, match="TZ-aware"):
        adapter.transform(raw)


def test_observation_carries_source_tool_prop(
    adapter: SecretScannerAdapter, raw_fixture: dict[str, object]
) -> None:
    """AU-3 requires every observation to identify its source tool."""
    result = adapter.transform(raw_fixture)
    for obs in result.observations:
        source_tool_props = [
            p for p in obs.props or [] if p.name == "source-tool"
        ]
        assert len(source_tool_props) == 1
        assert source_tool_props[0].value == "secret-scanner"

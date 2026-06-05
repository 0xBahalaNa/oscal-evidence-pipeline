"""Adapter for ``secret-scanner --output json`` (issue #3).

Ingests the three-section JSON document (``scan_metadata``, ``findings``,
``summary``) and emits one OSCAL ``Observation`` per finding, with paired
``Finding`` objects for FAIL/WARN severities.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import cast

from oscal_pydantic.assessment_results import (
    Finding,
    FindingTargetType,
    IdentifiesTheSubject,
    ObjectiveStatus,
    ObjectiveStatus1,
    ObjectiveStatusState,
    Observation,
    Property,
    RelatedObservation,
)

from oscal_pipeline.adapters.registry import register_adapter
from oscal_pipeline.adapters.result import TransformResult
from oscal_pipeline.adapters.uuid import deterministic_uuid

_FINGERPRINT_KEYS = frozenset({"scan_metadata", "findings", "summary"})

_FAIL_SEVERITIES = frozenset({"CRITICAL", "HIGH"})
_WARN_SEVERITIES = frozenset({"MEDIUM", "LOW"})
_PASS_SEVERITIES = frozenset({"INFO"})


class _Outcome(Enum):
    FAIL = "fail"
    WARN = "warn"
    PASS = "pass"


def _classify_severity(severity: object) -> _Outcome:
    if not isinstance(severity, str):
        return _Outcome.FAIL
    normalized = severity.upper()
    if normalized in _PASS_SEVERITIES:
        return _Outcome.PASS
    if normalized in _WARN_SEVERITIES:
        return _Outcome.WARN
    return _Outcome.FAIL


def _parse_timestamp(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise ValueError(f"scan_metadata.timestamp must be a string; got {type(raw).__name__}")
    return datetime.fromisoformat(raw)


def _prop(name: str, value: str) -> Property:
    return Property(name=name, value=value)


def _control_props(control_ids: list[str]) -> list[Property]:
    return [_prop("control-id", cid) for cid in control_ids]


def _parse_finding(raw_finding: object, index: int) -> dict[str, object]:
    if not isinstance(raw_finding, dict):
        raise ValueError(
            f"findings[{index}] must be an object; got {type(raw_finding).__name__}"
        )
    return cast(dict[str, object], raw_finding)


def _require_str(data: dict[str, object], key: str, index: int) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"findings[{index}].{key} must be a string; got {type(value).__name__}")
    return value


def _require_int(data: dict[str, object], key: str, index: int) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ValueError(f"findings[{index}].{key} must be an integer; got {type(value).__name__}")
    return value


def _require_control_ids(data: dict[str, object], index: int) -> list[str]:
    value = data.get("control_ids")
    if not isinstance(value, list):
        raise ValueError(
            f"findings[{index}].control_ids must be a list; got {type(value).__name__}"
        )
    ids: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(
                f"findings[{index}].control_ids[{i}] must be a string; "
                f"got {type(item).__name__}"
            )
        ids.append(item)
    return ids


@register_adapter("secret-scanner")
class SecretScannerAdapter:
    """Transform secret-scanner JSON into OSCAL observations and findings."""

    def matches(self, raw: dict[str, object]) -> bool:
        return frozenset(raw.keys()) == _FINGERPRINT_KEYS

    def transform(self, raw: dict[str, object]) -> TransformResult:
        metadata = raw.get("scan_metadata")
        if not isinstance(metadata, dict):
            raise ValueError("scan_metadata must be an object")
        metadata_dict = cast(dict[str, object], metadata)
        collected = _parse_timestamp(metadata_dict.get("timestamp"))

        findings_raw = raw.get("findings")
        if not isinstance(findings_raw, list):
            raise ValueError("findings must be a list")

        observations: list[Observation] = []
        findings: list[Finding] = []

        for index, item in enumerate(findings_raw):
            row = _parse_finding(item, index)
            file_path = _require_str(row, "file_path", index)
            line_number = _require_int(row, "line_number", index)
            finding_type = _require_str(row, "finding_type", index)
            pattern_matched = _require_str(row, "pattern_matched", index)
            severity = row.get("severity")
            control_ids = _require_control_ids(row, index)

            identity = f"{file_path}|{line_number}|{pattern_matched}"
            obs_uuid = deterministic_uuid(identity)
            subject_uuid = deterministic_uuid("subject", file_path)

            severity_str = severity if isinstance(severity, str) else ""
            obs_props = [
                _prop("severity", severity_str),
                _prop("file_path", file_path),
                _prop("line_number", str(line_number)),
                _prop("pattern_matched", pattern_matched),
            ]

            observations.append(
                Observation(
                    uuid=obs_uuid,
                    description=finding_type,
                    methods=["EXAMINE"],
                    collected=collected,
                    props=obs_props,
                    subjects=[
                        IdentifiesTheSubject(
                            subject_uuid=subject_uuid,
                            type="software",
                            title=file_path,
                        )
                    ],
                )
            )

            outcome = _classify_severity(severity)
            if outcome is _Outcome.PASS:
                continue

            primary_control = control_ids[0] if control_ids else finding_type
            target_id = deterministic_uuid("target", primary_control)
            find_uuid = deterministic_uuid("finding", identity)

            findings.append(
                Finding(
                    uuid=find_uuid,
                    title=finding_type,
                    description=finding_type,
                    target=ObjectiveStatus(
                        type=FindingTargetType.objective_id,
                        target_id=target_id,
                        status=ObjectiveStatus1(
                            state=ObjectiveStatusState.not_satisfied,
                        ),
                    ),
                    related_observations=[
                        RelatedObservation(observation_uuid=obs_uuid),
                    ],
                    props=_control_props(control_ids) if control_ids else None,
                )
            )

        return TransformResult(
            observations=tuple(observations),
            findings=tuple(findings),
        )

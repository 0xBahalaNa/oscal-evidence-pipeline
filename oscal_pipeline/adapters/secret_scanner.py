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
from oscal_pipeline.oscal.slug import objective_id_slug

# Source-tool name used in two places: as the registry key (via
# @register_adapter) and as the value of the "source-tool" property
# emitted on every Observation. Defined once here so the two stay in
# lockstep — AU-3 ("content of audit records") requires every
# observation to identify its source tool, and a divergence between the
# registry key and the property value would silently mis-attribute
# evidence in a merged multi-adapter SAR.
_ADAPTER_NAME = "secret-scanner"

_FINGERPRINT_KEYS = frozenset({"scan_metadata", "findings", "summary"})

_FAIL_SEVERITIES = frozenset({"CRITICAL", "HIGH"})
_WARN_SEVERITIES = frozenset({"MEDIUM", "LOW"})
_PASS_SEVERITIES = frozenset({"INFO"})


class _Outcome(Enum):
    FAIL = "fail"
    WARN = "warn"
    PASS = "pass"


class MissingControlIdError(ValueError):
    """Raised when a FAIL/WARN finding has no ``control_ids`` to map.

    Per the loud-failure-on-ambiguity policy, a non-PASS finding without
    control traceability must halt at ingest rather than emit a
    ``not-satisfied`` Finding with a synthetic target and no control props.
    """


class UnknownSeverityError(ValueError):
    """Raised when ``secret-scanner`` emits a severity outside the known vocabulary.

    Per the repo's loud-failure-on-ambiguity policy (see the module
    docstring of ``adapters/registry.py``), an evidence pipeline must
    fail at ingest rather than silently emit a default-categorized
    Finding. An unknown or wrong-typed severity from upstream is an
    evidence-contract regression the operator must fix at the source;
    silently mapping it to FAIL would emit a ``not-satisfied`` Finding
    against the wrong control mappings and ship that into the SAR —
    exactly the silent-incomplete-evidence failure mode CJIS AU-6
    weekly review and FedRAMP 20x continuous monitoring catch *late*.
    """


def _require_severity(data: dict[str, object], index: int) -> tuple[str, _Outcome]:
    """Validate the ``severity`` field and return ``(severity_str, outcome)``.

    Raises :class:`UnknownSeverityError` on non-string severity or on a
    string value outside the documented vocabulary
    (``INFO`` / ``LOW`` / ``MEDIUM`` / ``HIGH`` / ``CRITICAL``). Returning
    the validated string alongside the classified outcome means the
    caller can populate the ``severity`` property without a second
    isinstance narrow — the helper is the single source of truth for
    "this severity is known and well-formed."
    """
    raw = data.get("severity")
    if not isinstance(raw, str):
        raise UnknownSeverityError(
            f"findings[{index}].severity must be a string; "
            f"got {type(raw).__name__}"
        )
    normalized = raw.upper()
    if normalized in _PASS_SEVERITIES:
        return raw, _Outcome.PASS
    if normalized in _WARN_SEVERITIES:
        return raw, _Outcome.WARN
    if normalized in _FAIL_SEVERITIES:
        return raw, _Outcome.FAIL
    known = sorted(_PASS_SEVERITIES | _WARN_SEVERITIES | _FAIL_SEVERITIES)
    raise UnknownSeverityError(
        f"findings[{index}].severity unknown: {raw!r}; "
        f"expected one of {known}"
    )


def _parse_timestamp(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise ValueError(
            f"scan_metadata.timestamp must be a string; got {type(raw).__name__}"
        )
    dt = datetime.fromisoformat(raw)
    # OSCAL's ``collected`` field requires a TZ-aware ISO 8601 timestamp
    # per the NIST OSCAL JSON Schema. ``oscal-pydantic==2023.3.21`` does
    # not enforce this at construction time (its dateTime regex is
    # commented out), so a naive timestamp passes local tests and only
    # fails at the schema-validation gate — late discovery. Raise here
    # so the failure surfaces at ingest, where the operator can fix the
    # source-tool output. See CLAUDE.md: "Every emitted SAR must
    # validate against the NIST OSCAL schema before commit."
    if dt.tzinfo is None:
        raise ValueError(
            f"scan_metadata.timestamp must be TZ-aware (ISO 8601 with "
            f"offset, e.g. '...T12:00:00+00:00'); got naive {raw!r}"
        )
    return dt


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
    # ``bool`` is a subclass of ``int`` in Python, so a plain
    # ``isinstance(value, int)`` would silently accept ``True`` / ``False``
    # and let boolean garbage flow into props as the literal string
    # ``"True"`` / ``"False"``. Exclude ``bool`` explicitly so the
    # type-mismatch surfaces here at ingest, not later in the SAR.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(
            f"findings[{index}].{key} must be an integer; got {type(value).__name__}"
        )
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


@register_adapter(_ADAPTER_NAME)
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
            severity, outcome = _require_severity(row, index)
            control_ids = _require_control_ids(row, index)

            # Identity tuple that uniquely names this source-tool finding.
            # Same tuple seeds three derived UUIDs: the observation
            # (prefixed "observation"), the finding (prefixed "finding"),
            # and the assessment target (derived from the control id
            # below). Each derivation prepends a disjoint namespace token
            # so the four UUID spaces (observation / subject / finding /
            # target) cannot collide with each other when a file path
            # happens to match a sibling-namespace string. Verified
            # collision case before this fix: file_path="subject" +
            # line_number=4 + pattern_matched="AKIA" produced an obs
            # UUID identical to ``deterministic_uuid("subject", "4|AKIA")``.
            finding_identity = (file_path, str(line_number), pattern_matched)
            obs_uuid = deterministic_uuid("observation", *finding_identity)
            subject_uuid = deterministic_uuid("subject", file_path)

            obs_props = [
                # ``source-tool`` honors the AU-3 claim that every
                # observation identifies its source tool. The literal
                # comes from ``_ADAPTER_NAME`` so it cannot drift from
                # the registry key.
                _prop("source-tool", _ADAPTER_NAME),
                _prop("severity", severity),
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

            if outcome is _Outcome.PASS:
                continue

            if not control_ids:
                raise MissingControlIdError(
                    f"findings[{index}] has severity {severity!r} but empty "
                    f"control_ids; FAIL/WARN findings require control mapping"
                )

            primary_control = control_ids[0]
            # ``objective-id`` targets must resolve against a catalog objective
            # slug (e.g. ``ia-5.7_obj``), not a synthetic UUID — see issue #17.
            target_id = objective_id_slug(primary_control)
            find_uuid = deterministic_uuid("finding", *finding_identity)

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
                    props=_control_props(control_ids),
                )
            )

        return TransformResult(
            observations=tuple(observations),
            findings=tuple(findings),
        )

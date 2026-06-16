"""Stage 4 — assemble observations and findings into a validated OSCAL SAR."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Sequence

from oscal_pydantic.assessment_results import (
    AssessedControls,
    AssessmentResult,
    ImportAssessmentPlan,
    PartyOrganizationOrPerson,
    PublicationMetadata,
    ReviewedControlsAndControlObjectives,
    SecurityAssessmentResultsSAR,
    SelectControl,
)

from trestle.oscal import OSCAL_VERSION

from oscal_pipeline.adapters.uuid import deterministic_uuid
from oscal_pipeline.oscal.slug import control_id_slug

if TYPE_CHECKING:
    from oscal_pydantic.assessment_results import Finding, Observation

# Re-export Trestle's bundled OSCAL version. Hardcoding this number drifts
# the moment compliance-trestle bumps its OSCAL pin. The emitted SAR's
# ``oscal-version`` MUST match the schema Trestle uses to validate it, or
# the planned NIST OSCAL JSON Schema CI gate (per the repo CLAUDE.md
# mandate) will reject a version-mismatch on every run.
__all__ = [
    "OSCAL_VERSION",
    "OscalAssessmentResults",
    "RunMetadata",
    "SarValidationError",
    "assemble",
    "serialize_sar",
]

# Issue #5 names the root model ``OscalAssessmentResults``; oscal-pydantic
# generates ``SecurityAssessmentResultsSAR`` for the same schema object.
OscalAssessmentResults = SecurityAssessmentResultsSAR


class SarValidationError(ValueError):
    """Raised when Trestle rejects the assembled SAR after construction."""


@dataclass(frozen=True)
class RunMetadata:
    """Per-run context supplied by the pipeline orchestrator (issue #7)."""

    run_timestamp: datetime
    source_tool: str
    operator_name: str
    pipeline_version: str
    assessment_plan_href: str = "#local-evidence-plan"
    result_title: str | None = None


def _require_tz_aware(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        raise ValueError(
            "run_metadata.run_timestamp must be TZ-aware (ISO 8601 with offset)"
        )
    return timestamp


def _collect_control_ids(findings: Sequence[Finding]) -> list[str]:
    """Return sorted unique source-tool control IDs from finding props."""
    ids: set[str] = set()
    for finding in findings:
        for prop in finding.props or []:
            if prop.name == "control-id":
                ids.add(prop.value)
    return sorted(ids)


def _build_reviewed_controls(
    findings: Sequence[Finding],
) -> ReviewedControlsAndControlObjectives:
    control_ids = _collect_control_ids(findings)
    if not control_ids:
        # Schema requires at least one control-selection entry; when a run
        # produces observations only (all PASS), declare include-all so the
        # result remains schema-valid without inventing control mappings.
        return ReviewedControlsAndControlObjectives(
            control_selections=[
                AssessedControls(include_all={}),
            ]
        )
    return ReviewedControlsAndControlObjectives(
        control_selections=[
            AssessedControls(
                include_controls=[
                    SelectControl(control_id=control_id_slug(control_id))
                    for control_id in control_ids
                ]
            )
        ]
    )


def _strip_nulls(value: object) -> object:
    """Recursively remove JSON nulls so Trestle's strict models do not reject them."""
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _strip_nulls(item)) is not None
        }
    if isinstance(value, list):
        return [_strip_nulls(item) for item in value]
    return value


def _normalize_reviewed_controls_for_trestle(payload: dict[str, object]) -> None:
    """Patch reviewed-controls JSON so Trestle's union parser accepts it.

    oscal-pydantic models ``AssessedControls`` as a single object with optional
    ``include-all`` / ``include-controls``; Trestle expects a discriminated
    union (``ControlSelectionsAll`` vs ``ControlSelections``). Strip null
    ``include-all`` when ``include-controls`` is present.
    """
    results = payload.get("results")
    if not isinstance(results, list):
        return
    for result in results:
        if not isinstance(result, dict):
            continue
        reviewed = result.get("reviewed-controls")
        if not isinstance(reviewed, dict):
            continue
        selections = reviewed.get("control-selections")
        if not isinstance(selections, list):
            continue
        for selection in selections:
            if not isinstance(selection, dict):
                continue
            if selection.get("include-controls") and selection.get("include-all") is None:
                selection.pop("include-all", None)
            if selection.get("include-all") is not None and selection.get("include-controls") is None:
                selection.pop("include-controls", None)


def _build_sar_document_payload(sar: SecurityAssessmentResultsSAR) -> dict[str, object]:
    """Return the canonical OSCAL Assessment Results document dict for ``sar``.

    Single source of truth for both Trestle validation (Stage 4) and
    evidence serialization (Stage 5). Strips JSON nulls and normalizes
    reviewed-controls union shape so the bytes written to disk are
    exactly what ``assemble()`` validated.
    """
    assessment_results = _strip_nulls(json.loads(sar.json(by_alias=True)))
    if isinstance(assessment_results, dict):
        _normalize_reviewed_controls_for_trestle(assessment_results)
    return {"assessment-results": assessment_results}


def serialize_sar(sar: SecurityAssessmentResultsSAR) -> bytes:
    """Serialize ``sar`` to UTF-8 JSON bytes for evidence output.

    Uses ``_build_sar_document_payload`` — the same canonical form
    ``_validate_via_trestle_models`` parses — so validate-what-you-emit
    cannot drift between assembler and writer.
    """
    document = _build_sar_document_payload(sar)
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _validate_via_trestle_models(sar: SecurityAssessmentResultsSAR) -> None:
    """Round-trip the SAR through Trestle's pydantic models + AllValidator.

    This is **structural / model-level** validation only — it parses the
    SAR through Trestle's typed models and runs Trestle's
    ``AllValidator`` chain (Catalog / Duplicates / Refs / Links /
    RuleParameters).     It is NOT the published NIST OSCAL JSON Schema gate — that gate is
    its schema-strict complement, implemented in
    ``tests/test_schema_validation.py`` (runs in CI via the ``test`` job),
    validating the emitted SAR against the vendored schema at
    ``oscal_pipeline/schemas/oscal_assessment-results_schema-<OSCAL_VERSION>.json``;
    that gate catches the format/regex constraints this model-level pass relaxes.

    Failure modes this function does catch: duplicate UUIDs across
    observations/findings/parties, broken intra-document href refs,
    schema-shape violations enforced by oscal-pydantic's model regex.

    Failure modes this function does NOT catch: anything the OSCAL JSON
    Schema enforces that oscal-pydantic relaxes (e.g., the
    ``oscal-pydantic==2023.3.21`` release comments out several format
    constraints), TZ-aware datetime enforcement on every datetime field,
    full token-regex validation on control-id / objective-id fields.
    """
    from trestle.core.validator_factory import validator_factory
    from trestle.oscal.assessment_results import Model as TrestleSARModel

    payload = _build_sar_document_payload(sar)
    try:
        wrapper = TrestleSARModel.parse_obj(payload)
        trestle_model = wrapper.assessment_results
    except Exception as exc:
        raise SarValidationError(
            f"assembled SAR failed Trestle model parse: {exc}"
        ) from exc

    args = argparse.Namespace(mode="all", quiet=True)
    validator = validator_factory.get(args)
    if not validator.model_is_valid(trestle_model, quiet=True, trestle_root=None):
        # NB: this gates against Trestle's internal model validators
        # (Catalog / Duplicates / Refs / Links / RuleParameters). It does
        # NOT validate against the published NIST OSCAL JSON Schema — that
        # gate lives in ``tests/test_schema_validation.py`` (CI ``test`` job)
        # so the assembler stays import-time-cheap while CI still enforces
        # the vendored NIST schema before evidence ships.
        raise SarValidationError(
            f"assembled SAR failed Trestle validation: {validator.error_msg()}"
        )


def assemble(
    observations: Sequence[Observation],
    findings: Sequence[Finding],
    run_metadata: RunMetadata,
) -> SecurityAssessmentResultsSAR:
    """Combine transformed evidence into one schema-valid SAR document."""
    run_timestamp = _require_tz_aware(run_metadata.run_timestamp)
    # ``str(item.uuid)`` locks the sort key to the string form of the
    # UUID regardless of whether oscal-pydantic exposes ``uuid`` as a
    # bare ``str`` or as a typed root-model wrapper — Python's default
    # comparison on the wrapper would fall back to identity and raise
    # ``TypeError`` at sort time. The string sort is also lexicographic
    # over the canonical RFC 4122 hex form, which is the determinism
    # contract CM-3 cross-run-diffing relies on.
    sorted_observations = sorted(observations, key=lambda item: str(item.uuid))
    sorted_findings = sorted(findings, key=lambda item: str(item.uuid))

    title_date = run_timestamp.date().isoformat()
    result_title = (
        run_metadata.result_title
        or f"{run_metadata.source_tool} evidence run"
    )

    # ``AssessmentResult.observations`` and ``.findings`` are pydantic
    # ``min_items=1`` (``required=False``) — passing ``findings=[]`` for
    # an all-PASS run raises ``ValidationError``, but omitting the
    # kwarg entirely produces a schema-valid result with the field set
    # to ``None``. Continuous-monitoring (CA-7) runs that find no
    # failures must still emit a valid SAR; the conditional below is
    # the load-bearing fix for that case.
    result_kwargs: dict[str, object] = {
        "uuid": deterministic_uuid(
            "result",
            run_metadata.source_tool,
            run_timestamp.isoformat(),
        ),
        "title": result_title,
        "description": (
            f"Automated evidence collection from {run_metadata.source_tool}"
        ),
        "start": run_timestamp,
        "end": run_timestamp,
        "reviewed_controls": _build_reviewed_controls(sorted_findings),
    }
    if sorted_observations:
        result_kwargs["observations"] = list(sorted_observations)
    if sorted_findings:
        result_kwargs["findings"] = list(sorted_findings)

    sar = SecurityAssessmentResultsSAR(
        # SAR uuid namespace includes ``source_tool`` so two parallel
        # adapter runs at the same orchestrator timestamp do not collide
        # onto a single SAR UUID — see CM-3 cross-run-diff property.
        uuid=deterministic_uuid(
            "sar", run_timestamp.isoformat(), run_metadata.source_tool
        ),
        metadata=PublicationMetadata(
            title=f"Evidence Pipeline Run {title_date}",
            last_modified=run_timestamp,
            version=run_metadata.pipeline_version,
            oscal_version=OSCAL_VERSION,
            parties=[
                PartyOrganizationOrPerson(
                    uuid=deterministic_uuid("party", run_metadata.operator_name),
                    type="organization",
                    name=run_metadata.operator_name,
                )
            ],
        ),
        import_ap=ImportAssessmentPlan(href=run_metadata.assessment_plan_href),
        results=[AssessmentResult(**result_kwargs)],
    )

    _validate_via_trestle_models(sar)
    return sar

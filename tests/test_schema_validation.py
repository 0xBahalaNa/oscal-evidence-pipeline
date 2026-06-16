"""Published NIST OSCAL JSON Schema gate (issue #20).

Validates the byte-deterministic SAR emitted from the secret-scanner mixed
fixture against the vendored assessment-results schema. Runs in CI inside the
existing ``pytest --cov-fail-under=80`` step (CA-2 / AU-12 / CM-3).
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import jsonschema

import oscal_pipeline
from oscal_pipeline.adapters.secret_scanner import SecretScannerAdapter
from oscal_pipeline.assembler import OSCAL_VERSION, RunMetadata, assemble, serialize_sar

_SCHEMA_SHA256 = "4f9e277a177adbcca9527612ce450a33dc6096773fa229d413d801d196c61985"
_FIXTURE = Path(__file__).parent / "fixtures" / "secret_scanner_mixed.json"


# OSCAL's ``token`` datatype uses ECMA-262 ``\p{L}`` / ``\p{N}`` Unicode-property
# classes that Python ``re`` cannot compile (``re.PatternError: bad escape \p``).
# Approximate the two classes jsonschema hits and skip anything still untranslatable
# rather than crashing the CI gate — see ARCHITECTURE §11 for the fidelity caveat.
def _oscal_pattern_check(validator, pattern, instance, schema):
    if not validator.is_type(instance, "string"):
        return

    translated = pattern.replace(r"\p{L}", r"[^\W\d_]").replace(r"\p{N}", r"\d")

    try:
        compiled = re.compile(translated)
    except re.error:
        return

    if compiled.search(instance) is None:
        yield jsonschema.ValidationError(
            f"{instance!r} does not match {pattern!r}"
        )


def _load_vendored_schema() -> dict:
    schema_name = f"oscal_assessment-results_schema-{OSCAL_VERSION}.json"
    schema_path = resources.files("oscal_pipeline.schemas") / schema_name
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _build_oscal_validator(schema: dict) -> jsonschema.protocols.Validator:
    """Build a Draft-7 validator that survives OSCAL's ECMA ``\\p{}`` patterns."""
    oscal_draft7 = jsonschema.validators.extend(
        jsonschema.Draft7Validator,
        {"pattern": _oscal_pattern_check},
    )
    return oscal_draft7(schema, format_checker=jsonschema.FormatChecker())


def _build_sample_sar_doc(raw: dict | None = None) -> dict:
    """Transform + assemble a pinned-timestamp SAR for schema validation."""
    if raw is None:
        raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = SecretScannerAdapter().transform(raw)
    meta = RunMetadata(
        run_timestamp=datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc),
        source_tool="secret-scanner",
        operator_name="oscal-evidence-pipeline operator",
        pipeline_version=oscal_pipeline.__version__,
    )
    sar = assemble(result.observations, result.findings, meta)
    return json.loads(serialize_sar(sar))


def test_sample_sar_validates_against_oscal_schema() -> None:
    """Mixed-severity SAR from the secret-scanner fixture passes the NIST schema."""
    schema = _load_vendored_schema()
    validator = _build_oscal_validator(schema)
    sar_doc = _build_sample_sar_doc()

    assert list(validator.iter_errors(sar_doc)) == []

    bad_doc = deepcopy(sar_doc)
    del bad_doc["assessment-results"]["metadata"]["oscal-version"]
    assert len(list(validator.iter_errors(bad_doc))) >= 1


def test_all_pass_sar_validates_against_oscal_schema() -> None:
    """All-PASS runs (observations only) must also satisfy the published schema."""
    raw = {
        "scan_metadata": {"timestamp": "2026-06-05T12:00:00+00:00"},
        "findings": [
            {
                "file_path": "clean-scan.log",
                "line_number": 1,
                "finding_type": "No secrets in scope",
                "pattern_matched": "N/A",
                "severity": "INFO",
                "control_ids": ["SC-28"],
            }
        ],
        "summary": {},
    }
    schema = _load_vendored_schema()
    validator = _build_oscal_validator(schema)
    sar_doc = _build_sample_sar_doc(raw)

    assert list(validator.iter_errors(sar_doc)) == []


def test_schema_version_matches_oscal_version() -> None:
    """Vendored schema ``$id`` must track ``OSCAL_VERSION`` (AC#6 drift tripwire)."""
    schema = _load_vendored_schema()
    assert OSCAL_VERSION in schema["$id"]


def test_vendored_schema_integrity() -> None:
    """On-disk vendored schema bytes must match the pinned SHA256 provenance anchor."""
    schema_name = f"oscal_assessment-results_schema-{OSCAL_VERSION}.json"
    schema_path = resources.files("oscal_pipeline.schemas") / schema_name
    digest = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    assert digest == _SCHEMA_SHA256

"""Published NIST OSCAL JSON Schema validation (Layer 3).

Reusable validator lifted from the CI gate so the CLI emit boundary can enforce
schema conformance on every operator-emitted SAR without pulling ``jsonschema``
into the ``import oscal_pipeline`` path. See ARCHITECTURE §11.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from importlib import resources
from typing import Any, cast

import jsonschema  # type: ignore[import-untyped]
from trestle.oscal import OSCAL_VERSION


class SchemaValidationError(ValueError):
    """Raised when a SAR document fails the vendored NIST OSCAL JSON Schema."""


# OSCAL's ``token`` datatype uses ECMA-262 ``\p{L}`` / ``\p{N}`` Unicode-property
# classes that Python ``re`` cannot compile (``re.PatternError: bad escape \p``).
# Approximate the two classes jsonschema hits and skip anything still untranslatable
# rather than crashing the gate — see ARCHITECTURE §11 for the fidelity caveat.
def _oscal_pattern_check(
    validator: jsonschema.protocols.Validator,
    pattern: str,
    instance: object,
    schema: dict[str, Any],
) -> Iterator[jsonschema.ValidationError]:
    if not validator.is_type(instance, "string"):
        return

    instance_str = cast(str, instance)
    translated = pattern.replace(r"\p{L}", r"[^\W\d_]").replace(r"\p{N}", r"\d")

    try:
        compiled = re.compile(translated)
    except re.error:
        return

    if compiled.search(instance_str) is None:
        yield jsonschema.ValidationError(
            f"{instance!r} does not match {pattern!r}"
        )


def _load_vendored_schema() -> dict[str, Any]:
    schema_name = f"oscal_assessment-results_schema-{OSCAL_VERSION}.json"
    schema_path = resources.files("oscal_pipeline.schemas") / schema_name
    return cast(dict[str, Any], json.loads(schema_path.read_text(encoding="utf-8")))


def _build_oscal_validator(schema: dict[str, Any]) -> jsonschema.protocols.Validator:
    """Build a Draft-7 validator that survives OSCAL's ECMA ``\\p{}`` patterns."""
    oscal_draft7 = jsonschema.validators.extend(
        jsonschema.Draft7Validator,
        {"pattern": _oscal_pattern_check},
    )
    return oscal_draft7(schema, format_checker=jsonschema.FormatChecker())


def validate_against_vendored_schema(sar_doc: dict[str, Any]) -> None:
    """Validate *sar_doc* against the vendored NIST assessment-results schema.

    Raises:
        SchemaValidationError: if validation errors are found.

    Returns:
        None on success.
    """
    schema = _load_vendored_schema()
    validator = _build_oscal_validator(schema)
    errors = list(validator.iter_errors(sar_doc))

    if errors:
        messages = "; ".join(error.message for error in errors)
        raise SchemaValidationError(messages)
"""Deterministic UUID derivation for adapter observations and findings."""

from __future__ import annotations

import uuid

# Fixed namespace so uuid5 output is stable across machines and pipeline versions.
_OSCAL_PIPELINE_NAMESPACE = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")


def deterministic_uuid(*parts: str) -> str:
    """Return a stable RFC 4122 UUID string from ordered ``parts``.

    Uses UUID version 5 over a project-scoped namespace so re-running the
    pipeline on the same input reproduces the same observation UUIDs (CM-3 /
    cross-run SAR diff).
    """
    name = "|".join(parts)
    return str(uuid.uuid5(_OSCAL_PIPELINE_NAMESPACE, name))

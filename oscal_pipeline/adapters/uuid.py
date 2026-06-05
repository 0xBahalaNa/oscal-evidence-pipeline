"""Deterministic UUID derivation for adapter observations and findings."""

from __future__ import annotations

import uuid

# Fixed namespace so uuid5 output is stable across machines and pipeline
# versions. Generated once via ``uuid.uuid4()`` for this project; the
# previous value was the well-known RFC 4122 example UUID, which is
# shipped as the default in many tutorials and would silently collide
# with any other project that picked the same default. A project-scoped
# namespace makes observation UUIDs unguessable and ensures the
# CM-3 cross-run-diff property is genuinely scoped to this pipeline.
_OSCAL_PIPELINE_NAMESPACE = uuid.UUID("96368e43-bafd-432c-a608-66cb4df05b42")


def deterministic_uuid(*parts: str) -> str:
    """Return a stable RFC 4122 UUID string from ordered ``parts``.

    Uses UUID version 5 over a project-scoped namespace so re-running the
    pipeline on the same input reproduces the same observation UUIDs (CM-3 /
    cross-run SAR diff).
    """
    name = "|".join(parts)
    return str(uuid.uuid5(_OSCAL_PIPELINE_NAMESPACE, name))

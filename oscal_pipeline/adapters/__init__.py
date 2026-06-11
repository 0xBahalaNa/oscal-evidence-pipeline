"""Per-source-tool adapters.

Each adapter consumes a source audit tool's JSON output and produces
OSCAL ``observation`` objects via the typed models in ``oscal-pydantic``.
See ``ARCHITECTURE.md`` §3 Stage 3 for the design.

Public API re-exported here:

* :class:`Adapter` — the typed Protocol every adapter implements
* :class:`TransformResult` — observations + findings from ``transform``
* :func:`register_adapter` — decorator for adapter discovery
* :func:`find_adapter` — dispatch by input-JSON fingerprint; returns ``(key, adapter)``
* :data:`REGISTRY` — module-level mapping for introspection / tests
* :class:`AdapterAlreadyRegistered` / :class:`MultipleAdaptersMatch` /
  :class:`AdapterMatchError` — registry errors
"""

from oscal_pipeline.adapters.base import Adapter
from oscal_pipeline.adapters.registry import (
    REGISTRY,
    AdapterAlreadyRegistered,
    AdapterMatchError,
    MultipleAdaptersMatch,
    find_adapter,
    register_adapter,
)
from oscal_pipeline.adapters.result import TransformResult

# Register concrete adapters as import side effects (production dispatch).
from oscal_pipeline.adapters import secret_scanner as _secret_scanner  # noqa: F401

__all__ = [
    "REGISTRY",
    "Adapter",
    "AdapterAlreadyRegistered",
    "AdapterMatchError",
    "MultipleAdaptersMatch",
    "TransformResult",
    "find_adapter",
    "register_adapter",
]

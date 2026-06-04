"""Per-source-tool adapters.

Each adapter consumes a source audit tool's JSON output and produces
OSCAL ``observation`` objects via the typed models in ``oscal-pydantic``.
See ``ARCHITECTURE.md`` §3 Stage 3 for the design.

Public API re-exported here:

* :class:`Adapter` — the typed Protocol every adapter implements
* :func:`register_adapter` — decorator for adapter discovery
* :func:`find_adapter` — dispatch by input-JSON fingerprint
* :data:`REGISTRY` — module-level mapping for introspection / tests
* :class:`AdapterAlreadyRegistered` / :class:`MultipleAdaptersMatch` — registry errors

The first concrete adapter (``secret-scanner``) lands in issue #3.
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

__all__ = [
    "REGISTRY",
    "Adapter",
    "AdapterAlreadyRegistered",
    "AdapterMatchError",
    "MultipleAdaptersMatch",
    "find_adapter",
    "register_adapter",
]

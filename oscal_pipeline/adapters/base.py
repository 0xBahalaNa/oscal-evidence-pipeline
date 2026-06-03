"""Adapter Protocol — the typed contract every source-tool adapter implements.

The Protocol defined here is the load-bearing extension point referenced
by ``ARCHITECTURE.md`` §3 Stage 3. Each upstream audit tool (``s3-audit``,
``sg-audit``, ``cloudtrail-audit``, ``secret-scanner``, ``evidence-logger``)
is represented by exactly one adapter that knows how to detect its native
JSON shape and transform each finding into one or more OSCAL
``observation`` objects.

The Protocol is deliberately narrow. Control-ID mapping, observation
UUID derivation, and pass / fail policy live inside each concrete
adapter (landing in later issues). Hoisting any of that into the
Protocol itself would prematurely couple every adapter to a single
mapping-table shape — exactly the coupling ``ARCHITECTURE.md`` §1
property 1 ("audit tools stay simple") forbids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

# ``oscal-pydantic==2023.3.21`` is the only release of that library, and
# on Python 3.14 + pydantic v2 both its ``assessment_results`` and
# ``complete`` modules fail at import time (two unrelated upstream
# incompatibilities). Static type-checkers parse the ``TYPE_CHECKING``
# branch and resolve ``Observation`` correctly; the runtime branch
# never touches the broken modules. Revisit when oscal-pydantic ships a
# release that supports Python 3.14, or when issue #3 (the first real
# adapter that has to *instantiate* an ``Observation``) forces a
# venv-level decision. See CLAUDE.md EVOLUTION.
if TYPE_CHECKING:
    from oscal_pydantic.assessment_results import Observation
else:
    Observation = object


@runtime_checkable
class Adapter(Protocol):
    """Structural type for source-tool adapters (ARCHITECTURE.md §3 Stage 3).

    Each concrete adapter (one per upstream audit tool) implements
    ``matches`` and ``transform``. Per the Stage 3 mapping rules: one
    source-tool finding becomes one OSCAL ``observation``; FAIL / WARN
    observations get a paired ``finding`` downstream in Stage 4 (SAR
    assembly), not here.

    ``@runtime_checkable`` enables ``isinstance(obj, Adapter)`` for test
    assertions and registry-time sanity checks. Be aware the runtime
    check only verifies that method *names* exist on the object;
    signature-level enforcement is mypy's job at static-check time.
    """

    def matches(self, raw: dict[str, object]) -> bool:
        """Return ``True`` iff ``raw`` is JSON produced by this adapter's tool.

        Implementations should inspect structural fingerprints (top-level
        keys, presence of a marker field) rather than values, so the
        check stays cheap enough for the registry dispatcher to call on
        every adapter for every input file without measurable cost.
        """
        ...

    def transform(self, raw: dict[str, object]) -> list[Observation]:
        """Translate source-tool JSON into a list of OSCAL ``Observation`` objects.

        The adapter owns: deterministic ``uuid`` derivation so re-running
        the pipeline on the same input produces the same observation
        UUIDs (the CM-3 / KSI cross-run-diff property), mapping
        source-tool finding types into OSCAL ``methods`` and
        ``subjects``, and attaching control-ID ``props`` derived from
        the upstream tool's ``control_ids`` field.
        """
        ...

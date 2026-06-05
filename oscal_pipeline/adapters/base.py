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

from typing import Protocol, runtime_checkable

from oscal_pipeline.adapters.result import TransformResult


@runtime_checkable
class Adapter(Protocol):
    """Structural type for source-tool adapters (ARCHITECTURE.md §3 Stage 3).

    Each concrete adapter (one per upstream audit tool) implements
    ``matches`` and ``transform``. Per the Stage 3 mapping rules: one
    source-tool finding becomes one OSCAL ``observation``; FAIL / WARN
    severities get a paired ``finding`` in the same ``TransformResult``.

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

    def transform(self, raw: dict[str, object]) -> TransformResult:
        """Translate source-tool JSON into OSCAL observations and findings.

        The adapter owns: deterministic ``uuid`` derivation so re-running
        the pipeline on the same input produces the same observation
        UUIDs (the CM-3 / KSI cross-run-diff property), mapping
        source-tool finding types into OSCAL ``methods`` and
        ``subjects``, and attaching control-ID ``props`` on findings.
        """
        ...

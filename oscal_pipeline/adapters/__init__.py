"""Per-source-tool adapters.

Each adapter (introduced in later issues) consumes a source audit tool's
JSON output and produces OSCAL ``observation`` + ``finding`` objects via
the typed models in ``oscal-pydantic``.

The Adapter Protocol lands in issue #2; the first concrete adapter
(``secret-scanner``) lands in issue #3.
"""

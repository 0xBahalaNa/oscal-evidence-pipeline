"""Smoke test for the Issue #1 package skeleton.

Validates the issue acceptance criterion:

    python -c "import oscal_pipeline; print(oscal_pipeline.__version__)"

Adapter, ingestion, and assembly tests arrive in later issues; this file
only proves the package imports cleanly from an editable install and the
version string is populated via ``importlib.metadata``.
"""

import oscal_pipeline


def test_package_imports_and_exposes_version() -> None:
    assert isinstance(oscal_pipeline.__version__, str)
    assert oscal_pipeline.__version__, "__version__ must be a non-empty string"

"""Shared pytest fixtures for the test suite.

The ``_isolated_registry`` autouse fixture lives here (not inside an
individual test module) so every test file gets registry isolation
without having to redeclare the fixture. Without this, a test file that
imports a concrete adapter would auto-register it as an import side
effect, leaving REGISTRY polluted for every later test in the session.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from oscal_pipeline.adapters import REGISTRY


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    """Snapshot / clear / restore ``REGISTRY`` around every test in the suite.

    Production state (adapters auto-registered at import time) is
    preserved across the test — snapshot before, clear for isolation
    during, restore after — so tests see an empty registry but the rest
    of the session does not.
    """
    snapshot = dict(REGISTRY)
    REGISTRY.clear()
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)

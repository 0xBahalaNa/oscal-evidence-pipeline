"""Adapter transform output — observations plus optional findings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oscal_pydantic.assessment_results import Finding, Observation


@dataclass(frozen=True)
class TransformResult:
    """Typed bundle returned by ``Adapter.transform``.

    One source-tool run yields many observations; FAIL/WARN severities also
    produce linked ``Finding`` objects (PASS is observation-only).
    """

    observations: tuple["Observation", ...]
    findings: tuple["Finding", ...]

    @staticmethod
    def empty() -> TransformResult:
        return TransformResult(observations=(), findings=())

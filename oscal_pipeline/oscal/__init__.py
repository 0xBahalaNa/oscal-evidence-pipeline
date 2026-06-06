"""Shared OSCAL helpers used across adapters and assembly."""

from oscal_pipeline.oscal.slug import (
    InvalidControlIdError,
    control_id_slug,
    objective_id_slug,
)

__all__ = [
    "InvalidControlIdError",
    "control_id_slug",
    "objective_id_slug",
]

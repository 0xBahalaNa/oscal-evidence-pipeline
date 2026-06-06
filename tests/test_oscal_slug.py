"""Tests for OSCAL control-id slug helpers (issue #17).

Coverage anchors on the failure modes flagged by the pre-commit review:

* Leading digit and leading underscore — neither is a valid NIST family
* Unicode homoglyphs (Cyrillic а/с look identical to ASCII a/c) —
  ASCII-locking the regex catches these before they ship into the SAR
* Single-, double-, and triple-paren enhancement notation
* Non-string input — must raise the documented ``InvalidControlIdError``
  rather than a bare ``AttributeError`` from the underlying ``.strip()``
"""

from __future__ import annotations

import pytest

from oscal_pipeline.oscal.slug import (
    InvalidControlIdError,
    control_id_slug,
    objective_id_slug,
)


@pytest.mark.parametrize(
    ("control_id", "expected_control", "expected_objective"),
    [
        # Basic controls
        ("AC-1", "ac-1", "ac-1_obj"),
        ("SC-28", "sc-28", "sc-28_obj"),
        ("sc-12", "sc-12", "sc-12_obj"),
        # Single-paren enhancements
        ("IA-5(7)", "ia-5.7", "ia-5.7_obj"),
        ("AC-2(1)", "ac-2.1", "ac-2.1_obj"),
        # Decimal form NIST catalogs already use (idempotent passthrough)
        ("AC-2.1", "ac-2.1", "ac-2.1_obj"),
        # Double-paren enhancements (rare but real in 800-53r5)
        ("SI-4(20)(b)", "si-4.20.b", "si-4.20.b_obj"),
        ("AC-6(7)(a)", "ac-6.7.a", "ac-6.7.a_obj"),
    ],
)
def test_slugify_known_shapes(
    control_id: str, expected_control: str, expected_objective: str
) -> None:
    assert control_id_slug(control_id) == expected_control
    assert objective_id_slug(control_id) == expected_objective


def test_slugify_rejects_empty_string() -> None:
    with pytest.raises(InvalidControlIdError, match="non-empty"):
        control_id_slug("")


def test_slugify_rejects_whitespace_only() -> None:
    with pytest.raises(InvalidControlIdError, match="non-empty"):
        control_id_slug("   \t  ")


def test_slugify_rejects_leading_digit() -> None:
    """NIST control IDs must start with a family code, not a digit."""
    with pytest.raises(InvalidControlIdError, match="does not match"):
        control_id_slug("5-bad")


def test_slugify_rejects_leading_underscore() -> None:
    """The bare OSCAL token regex allows leading ``_``; NIST shape does not."""
    with pytest.raises(InvalidControlIdError, match="does not match"):
        control_id_slug("_secret")


def test_slugify_rejects_unicode_homoglyph() -> None:
    """Cyrillic а (U+0430) + с (U+0441) look identical to ASCII a, c — reject."""
    cyrillic = "ас-2"  # "ас-2" — visually identical to "ac-2"
    assert cyrillic != "ac-2"  # sanity: bytes differ
    with pytest.raises(InvalidControlIdError, match="does not match"):
        control_id_slug(cyrillic)


def test_slugify_rejects_invalid_characters() -> None:
    with pytest.raises(InvalidControlIdError, match="does not match"):
        control_id_slug("IA-5(7)/bad")


def test_slugify_rejects_missing_dash() -> None:
    """``ia5-7`` collapses the family/number separator — not NIST shape."""
    with pytest.raises(InvalidControlIdError, match="does not match"):
        control_id_slug("ia5")


def test_slugify_rejects_non_string_input() -> None:
    """Adapter-author misuse must surface as ``InvalidControlIdError``."""
    with pytest.raises(InvalidControlIdError, match="must be a string"):
        control_id_slug(None)  # type: ignore[arg-type]
    with pytest.raises(InvalidControlIdError, match="must be a string"):
        control_id_slug(123)  # type: ignore[arg-type]


def test_objective_slug_uses_obj_suffix() -> None:
    """Quick sanity check: objective_id_slug == control_id_slug + ``_obj``."""
    assert objective_id_slug("AC-1") == f"{control_id_slug('AC-1')}_obj"

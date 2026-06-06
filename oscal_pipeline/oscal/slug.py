"""Normalize NIST 800-53 control IDs into OSCAL-safe slug forms.

Source tools emit human-oriented IDs like ``IA-5(7)`` and (rarely)
``SI-4(20)(b)``; OSCAL ``control-id`` and ``objective-id`` fields require
a constrained ASCII token that resolves against the NIST 800-53 catalog.
These helpers convert once so every adapter and the assembler share the
same convention.

The slug regex is deliberately tighter than the bare OSCAL token regex:

* **ASCII-only** (no Cyrillic homoglyphs — ``ас-2`` with Cyrillic а+с
  would otherwise visually masquerade as ASCII ``ac-2`` and silently
  mis-attribute SAR control coverage)
* **NIST family-shape only** (must start with a 2-3-letter family code,
  not with a digit, not with an underscore)
* **Allows the dotted enhancement form this module produces**
  (``ia-5.7``, ``si-4.20.b``) and the decimal form NIST 800-53
  catalogs already use (``ac-2.1``)

The non-string input path raises :class:`InvalidControlIdError` rather
than the bare ``AttributeError`` from ``.strip()``, so adapter-author
misuse surfaces with the documented exception type the loud-failure
policy promises.
"""

from __future__ import annotations

import re

# NIST family-shape, ASCII-locked. Matches:
#   ac-1, sc-28, ia-5.7, si-4.20.b, ac-6.7.a
# Does NOT match (rejected by ``_normalize_base``):
#   5-bad        — leading digit
#   _secret      — leading underscore
#   ас-2         — Cyrillic homoglyph
#   AC-2         — uppercase (slugifier lowercases first; only post-lowercase form is checked)
#   ia5-7        — missing dash after family
#   ia-5.b.20    — alpha then number (NIST shape is base-num + .num* + .alpha?)
_OSCAL_CONTROL_ID_RE = re.compile(
    r"^[a-z]{2,3}-\d+(?:\.\d+)*(?:\.[a-z][a-z0-9]*)?$", re.ASCII
)

# NIST enhancement notation: ``AC-2(1)`` → base + (1).
# Supports nested forms: ``SI-4(20)(b)`` → base + (20)(b).
# Each parenthesized group becomes a dot-separated suffix.
_ENHANCEMENT_RE = re.compile(
    r"^([A-Za-z]+-\d+)((?:\([A-Za-z0-9]+\))+)$", re.ASCII
)
_ENHANCEMENT_PART_RE = re.compile(r"\(([A-Za-z0-9]+)\)", re.ASCII)


class InvalidControlIdError(ValueError):
    """Raised when a control ID cannot be slugified for OSCAL output."""


def _normalize_base(control_id: object) -> str:
    """Return a lowercase OSCAL control-id token from *control_id*.

    Validates type, shape, and ASCII-ness. Loud-failure on any deviation
    per the repo's evidence-pipeline policy: an unmappable control ID
    halts at ingest rather than emitting a synthetic / mis-attributed
    SAR target downstream.
    """
    if not isinstance(control_id, str):
        raise InvalidControlIdError(
            f"control_id must be a string; got {type(control_id).__name__}"
        )

    stripped = control_id.strip()
    if not stripped:
        raise InvalidControlIdError("control_id must be a non-empty string")

    match = _ENHANCEMENT_RE.match(stripped)
    if match:
        parts = _ENHANCEMENT_PART_RE.findall(match.group(2))
        slug = ".".join((match.group(1).lower(), *(p.lower() for p in parts)))
    else:
        slug = stripped.lower()

    if not _OSCAL_CONTROL_ID_RE.match(slug):
        raise InvalidControlIdError(
            f"control_id {control_id!r} slugifies to {slug!r}, "
            f"which does not match the NIST control-id pattern "
            f"(ASCII family-letters-dash-number, "
            f"e.g. ``ac-1``, ``ia-5.7``, ``si-4.20.b``)"
        )
    return slug


def control_id_slug(control_id: object) -> str:
    """Map a source-tool control ID to an OSCAL ``control-id`` token.

    Examples: ``IA-5(7)`` → ``ia-5.7``; ``SC-28`` → ``sc-28``;
    ``SI-4(20)(b)`` → ``si-4.20.b``.
    """
    return _normalize_base(control_id)


def objective_id_slug(control_id: object) -> str:
    """Map a source-tool control ID to an OSCAL ``objective-id`` target slug.

    Examples: ``IA-5(7)`` → ``ia-5.7_obj``; ``SC-28`` → ``sc-28_obj``.
    """
    return f"{control_id_slug(control_id)}_obj"

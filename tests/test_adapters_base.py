"""Tests for the Adapter Protocol and registry (issue #2).

Covers the six acceptance behaviors:

1. Protocol conformance via ``isinstance`` (runtime_checkable).
2. Decorator-based registration appends an instance to ``REGISTRY``.
3. Double registration raises ``AdapterAlreadyRegistered``.
4. Dispatch returns the matching adapter instance.
5. Dispatch returns ``None`` when no adapter claims the input.
6. Dispatch raises ``MultipleAdaptersMatch`` on ambiguity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from oscal_pipeline.adapters import (
    REGISTRY,
    Adapter,
    AdapterAlreadyRegistered,
    AdapterMatchError,
    MultipleAdaptersMatch,
    find_adapter,
    register_adapter,
)

if TYPE_CHECKING:
    from oscal_pydantic.assessment_results import Observation


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Snapshot / restore ``REGISTRY`` around each test.

    Once concrete adapters auto-register on import, we want isolation
    without clobbering production state — snapshot before, restore
    after. ``autouse=True`` so every test in this module gets the
    isolation without an explicit fixture argument.
    """
    snapshot = dict(REGISTRY)
    REGISTRY.clear()
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


class _StubAdapter:
    """Minimal adapter shape used by the conformance and registry tests."""

    def matches(self, raw: dict) -> bool:
        return raw.get("source_tool") == "stub"

    def transform(self, raw: dict) -> list[Observation]:
        return []


def test_stub_satisfies_adapter_protocol_at_runtime() -> None:
    assert isinstance(_StubAdapter(), Adapter)


def test_register_adapter_inserts_instance_into_registry() -> None:
    register_adapter("stub")(_StubAdapter)
    assert "stub" in REGISTRY
    assert isinstance(REGISTRY["stub"], _StubAdapter)


def test_register_adapter_raises_on_duplicate_key() -> None:
    register_adapter("stub")(_StubAdapter)
    with pytest.raises(AdapterAlreadyRegistered):
        register_adapter("stub")(_StubAdapter)


def test_find_adapter_returns_matching_instance() -> None:
    register_adapter("stub")(_StubAdapter)
    instance = find_adapter({"source_tool": "stub"})
    assert isinstance(instance, _StubAdapter)


def test_find_adapter_returns_none_when_no_match() -> None:
    register_adapter("stub")(_StubAdapter)
    assert find_adapter({"source_tool": "other"}) is None


def test_find_adapter_raises_when_multiple_adapters_claim_input() -> None:
    class _GreedyAdapter:
        def matches(self, raw: dict) -> bool:
            return True  # claims every input

        def transform(self, raw: dict) -> list[Observation]:
            return []

    register_adapter("stub")(_StubAdapter)
    register_adapter("greedy")(_GreedyAdapter)

    with pytest.raises(MultipleAdaptersMatch):
        find_adapter({"source_tool": "stub"})


# --- Commit 1: hardened dispatch + registration error paths ----------------


def test_register_adapter_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        register_adapter("")


def test_register_adapter_rejects_whitespace_key() -> None:
    with pytest.raises(ValueError, match="non-empty, non-whitespace"):
        register_adapter("   ")


def test_register_adapter_error_names_both_existing_and_rejected_class() -> None:
    register_adapter("stub")(_StubAdapter)

    class _OtherAdapter:
        def matches(self, raw: dict) -> bool:
            return False

        def transform(self, raw: dict) -> list[Observation]:
            return []

    with pytest.raises(AdapterAlreadyRegistered) as exc_info:
        register_adapter("stub")(_OtherAdapter)

    message = str(exc_info.value)
    assert "_StubAdapter" in message
    assert "_OtherAdapter" in message
    assert "rejected" in message


def test_find_adapter_wraps_matches_exceptions_with_adapter_context() -> None:
    class _BrokenAdapter:
        def matches(self, raw: dict) -> bool:
            return raw["nonexistent_key"] == "value"  # raises KeyError

        def transform(self, raw: dict) -> list[Observation]:
            return []

    register_adapter("broken")(_BrokenAdapter)

    with pytest.raises(AdapterMatchError) as exc_info:
        find_adapter({"some_other_key": "value"})

    message = str(exc_info.value)
    assert "broken" in message
    assert "_BrokenAdapter" in message
    assert "KeyError" in message
    assert isinstance(exc_info.value.__cause__, KeyError)


def test_find_adapter_treats_truthy_non_bool_as_no_match() -> None:
    class _TruthyNonBoolAdapter:
        def matches(self, raw: dict) -> bool:
            # Buggy: returns a truthy string instead of a strict bool.
            # The dispatcher must NOT silently dispatch on truthy values.
            return raw.get("source_tool", "")  # type: ignore[return-value]

        def transform(self, raw: dict) -> list[Observation]:
            return []

    register_adapter("truthy-bug")(_TruthyNonBoolAdapter)

    assert find_adapter({"source_tool": "anything"}) is None


def test_multiple_adapters_match_error_includes_keys_and_class_names() -> None:
    class _GreedyA:
        def matches(self, raw: dict) -> bool:
            return True

        def transform(self, raw: dict) -> list[Observation]:
            return []

    class _GreedyB:
        def matches(self, raw: dict) -> bool:
            return True

        def transform(self, raw: dict) -> list[Observation]:
            return []

    register_adapter("greedy-a")(_GreedyA)
    register_adapter("greedy-b")(_GreedyB)

    with pytest.raises(MultipleAdaptersMatch) as exc_info:
        find_adapter({})

    message = str(exc_info.value)
    assert "'greedy-a'" in message
    assert "'greedy-b'" in message
    assert "_GreedyA" in message
    assert "_GreedyB" in message

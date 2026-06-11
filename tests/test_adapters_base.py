"""Tests for the Adapter Protocol and registry (issue #2).

Covers Protocol conformance (callability + arity, not just attribute
existence — ``runtime_checkable`` alone is shallow), decorator-based
registration including the "returns the class unchanged" promise, the
two registry-level loud-failure policies (``AdapterAlreadyRegistered``,
``MultipleAdaptersMatch``), the dispatcher's exception-wrapping policy
(``AdapterMatchError``), and the strict-bool requirement on
``matches()``.

The ``_isolated_registry`` autouse fixture lives in ``conftest.py`` so
every test module in the suite gets registry isolation without
redeclaring it.
"""

from __future__ import annotations

import inspect

import pytest

from oscal_pipeline.adapters import (
    REGISTRY,
    Adapter,
    AdapterAlreadyRegistered,
    AdapterMatchError,
    MultipleAdaptersMatch,
    TransformResult,
    find_adapter,
    register_adapter,
)


class _StubAdapter:
    """Minimal adapter shape used by the conformance and registry tests."""

    def matches(self, raw: dict[str, object]) -> bool:
        return raw.get("source_tool") == "stub"

    def transform(self, raw: dict[str, object]) -> TransformResult:
        return TransformResult.empty()


def test_stub_satisfies_adapter_protocol_at_runtime() -> None:
    stub = _StubAdapter()
    # ``runtime_checkable`` only verifies attribute *names* exist — non-callable
    # attributes with the right names also pass. Add callability + arity
    # assertions so this test fails for actually-broken stub shapes.
    assert isinstance(stub, Adapter)
    assert callable(stub.matches)
    assert callable(stub.transform)
    assert list(inspect.signature(stub.matches).parameters) == ["raw"]
    assert list(inspect.signature(stub.transform).parameters) == ["raw"]


def test_register_adapter_inserts_instance_into_registry() -> None:
    result = register_adapter("stub")(_StubAdapter)
    assert "stub" in REGISTRY
    assert isinstance(REGISTRY["stub"], _StubAdapter)
    # The decorator's docstring promises it returns the class unchanged
    # so adapter modules can keep using the name after decoration.
    assert result is _StubAdapter


def test_register_adapter_raises_on_duplicate_key() -> None:
    register_adapter("stub")(_StubAdapter)
    with pytest.raises(AdapterAlreadyRegistered):
        register_adapter("stub")(_StubAdapter)


def test_find_adapter_returns_matching_key_and_instance() -> None:
    register_adapter("stub")(_StubAdapter)
    resolved = find_adapter({"source_tool": "stub"})
    assert resolved is not None
    key, instance = resolved
    assert key == "stub"
    assert isinstance(instance, _StubAdapter)


def test_find_adapter_returns_none_when_no_match() -> None:
    register_adapter("stub")(_StubAdapter)
    assert find_adapter({"source_tool": "other"}) is None


def test_find_adapter_raises_when_multiple_adapters_claim_input() -> None:
    class _GreedyAdapter:
        def matches(self, raw: dict[str, object]) -> bool:
            return True  # claims every input

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

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
        def matches(self, raw: dict[str, object]) -> bool:
            return False

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

    with pytest.raises(AdapterAlreadyRegistered) as exc_info:
        register_adapter("stub")(_OtherAdapter)

    message = str(exc_info.value)
    assert "_StubAdapter" in message
    assert "_OtherAdapter" in message
    assert "rejected" in message


def test_find_adapter_wraps_matches_exceptions_with_adapter_context() -> None:
    class _BrokenAdapter:
        def matches(self, raw: dict[str, object]) -> bool:
            return raw["nonexistent_key"] == "value"  # raises KeyError

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

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
        def matches(self, raw: dict[str, object]) -> bool:
            # Buggy: returns a truthy string instead of a strict bool.
            # The dispatcher must NOT silently dispatch on truthy values.
            return raw.get("source_tool", "")  # type: ignore[return-value]

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

    register_adapter("truthy-bug")(_TruthyNonBoolAdapter)

    assert find_adapter({"source_tool": "anything"}) is None


def test_multiple_adapters_match_error_includes_keys_and_class_names() -> None:
    class _GreedyA:
        def matches(self, raw: dict[str, object]) -> bool:
            return True

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

    class _GreedyB:
        def matches(self, raw: dict[str, object]) -> bool:
            return True

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

    register_adapter("greedy-a")(_GreedyA)
    register_adapter("greedy-b")(_GreedyB)

    with pytest.raises(MultipleAdaptersMatch) as exc_info:
        find_adapter({})

    message = str(exc_info.value)
    assert "'greedy-a'" in message
    assert "'greedy-b'" in message
    assert "_GreedyA" in message
    assert "_GreedyB" in message

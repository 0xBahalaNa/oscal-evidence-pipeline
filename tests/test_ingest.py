"""Tests for Stage 2 ingestion (issue #4)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from oscal_pipeline.adapters import (
    AdapterMatchError,
    MultipleAdaptersMatch,
    TransformResult,
    register_adapter,
)
from oscal_pipeline.adapters.secret_scanner import SecretScannerAdapter
from oscal_pipeline.ingest import ingest


class _AlphaAdapter:
    def matches(self, raw: dict[str, object]) -> bool:
        return "alpha" in raw

    def transform(self, raw: dict[str, object]) -> TransformResult:
        return TransformResult.empty()


class _BetaAdapter:
    def matches(self, raw: dict[str, object]) -> bool:
        return "beta" in raw

    def transform(self, raw: dict[str, object]) -> TransformResult:
        return TransformResult.empty()


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


class _BrokenAdapter:
    def matches(self, raw: dict[str, object]) -> bool:
        return raw["nonexistent_key"] == "value"

    def transform(self, raw: dict[str, object]) -> TransformResult:
        return TransformResult.empty()


def test_ingest_selects_matching_adapter_among_many(tmp_path: Path) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    register_adapter("beta")(_BetaAdapter)
    (tmp_path / "a.json").write_text('{"alpha": true}', encoding="utf-8")

    results = list(ingest(tmp_path))

    assert len(results) == 1
    _, key, adapter, _ = results[0]
    assert key == "alpha"
    assert isinstance(adapter, _AlphaAdapter)


def test_ingest_skips_bad_encoding_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    (tmp_path / "aaa_bad.json").write_bytes(b"\xff\xfe\x00bad")
    good = tmp_path / "zzz_good.json"
    good.write_text('{"alpha": true}', encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        results = list(ingest(tmp_path))

    assert [path.name for path, _, _, _ in results] == ["zzz_good.json"]
    assert any(record.levelname == "ERROR" for record in caplog.records)
    assert any("aaa_bad.json" in record.message for record in caplog.records)


def test_ingest_yields_tuple_for_known_schema(tmp_path: Path) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    payload = {"alpha": True, "value": 1}
    input_file = tmp_path / "known.json"
    input_file.write_text(json.dumps(payload), encoding="utf-8")

    results = list(ingest(tmp_path))

    assert len(results) == 1
    path, key, adapter, raw = results[0]
    assert path == input_file
    assert key == "alpha"
    assert isinstance(adapter, _AlphaAdapter)
    assert raw == payload


def test_ingest_yields_registry_key_matching_registration(tmp_path: Path) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    (tmp_path / "payload.json").write_text('{"alpha": true}', encoding="utf-8")

    _, key, _, _ = list(ingest(tmp_path))[0]

    assert key == "alpha"


def test_ingest_skips_unknown_schema_with_warning_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    unknown = tmp_path / "unknown.json"
    unknown.write_text("{}", encoding="utf-8")
    good = tmp_path / "zzz_good.json"
    good.write_text('{"alpha": true}', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        results = list(ingest(tmp_path))

    assert len(results) == 1
    assert results[0][0].name == "zzz_good.json"
    assert any(
        record.levelname == "WARNING"
        and "no adapter recognizes this schema" in record.message
        for record in caplog.records
    )
    assert any(str(unknown) in record.message for record in caplog.records)


def test_ingest_skips_non_dict_top_level_with_warning_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    bad = tmp_path / "list.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    good = tmp_path / "zzz_good.json"
    good.write_text('{"alpha": true}', encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        results = list(ingest(tmp_path))

    assert len(results) == 1
    assert results[0][0].name == "zzz_good.json"
    assert any(
        record.levelname == "WARNING" and "not an object" in record.message
        for record in caplog.records
    )
    assert any(str(bad) in record.message for record in caplog.records)


def test_ingest_skips_malformed_json_with_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    input_file = tmp_path / "bad.json"
    input_file.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        results = list(ingest(tmp_path))

    assert results == []
    assert any(
        record.levelname == "ERROR"
        and "skipping" in record.message
        and str(input_file) in record.message
        for record in caplog.records
    )


def test_ingest_empty_dir_logs_info_and_yields_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        results = list(ingest(tmp_path))

    assert results == []
    assert any(
        record.levelname == "INFO" and "no JSON files found" in record.message
        for record in caplog.records
    )


def test_ingest_raises_file_not_found_for_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        list(ingest(missing))


def test_ingest_raises_not_a_directory_for_file_path(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-dir.json"
    file_path.write_text("{}", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="not a directory"):
        list(ingest(file_path))


def test_ingest_propagates_multiple_adapters_match(tmp_path: Path) -> None:
    register_adapter("greedy-a")(_GreedyA)
    register_adapter("greedy-b")(_GreedyB)
    input_file = tmp_path / "ambiguous.json"
    input_file.write_text('{"alpha": true}', encoding="utf-8")

    with pytest.raises(MultipleAdaptersMatch) as exc_info:
        list(ingest(tmp_path))

    assert any("while ingesting" in note for note in exc_info.value.__notes__)
    assert str(input_file) in exc_info.value.__notes__[0]


def test_ingest_propagates_adapter_match_error(tmp_path: Path) -> None:
    register_adapter("broken")(_BrokenAdapter)
    input_file = tmp_path / "broken.json"
    input_file.write_text('{"alpha": true}', encoding="utf-8")

    with pytest.raises(AdapterMatchError) as exc_info:
        list(ingest(tmp_path))

    assert any("while ingesting" in note for note in exc_info.value.__notes__)
    assert str(input_file) in exc_info.value.__notes__[0]


def test_ingest_yields_files_in_sorted_order(tmp_path: Path) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    (tmp_path / "c.json").write_text('{"alpha": true, "name": "c"}', encoding="utf-8")
    (tmp_path / "a.json").write_text('{"alpha": true, "name": "a"}', encoding="utf-8")
    (tmp_path / "b.json").write_text('{"alpha": true, "name": "b"}', encoding="utf-8")

    results = list(ingest(tmp_path))

    assert [path.name for path, _, _, _ in results] == ["a.json", "b.json", "c.json"]


def test_ingest_accepts_bom_prefixed_json(tmp_path: Path) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    bom_file = tmp_path / "bom.json"
    bom_file.write_bytes(b"\xef\xbb\xbf" + b'{"alpha": true}')

    _, key, adapter, raw = list(ingest(tmp_path))[0]

    assert key == "alpha"
    assert isinstance(adapter, _AlphaAdapter)
    assert raw == {"alpha": True}


def test_ingest_raises_file_not_found_at_call_time(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        ingest(missing)


def test_ingest_raises_not_a_directory_at_call_time(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-dir.json"
    file_path.write_text("{}", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="not a directory"):
        ingest(file_path)


def test_ingest_skips_recursion_error_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    (tmp_path / "deep.json").write_text("[" * 200_000, encoding="utf-8")
    good = tmp_path / "zzz_good.json"
    good.write_text('{"alpha": true}', encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        results = list(ingest(tmp_path))

    assert [path.name for path, _, _, _ in results] == ["zzz_good.json"]


def test_ingest_halts_run_without_reaching_later_files(tmp_path: Path) -> None:
    register_adapter("alpha")(_AlphaAdapter)
    register_adapter("beta")(_BetaAdapter)
    (tmp_path / "a.json").write_text('{"alpha": true}', encoding="utf-8")
    (tmp_path / "poison.json").write_text(
        '{"alpha": true, "beta": true}', encoding="utf-8"
    )
    (tmp_path / "z.json").write_text('{"alpha": true}', encoding="utf-8")

    gen = ingest(tmp_path)
    first = next(gen)
    assert first[0].name == "a.json"

    with pytest.raises(MultipleAdaptersMatch):
        next(gen)


def test_ingest_dispatches_production_secret_scanner_adapter(
    tmp_path: Path,
) -> None:
    # Register production adapter explicitly — conftest clears REGISTRY per test,
    # and importlib.reload would desync exception/class identities for later tests.
    register_adapter("secret-scanner")(SecretScannerAdapter)
    fixture_src = Path(__file__).parent / "fixtures" / "secret_scanner_mixed.json"
    (tmp_path / "scan.json").write_text(
        fixture_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    _, key, adapter, _ = list(ingest(tmp_path))[0]

    assert key == "secret-scanner"
    assert isinstance(adapter, SecretScannerAdapter)

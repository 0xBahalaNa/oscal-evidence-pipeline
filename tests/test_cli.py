"""Tests for CLI entry point (issue #7)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from oscal_pipeline.adapters import REGISTRY
from oscal_pipeline.adapters.secret_scanner import SecretScannerAdapter
from oscal_pipeline.cli import (
    EXIT_NO_INPUT,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
    InputDirError,
    MixedSourceToolsError,
    NoInputError,
    _resolve_operator,
    _run_pipeline,
    main,
)


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "secret-scanner-output"


@pytest.fixture(autouse=True)
def _register_secret_scanner_adapter() -> None:
    """Re-register production adapter after conftest clears ``REGISTRY``."""
    REGISTRY["secret-scanner"] = SecretScannerAdapter()


def test_help_shows_run_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "oscal-pipeline" in captured.out
    assert "run" in captured.out


def test_run_help_lists_required_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--input-dir" in captured.out
    assert "--output" in captured.out
    assert "--profile" in captured.out
    assert "fedramp-high" in captured.out


def test_run_pipeline_writes_valid_sar(tmp_path: Path) -> None:
    output_path = _run_pipeline(_FIXTURE_DIR, tmp_path, "fedramp-high")
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "assessment-results" in payload
    assert payload["assessment-results"]["metadata"]["version"]


def test_run_pipeline_default_profile_in_assessment_plan_href(
    tmp_path: Path,
) -> None:
    _run_pipeline(_FIXTURE_DIR, tmp_path, "fedramp-high")
    written = sorted(tmp_path.glob("assessment-results-*.json"))[0]
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert (
        payload["assessment-results"]["import-ap"]["href"]
        == "#fedramp-high-evidence-plan"
    )


def test_run_pipeline_raises_for_missing_input_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(InputDirError, match="does not exist"):
        _run_pipeline(missing, tmp_path, "fedramp-high")


def test_run_pipeline_raises_for_empty_input_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(NoInputError, match="no recognizable"):
        _run_pipeline(empty, tmp_path, "fedramp-high")


def test_run_pipeline_raises_for_unrecognized_json_only(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "unknown.json").write_text('{"foo": true}', encoding="utf-8")
    with pytest.raises(NoInputError, match="no recognizable"):
        _run_pipeline(input_dir, tmp_path, "fedramp-high")


def test_run_pipeline_raises_for_mixed_source_tools(tmp_path: Path) -> None:
    from oscal_pipeline.adapters import TransformResult, register_adapter

    class _AlphaAdapter:
        def matches(self, raw: dict[str, object]) -> bool:
            return "alpha" in raw

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

    register_adapter("alpha")(_AlphaAdapter)
    input_dir = tmp_path / "mixed"
    input_dir.mkdir()
    shutil.copy(
        _FIXTURE_DIR / "secret_scanner_mixed.json",
        input_dir / "scanner.json",
    )
    (input_dir / "alpha.json").write_text('{"alpha": true}', encoding="utf-8")

    with pytest.raises(MixedSourceToolsError, match="mixes source tools"):
        _run_pipeline(input_dir, tmp_path, "fedramp-high")


def test_main_exits_2_for_missing_input_dir(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--input-dir", str(missing), "--output", str(tmp_path / "out")])
    assert exc_info.value.code == EXIT_NO_INPUT
    assert "does not exist" in caplog.text


def test_main_exits_2_for_file_as_input_dir(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    a_file = tmp_path / "not_a_dir.json"
    a_file.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--input-dir", str(a_file), "--output", str(tmp_path / "out")])
    assert exc_info.value.code == EXIT_NO_INPUT
    assert "not a directory" in caplog.text.lower()


def test_main_exits_2_for_no_recognized_input(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--input-dir", str(input_dir), "--output", str(tmp_path / "out")])
    assert exc_info.value.code == EXIT_NO_INPUT
    assert "no recognizable" in caplog.text


def test_main_exits_1_for_validation_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch(
        "oscal_pipeline.cli.assemble",
        side_effect=ValueError("bad SAR"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--input-dir",
                    str(_FIXTURE_DIR),
                    "--output",
                    str(tmp_path),
                ]
            )
    assert exc_info.value.code == EXIT_VALIDATION
    assert "bad SAR" in caplog.text


def test_main_exits_1_for_ambiguous_adapters(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from oscal_pipeline.adapters import TransformResult, register_adapter

    class _AmbiguousA:
        def matches(self, raw: dict[str, object]) -> bool:
            return "shared" in raw

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

    class _AmbiguousB:
        def matches(self, raw: dict[str, object]) -> bool:
            return "shared" in raw

        def transform(self, raw: dict[str, object]) -> TransformResult:
            return TransformResult.empty()

    register_adapter("alpha-amb")(_AmbiguousA)
    register_adapter("beta-amb")(_AmbiguousB)
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "doc.json").write_text('{"shared": true}', encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--input-dir", str(input_dir), "--output", str(tmp_path / "out")])
    assert exc_info.value.code == EXIT_VALIDATION
    assert "multiple adapters" in caplog.text.lower()


def test_resolve_operator_falls_back_when_getuser_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch("oscal_pipeline.cli.getpass.getuser", side_effect=OSError("no passwd")):
        assert _resolve_operator() == "unknown-operator"
    assert "unknown-operator" in caplog.text


def test_run_pipeline_succeeds_when_operator_unresolved(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch("oscal_pipeline.cli.getpass.getuser", side_effect=OSError("no passwd")):
        output_path = _run_pipeline(_FIXTURE_DIR, tmp_path, "fedramp-high")
    assert output_path.exists()
    assert "unknown-operator" in caplog.text


def test_main_exits_0_on_success(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                "--input-dir",
                str(_FIXTURE_DIR),
                "--output",
                str(tmp_path),
            ]
        )
    assert exc_info.value.code == EXIT_SUCCESS
    written = sorted(tmp_path.glob("assessment-results-*.json"))
    assert written, "success must write a SAR artifact"
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert "assessment-results" in payload


def test_smoke_test_fixture_path_exists() -> None:
    """Issue #7 AC names ``tests/fixtures/secret-scanner-output``."""
    assert _FIXTURE_DIR.is_dir()
    json_files = list(_FIXTURE_DIR.glob("*.json"))
    assert json_files, "smoke-test fixture directory must contain JSON"

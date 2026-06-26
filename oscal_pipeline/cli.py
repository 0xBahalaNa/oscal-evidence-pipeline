"""CLI entry point — ``oscal-pipeline run``.

Wires Stage 2 (ingest) through Stage 5 (write) into a single command
for operators, CI, and scheduled jobs.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import oscal_pipeline
from oscal_pipeline.adapters import AdapterMatchError, MultipleAdaptersMatch
from oscal_pipeline.assembler import RunMetadata, SarValidationError, assemble, serialize_sar
from oscal_pipeline.ingest import ingest
from oscal_pipeline.schemas.validate import SchemaValidationError, validate_against_vendored_schema
from oscal_pipeline.writer import write

if TYPE_CHECKING:
    from oscal_pydantic.assessment_results import Finding, Observation

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_VALIDATION = 1
EXIT_NO_INPUT = 2

_DEFAULT_OUTPUT_DIR = Path("evidence")
_DEFAULT_PROFILE = "fedramp-high"


class CliError(Exception):
    """Base for CLI failures mapped to a non-zero exit code."""

    exit_code: int = EXIT_VALIDATION


class NoInputError(CliError):
    """No recognizable evidence files were ingested."""

    exit_code = EXIT_NO_INPUT


class InputDirError(CliError):
    """``--input-dir`` does not exist or is not a directory."""

    exit_code = EXIT_NO_INPUT


class MixedSourceToolsError(CliError):
    """Input directory contains evidence from more than one source tool."""

    exit_code = EXIT_VALIDATION


def _resolve_operator() -> str:
    """OS login name for AU-3 provenance, with a safe fallback.

    ``getpass.getuser()`` raises ``OSError`` in container/CI/cron environments
    with no passwd entry and no ``USER``/``LOGNAME`` env — this CLI's stated
    target. Degrade to a sentinel so a completed evidence run still produces
    its SAR.
    """
    try:
        return getpass.getuser()
    except OSError:
        logger.warning("operator name unresolved; using 'unknown-operator'")
        return "unknown-operator"


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oscal-pipeline",
        description=(
            "Transform compliance audit-tool JSON into OSCAL "
            "Assessment Results (SAR) evidence."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Ingest audit-tool JSON and emit a validated SAR.",
    )
    run_parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        metavar="PATH",
        help="Directory containing upstream audit-tool ``*.json`` output.",
    )
    run_parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        metavar="PATH",
        help=(
            "Directory for timestamped SAR output "
            f"(default: {_DEFAULT_OUTPUT_DIR})."
        ),
    )
    run_parser.add_argument(
        "--profile",
        default=_DEFAULT_PROFILE,
        metavar="ID",
        help=(
            "OSCAL profile identifier for the assessment baseline "
            f"(default: {_DEFAULT_PROFILE})."
        ),
    )
    return parser


def _run_pipeline(input_dir: Path, output_dir: Path, profile: str) -> Path:
    """Execute ingest → transform → assemble → schema-validate → write."""
    try:
        ingested = list(ingest(input_dir))
    except FileNotFoundError as exc:
        raise InputDirError(str(exc)) from exc
    except NotADirectoryError as exc:
        raise InputDirError(str(exc)) from exc

    if not ingested:
        raise NoInputError(
            f"no recognizable audit-tool JSON found in {input_dir}"
        )

    source_tools = {key for _, key, _, _ in ingested}
    if len(source_tools) > 1:
        tools = ", ".join(sorted(source_tools))
        raise MixedSourceToolsError(
            "input directory mixes source tools "
            f"({tools}); run one tool per invocation in v1.0"
        )

    source_tool = next(iter(source_tools))
    run_timestamp = datetime.now(timezone.utc)

    observations: list[Observation] = []
    findings: list[Finding] = []

    for path, _key, adapter, raw in ingested:
        logger.info("transforming %s via %s", path.name, type(adapter).__name__)
        result = adapter.transform(raw)
        observations.extend(result.observations)
        findings.extend(result.findings)

    run_metadata = RunMetadata(
        run_timestamp=run_timestamp,
        source_tool=source_tool,
        operator_name=_resolve_operator(),
        pipeline_version=oscal_pipeline.__version__,
        assessment_plan_href=f"#{profile}-evidence-plan",
    )

    sar = assemble(observations, findings, run_metadata)
    sar_doc = json.loads(serialize_sar(sar))
    validate_against_vendored_schema(sar_doc)
    output_path = write(sar, output_dir)
    logger.info("wrote SAR evidence to %s", output_path)
    return output_path


def _cmd_run(args: argparse.Namespace) -> int:
    """Handle ``oscal-pipeline run``."""
    _run_pipeline(args.input_dir, args.output, args.profile)
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point for ``[project.scripts]``."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _configure_logging(args.verbose)

    try:
        if args.command == "run":
            exit_code = _cmd_run(args)
        else:
            parser.error(f"unknown command: {args.command}")
    except CliError as exc:
        logger.error("%s", exc)
        sys.exit(exc.exit_code)
    except (SarValidationError, SchemaValidationError, ValueError) as exc:
        logger.error("%s", exc)
        sys.exit(EXIT_VALIDATION)
    except (MultipleAdaptersMatch, AdapterMatchError) as exc:
        logger.error("%s", exc)
        sys.exit(EXIT_VALIDATION)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

"""Stage 2 — read audit-tool JSON from a directory and dispatch to adapters.

Each ``*.json`` file in ``input_dir`` is loaded, schema-fingerprinted via
``find_adapter``, and yielded as ``(path, key, adapter, raw)`` when exactly
one adapter claims the document. ``key`` is the registry source-tool name
(e.g. ``"secret-scanner"``) required by Stage 4 assembly. Unknown schemas
are skipped with a warning; ambiguous or broken adapter fingerprints
propagate as registry errors and are **run-fatal** — do not catch-and-resume
or later files are silently dropped when the generator finalizes.

See ``ARCHITECTURE.md`` §3 Stage 2 for failure-mode policy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

from oscal_pipeline.adapters import (
    Adapter,
    AdapterMatchError,
    MultipleAdaptersMatch,
    find_adapter,
)

logger = logging.getLogger(__name__)


def _validate_input_dir(input_dir: Path) -> None:
    """Raise at call time if ``input_dir`` cannot be scanned."""
    if not input_dir.exists():
        raise FileNotFoundError(f"input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"input path is not a directory: {input_dir}")


def ingest(
    input_dir: Path,
) -> Iterator[tuple[Path, str, Adapter, dict[str, object]]]:
    """Yield ``(path, registry_key, adapter, raw_json)`` for each recognized file.

    Validates ``input_dir`` at call time (before returning the iterator).
    Iterates ``input_dir.glob("*.json")`` in sorted filename order (CM-3
    determinism). Malformed or unrecognized files are logged and skipped.
    ``MultipleAdaptersMatch`` and ``AdapterMatchError`` propagate to the
    consumer and halt the run — they must not be caught here.
    """
    _validate_input_dir(input_dir)
    return _ingest_files(input_dir)


def _ingest_files(
    input_dir: Path,
) -> Iterator[tuple[Path, str, Adapter, dict[str, object]]]:
    """Scan ``input_dir`` and yield recognized audit-tool JSON documents."""
    paths = sorted(input_dir.glob("*.json"))
    if not paths:
        logger.info("no JSON files found in %s", input_dir)
        return

    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            RecursionError,
        ) as exc:
            logger.error("skipping %s: %s", path, exc)
            continue
        if not isinstance(raw, dict):
            logger.warning(
                "skipping %s: top-level JSON is %s, not an object",
                path,
                type(raw).__name__,
            )
            continue
        # find_adapter stays outside the parse try — its ambiguity errors
        # must propagate, not be swallowed as malformed input.
        try:
            resolved = find_adapter(raw)
        except (MultipleAdaptersMatch, AdapterMatchError) as exc:
            exc.add_note(f"while ingesting {path}")
            raise
        if resolved is None:
            logger.warning("skipping %s: no adapter recognizes this schema", path)
            continue
        key, adapter = resolved
        yield path, key, adapter, raw

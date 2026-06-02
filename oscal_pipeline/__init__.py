"""OSCAL Evidence Pipeline package root.

Exposes ``__version__`` sourced from installed package metadata so that
``pyproject.toml`` stays the single source of truth for the version
number — bumping it in one place updates every reader.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("oscal-pipeline")
except PackageNotFoundError:
    # Importing from a source checkout without ``pip install -e .`` first
    # leaves the package metadata unregistered. Surfacing a clear sentinel
    # rather than crashing on import keeps ad-hoc REPL use friendly.
    # The acceptance smoke test always exercises the installed path, so
    # this branch should not appear during CI.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]

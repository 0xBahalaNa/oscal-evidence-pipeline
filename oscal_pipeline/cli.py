"""CLI entry point — ``oscal-pipeline run``.

This module exists in v0.1.0 as a stub so that the ``[project.scripts]``
declaration in ``pyproject.toml`` resolves at install time and
``pip install -e .`` succeeds without errors. The real CLI body lands in
issue #7 once the adapter framework, ingestion, assembly, and writer
modules are in place.
"""


def main() -> None:
    """Console-script entry point.

    Invoked by the ``oscal-pipeline`` script that ``pip`` generates from
    the ``[project.scripts]`` table.
    """
    raise NotImplementedError(
        "oscal-pipeline CLI is not implemented yet. "
        "Tracking issue: "
        "https://github.com/0xBahalaNa/oscal-evidence-pipeline/issues/7"
    )

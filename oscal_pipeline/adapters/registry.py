"""Adapter registry — discovery and dispatch for source-tool adapters.

The registry is a module-level mapping populated by the
``register_adapter`` decorator at import time. The ingestion stage
(landing in issue #5) calls ``find_adapter(raw)`` to resolve which
adapter owns a given input JSON document. The dispatcher iterates
registered adapters and returns the one whose ``matches`` returns
``True``.

Two policies are enforced loudly because silent failures in an evidence
pipeline are exactly the kind of regression CJIS AU-6 weekly reviews are
supposed to catch *late*. We would rather fail at ingest with an
explicit error:

* **Double registration raises.** Re-registering the same key — usually
  an editing accident or two adapters claiming the same source tool —
  raises ``AdapterAlreadyRegistered``. Silent override would let one
  adapter mask another and produce wrong OSCAL observations downstream
  with no obvious failure mode.
* **Multiple matches raise.** If two adapters' ``matches`` both return
  ``True`` for the same input, ``find_adapter`` raises
  ``MultipleAdaptersMatch`` rather than silently picking the first one.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from oscal_pipeline.adapters.base import Adapter


class AdapterAlreadyRegistered(Exception):
    """Raised by ``register_adapter`` when ``key`` is already in the registry."""


class MultipleAdaptersMatch(Exception):
    """Raised by ``find_adapter`` when two or more adapters claim the same input."""


# Module-level dict so test fixtures can snapshot / restore cleanly
# between tests. A class-based singleton would force every test to
# reach into class-level state — same blast radius, more ceremony.
# Values are *instances*, not classes: adapters are stateless per
# convention (the Protocol carries no constructor), so a single shared
# instance per registered adapter is correct and avoids re-instantiating
# on every dispatch call.
REGISTRY: dict[str, Adapter] = {}

T = TypeVar("T", bound=type[Adapter])


def register_adapter(key: str) -> Callable[[T], T]:
    """Decorator: register an adapter class under ``key``.

    ``key`` is the canonical source-tool name (e.g. ``"secret-scanner"``,
    ``"s3-audit"``) and matches the directory layout under
    ``oscal_pipeline/adapters/``. The decorated class is instantiated
    immediately and the *instance* is stored in the registry, so
    ``find_adapter`` does not have to instantiate on every dispatch.

    The decorator returns the class unchanged so the adapter module can
    use it directly (e.g. for tests) after decoration.
    """

    def _decorator(cls: T) -> T:
        if key in REGISTRY:
            existing = type(REGISTRY[key]).__name__
            raise AdapterAlreadyRegistered(
                f"adapter already registered for key {key!r}: {existing}"
            )
        REGISTRY[key] = cls()
        return cls

    return _decorator


def find_adapter(raw: dict[str, object]) -> Adapter | None:
    """Return the adapter that claims ``raw``, or ``None`` if none claim it.

    Iterates registered adapters in registration order and returns the
    one whose ``matches(raw)`` returns ``True``. Raises
    ``MultipleAdaptersMatch`` if more than one claims ``raw`` — silent
    ambiguity here would propagate downstream as duplicated OSCAL
    observations with no clear authorship.
    """
    matched: list[Adapter] = [
        adapter for adapter in REGISTRY.values() if adapter.matches(raw)
    ]

    if not matched:
        return None
    if len(matched) > 1:
        names = ", ".join(type(adapter).__name__ for adapter in matched)
        raise MultipleAdaptersMatch(
            f"multiple adapters claim this input: {names}"
        )
    return matched[0]

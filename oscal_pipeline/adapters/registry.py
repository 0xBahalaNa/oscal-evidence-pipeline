"""Adapter registry — discovery and dispatch for source-tool adapters.

The registry is a module-level mapping populated by the
``register_adapter`` decorator at import time. The ingestion stage
(landing in issue #5) calls ``find_adapter(raw)`` to resolve which
adapter owns a given input JSON document.

Three policies are enforced loudly because silent failures in an evidence
pipeline are exactly the kind of regression CJIS AU-6 weekly reviews are
supposed to catch *late*. We would rather fail at ingest with an
explicit error than ship silently-incomplete evidence:

* **Double registration raises.** Re-registering the same key — usually
  an editing accident or two adapters claiming the same source tool —
  raises ``AdapterAlreadyRegistered``. Silent override would let one
  adapter mask another and produce wrong OSCAL observations downstream
  with no obvious failure mode. The error names both the previously
  registered class and the rejected one so the operator can find both.
* **Multiple matches raise.** If two adapters' ``matches`` both return
  ``True`` for the same input, ``find_adapter`` raises
  ``MultipleAdaptersMatch``. The error lists every offending registry
  key paired with its class name so the operator can locate every
  conflicting adapter, not just the first two.
* **An adapter's ``matches()`` exception surfaces with context.** If any
  adapter's ``matches()`` itself raises, ``find_adapter`` does not
  silently skip the adapter; it wraps the original exception in an
  ``AdapterMatchError`` naming the offending registry key and class.
  Silently skipping would let a broken adapter mask a correct one
  downstream — the same silent-ambiguity failure mode the multi-match
  policy guards against.

Registration also validates that ``key`` is a non-empty, non-whitespace
string. An empty key would live in the registry indistinguishable from
a real source-tool name and only surface on a later collision.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from oscal_pipeline.adapters.base import Adapter


class AdapterAlreadyRegistered(Exception):
    """Raised by ``register_adapter`` when ``key`` is already in the registry."""


class MultipleAdaptersMatch(Exception):
    """Raised by ``find_adapter`` when two or more adapters claim the same input."""


class AdapterMatchError(Exception):
    """Raised by ``find_adapter`` when an adapter's ``matches()`` itself raises.

    Wraps the original exception (accessible via ``__cause__``) with the
    offending adapter's registry key and class name so an operator can
    locate the broken adapter without instrumenting the dispatcher.
    """


# Module-level dict so test fixtures can snapshot / restore cleanly
# between tests. A class-based singleton would force every test to
# reach into class-level state — same blast radius, more ceremony.
# Values are *instances*, not classes: adapters are stateless per
# convention (the Protocol carries no constructor), so a single shared
# instance per registered adapter is correct and avoids re-instantiating
# on every dispatch call. The instance-vs-class contract gets revisited
# in issue #13 before issue #3's first concrete adapter lands.
REGISTRY: dict[str, Adapter] = {}

T = TypeVar("T", bound=type[Adapter])


def register_adapter(key: str) -> Callable[[T], T]:
    """Decorator: register an adapter class under ``key``.

    ``key`` must be a non-empty, non-whitespace string. It is the
    canonical source-tool name (e.g. ``"secret-scanner"``,
    ``"s3-audit"``) and matches the directory layout under
    ``oscal_pipeline/adapters/``. The decorated class is instantiated
    immediately and the *instance* is stored in the registry, so
    ``find_adapter`` does not have to instantiate on every dispatch.

    The decorator returns the class unchanged so the adapter module can
    use it directly (e.g. for tests) after decoration.
    """
    if not isinstance(key, str) or not key.strip():
        raise ValueError(
            f"adapter key must be a non-empty, non-whitespace string; got {key!r}"
        )

    def _decorator(cls: T) -> T:
        if key in REGISTRY:
            existing = type(REGISTRY[key]).__name__
            raise AdapterAlreadyRegistered(
                f"adapter already registered for key {key!r}: "
                f"{existing} (rejected: {cls.__name__})"
            )
        REGISTRY[key] = cls()
        return cls

    return _decorator


def find_adapter(raw: dict[str, object]) -> Adapter | None:
    """Return the adapter that claims ``raw``, or ``None`` if none claim it.

    Iterates *every* registered adapter (no short-circuit) so multi-match
    ambiguity surfaces every offender, not just the first two.
    ``matches()`` must return strict ``True`` (not a truthy non-bool) for
    the adapter to be considered a claimer — truthy non-bool returns
    are treated as silent bugs and ignored, so adapter intent stays
    explicit.

    Three failure modes are surfaced loudly:

    * No adapter claims ``raw`` → return ``None``.
    * Two or more adapters claim ``raw`` → raise
      ``MultipleAdaptersMatch`` listing every offending key + class name.
    * Any adapter's ``matches()`` itself raises → wrap in
      ``AdapterMatchError`` naming the offending key + class (so a
      broken adapter can't silently mask a correct one downstream).
    """
    matched: list[tuple[str, Adapter]] = []
    for key, adapter in REGISTRY.items():
        try:
            verdict = adapter.matches(raw)
        except Exception as exc:
            raise AdapterMatchError(
                f"adapter {key!r} ({type(adapter).__name__}) "
                f"raised {type(exc).__name__} during matches(): {exc}"
            ) from exc
        if verdict is True:
            matched.append((key, adapter))

    if not matched:
        return None
    if len(matched) > 1:
        offenders = ", ".join(
            f"{k!r} ({type(a).__name__})" for k, a in matched
        )
        raise MultipleAdaptersMatch(
            f"multiple adapters claim this input: {offenders}"
        )
    return matched[0][1]

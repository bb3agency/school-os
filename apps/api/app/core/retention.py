"""Per-school retention periods for the retention jobs (FR-ADM-002, docs/05 §13, docs/08 §7).

The school's owner or principal sets retention per data category within the bounds of
``app/admin/retention.yaml`` (``PUT /api/v1/admin/retention``). The jobs that delete working
data (imports' raw files, export files, read notifications) live in their own modules and must
not import ``app.admin`` (it reads from all of them), so the admin module registers a provider
here when it is imported (the API app and the worker both load it) and each job asks
:func:`days` inside the school's ``tenant_session``:

    keep = retention.days(session, "import_raw_files", default=config.raw_file_retention_days)

Without a provider (a process that did not load ``app.admin``), or when the school kept the
default, the job's own configured default applies: exactly the behaviour before FR-ADM-002.
A provider answers ``None`` for "no school setting"; it never raises for a missing setting.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

type Provider = Callable[[Session, str], int | None]


_lock = threading.Lock()
_providers: list[Provider] = []


def register_provider(provider: Provider) -> None:
    """Add ``provider`` once (idempotent; the admin module calls this at import)."""
    with _lock:
        if provider not in _providers:
            _providers.append(provider)


def unregister_provider(provider: Provider) -> None:
    """Remove ``provider`` (tests)."""
    with _lock:
        if provider in _providers:
            _providers.remove(provider)


def providers() -> tuple[Provider, ...]:
    with _lock:
        return tuple(_providers)


def days(session: Session, category: str, *, default: int) -> int:
    """The number of days this school keeps ``category`` (the first provider's answer, else
    ``default``). Call inside the school's ``tenant_session``."""
    if default < 1:
        raise ValueError("retention defaults are at least one day")
    for provider in providers():
        value = provider(session, category)
        if value is not None:
            return value
    return default


__all__ = [
    "Provider",
    "days",
    "providers",
    "register_provider",
    "unregister_provider",
]

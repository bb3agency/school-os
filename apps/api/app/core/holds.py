"""Legal holds seen from the retention and erasure jobs (FC-12; ADR-0059; FR-DPDP-043, FR-DPDP-044).

A school may place a legal hold on one student or on a whole record class (``app.privacy``,
``ops.legal_holds``). While a hold is active, nothing it covers is deleted by retention or
erasure. The one function that answers "what is held?" is ``app.privacy.service.hold_scope``.

The jobs that delete data live in modules that ``app.privacy`` itself depends on (documents,
exports, notifications, students, ...), so they cannot import ``app.privacy.service`` (an
import cycle). As with :mod:`app.core.retention`, the privacy service registers its function
here when it is imported (the API app and the worker both load it), and each job asks
:func:`check` inside the school's ``tenant_session`` before it deletes:

    held = holds.check(session, job="imports.purge_raw_files", record_class="imports")
    if held.whole_class:
        return 0                      # the whole class is held: delete nothing
    ...                               # else skip rows of held.student_ids

**Fail closed.** Without a registered provider (a process that did not load ``app.privacy``) a
job is told everything is held and deletes nothing that night; a warning is logged. A missed
night of retention is recoverable, a deleted record under a legal hold is not.

Every job that deletes by retention is listed in ``app/privacy/holds.yaml`` (``retention_jobs``)
and a test (``tests/privacy/test_holds_jobs.py``) proves each one consults this check.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HoldScope:
    """What a retention or erasure job must keep for one record class.

    ``whole_class``: a hold on the record class itself (or no provider: fail closed), so
    nothing of the class may be deleted. ``student_ids``: students under an active hold; rows
    about them are kept even when the class is not held as a whole.
    """

    whole_class: bool = False
    student_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def keeps_student(self, student_id: uuid.UUID | None) -> bool:
        """True when a row about ``student_id`` (``None``: not about one student) must stay."""
        if self.whole_class:
            return True
        return student_id is not None and student_id in self.student_ids

    def releasable(self, student_ids: Collection[uuid.UUID]) -> list[uuid.UUID]:
        """The given students whose rows may be deleted (none when the class is held)."""
        if self.whole_class:
            return []
        return [s for s in student_ids if s not in self.student_ids]

    @property
    def anything(self) -> bool:
        return self.whole_class or bool(self.student_ids)


NOTHING_HELD = HoldScope()
EVERYTHING_HELD = HoldScope(whole_class=True)

type Provider = Callable[[Session, str], HoldScope]

_lock = threading.Lock()
_provider: list[Provider] = []
# Every (job, record_class) asked in this process: the job-enumeration test reads it.
_asked: set[tuple[str, str]] = set()


def register_provider(provider: Provider) -> None:
    """Install the privacy module's hold check (idempotent; called at import)."""
    with _lock:
        _provider.clear()
        _provider.append(provider)


def unregister_provider() -> None:
    """Remove the provider (tests of the fail-closed path)."""
    with _lock:
        _provider.clear()


def provider() -> Provider | None:
    with _lock:
        return _provider[0] if _provider else None


def check(session: Session, *, job: str, record_class: str) -> HoldScope:
    """What ``job`` must keep of ``record_class`` in the current school. Call inside the
    school's ``tenant_session``, in the same transaction as the delete."""
    with _lock:
        _asked.add((job, record_class))
    found = provider()
    if found is None:
        log.warning("legal_hold.check_unavailable", job=job, record_class=record_class)
        return EVERYTHING_HELD
    scope = found(session, record_class)
    if scope.anything:
        log.info(
            "legal_hold.deletion_skipped",
            job=job,
            record_class=record_class,
            whole_class=scope.whole_class,
            count=len(scope.student_ids),
        )
    return scope


def asked() -> frozenset[tuple[str, str]]:
    """The (job, record_class) pairs checked so far in this process (tests)."""
    with _lock:
        return frozenset(_asked)


__all__ = [
    "EVERYTHING_HELD",
    "NOTHING_HELD",
    "HoldScope",
    "Provider",
    "asked",
    "check",
    "provider",
    "register_provider",
    "unregister_provider",
]

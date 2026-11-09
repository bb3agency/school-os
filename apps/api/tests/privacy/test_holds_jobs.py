"""Every retention and erasure path consults the legal hold check (FC-12; ADR-0059;
FR-DPDP-043, FR-DPDP-044, FR-DPDP-050). No database.

- Every Celery task that deletes by name (purge, retention, erase, expire, sweep) and every
  ``enforced_by`` job of ``app/admin/retention.yaml`` is listed in ``app/privacy/holds.yaml``
  either as a ``retention_jobs`` entry or as an ``exempt_jobs`` entry with a reason.
- Every ``retention_jobs`` entry names a real record class and is asked in the source through
  ``holds.check(..., job="<name>", record_class="<class>")``.
- ``app.core.holds.check`` fails closed without a provider; ``app.privacy.service`` registers
  ``hold_scope`` as the provider.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core import holds
from app.privacy import config as privacy_config
from app.privacy.holds_config import load_config

APP = Path(__file__).resolve().parents[2] / "app"
DELETES = re.compile(r"purge|retention|erase|expire|sweep_discarded|narration")
CALL = re.compile(
    r"holds\.check\(\s*\w+,\s*job=(?:\"(?P<job>[a-z0-9_.]+)\"|(?P<var>\w+\[0\]))"
    r",\s*record_class=(?:\"(?P<cls>[a-z0-9_]+)\"|\w+\[1\])",
    re.S,
)


def _registered_tasks() -> set[str]:
    from sos_worker.celery_app import celery_app

    celery_app.loader.import_default_modules()
    return {name for name in celery_app.tasks if not name.startswith("celery.")}


def _enforced_by() -> set[str]:
    raw: dict[str, Any] = yaml.safe_load((APP / "admin" / "retention.yaml").read_text("utf-8"))
    return {c["enforced_by"] for c in raw["categories"].values() if c.get("enforced_by")}


def _covered(name: str) -> bool:
    cfg = load_config()
    if name in cfg.exempt_jobs:
        return True
    return any(job == name or job.startswith(f"{name}.") for job in cfg.retention_jobs)


# Celery tasks whose names do not say "purge" but run a retention step (task -> catalogued job).
RUNNERS = {
    "knowledge.tidy_conversations": "knowledge.purge_memories",
    "fees.payments_daily": "fees.purge_payment_events",
    "fees.upi_morning": "fees.erase_upi_narrations",
    "tally.check_silent_agents": "tally.purge_old_syncs",
}


def test_FR_DPDP_050_every_deleting_job_is_catalogued() -> None:
    tasks = _registered_tasks()
    for task, job in RUNNERS.items():
        assert task in tasks, task
        assert _covered(job), job
    names = {n for n in tasks if DELETES.search(n)} | _enforced_by()
    missing = sorted(n for n in names if not _covered(n))
    assert not missing, (
        "add these jobs to app/privacy/holds.yaml retention_jobs (and call "
        f"app.core.holds.check before deleting) or to exempt_jobs with a reason: {missing}"
    )


def test_FR_DPDP_043_every_retention_job_asks_the_hold_check() -> None:
    cfg = load_config()
    classes = privacy_config.record_classes().classes
    source = "\n".join(p.read_text("utf-8") for p in APP.rglob("*.py"))
    asked: dict[str, set[str]] = {}
    for m in CALL.finditer(source):
        if m.group("job"):
            asked.setdefault(m.group("job"), set()).add(m.group("cls"))
    # The messaging purge looks its pairs up in a table (_HELD_BY): read the table too.
    for job, cls in re.findall(r'\("(messaging\.[a-z_.]+)", "([a-z_]+)"\)', source):
        asked.setdefault(job, set()).add(cls)
    for job, spec in cfg.retention_jobs.items():
        assert spec.record_class in classes, (job, spec.record_class)
        assert classes[spec.record_class].erasure != "no_personal_data", job
        assert job in asked, f"{job} never calls app.core.holds.check"
        assert spec.record_class in asked[job], (job, asked[job])


def test_FR_DPDP_044_the_check_fails_closed_without_a_provider() -> None:
    import app.privacy.service as privacy

    assert holds.provider() is privacy.hold_scope
    try:
        holds.unregister_provider()
        scope = holds.check(object(), job="test.job", record_class="exports")  # type: ignore[arg-type]
        assert scope.whole_class
        assert scope.keeps_student(None)
    finally:
        holds.register_provider(privacy.hold_scope)
    assert ("test.job", "exports") in holds.asked()


def test_FR_DPDP_043_hold_scope_semantics() -> None:
    held, free = uuid.uuid4(), uuid.uuid4()
    scope = holds.HoldScope(student_ids=frozenset({held}))
    assert scope.keeps_student(held)
    assert not scope.keeps_student(free)
    assert not scope.keeps_student(None)
    assert scope.releasable([held, free]) == [free]
    assert holds.EVERYTHING_HELD.releasable([free]) == []
    assert not holds.NOTHING_HELD.anything


@pytest.mark.parametrize("code", sorted(load_config().reason_codes))
def test_FR_DPDP_040_reason_codes_are_codes(code: str) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]{1,62}", code)
    assert load_config().review.max_months == 12
    assert load_config().verified is False

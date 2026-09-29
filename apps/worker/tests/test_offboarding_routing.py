"""Offboarding runs in the worker (FR-PLT-005; docs/16 §5.5; ADR-0029): the deletion job on
``maintenance``, certificates on the Chromium ``pdf`` workers, both on the shared tier only, and
every module that owns school data is registered with the purge in the worker process."""

from __future__ import annotations

from app.core.config import DeploymentMode, Settings
from app.platform.tasks import beat_schedule
from app.tenancy import offboarding
from sos_worker.celery_app import celery_app

ROUTES = {"offboarding.process": "maintenance", "offboarding.certify": "pdf"}


def test_FR_PLT_005_offboarding_tasks_registered_and_routed() -> None:
    celery_app.loader.import_default_modules()
    for task, queue in ROUTES.items():
        assert task in celery_app.tasks
        # beat and send_task honour task_routes, not the task's own queue.
        assert celery_app.amqp.router.route({}, task)["queue"].name == queue


def test_ADR_0017_offboarding_scheduled_only_on_the_shared_tier() -> None:
    shared = beat_schedule(Settings(deployment_mode=DeploymentMode.SHARED))
    dedicated = beat_schedule(Settings(deployment_mode=DeploymentMode.DEDICATED))
    for task, queue in ROUTES.items():
        entries = [v for v in shared.values() if v["task"] == task]
        assert len(entries) == 1
        assert entries[0]["options"] == {"queue": queue}
        assert task not in {v["task"] for v in dedicated.values()}


def test_ADR_0029_every_purge_owner_is_registered_in_the_worker() -> None:
    celery_app.loader.import_default_modules()
    cfg = offboarding.config()
    assert set(offboarding.DATA_OWNERS) == set(cfg.purge_order)
    assert set(offboarding.OBJECT_OWNERS) == set(cfg.object_owners)

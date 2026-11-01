"""AI answer quota alerts reach schools through an hourly school-side pull (ADR-0038 follow-up,
docs/16 §19 Q16; FR-PLT-020): a registered task on the consumed ``maintenance`` queue, scheduled
on the shared tier only (the control plane runs there; ADR-0017)."""

from __future__ import annotations

from app.core.config import DeploymentMode, Settings
from app.notifications.quota_sync import beat_schedule
from sos_worker.celery_app import QUEUES, celery_app

TASK = "notifications.ai_quota_sweep"


def test_ADR_0038_ai_quota_sweep_registered_on_a_consumed_queue() -> None:
    celery_app.loader.import_default_modules()
    assert TASK in celery_app.tasks
    queue = celery_app.amqp.router.route({}, TASK)["queue"].name
    assert queue == "maintenance"
    assert queue in QUEUES


def test_ADR_0038_ai_quota_sweep_hourly_on_the_shared_tier_only() -> None:
    shared = beat_schedule(Settings(deployment_mode=DeploymentMode.SHARED))
    dedicated = beat_schedule(Settings(deployment_mode=DeploymentMode.DEDICATED))
    (entry,) = [v for v in shared.values() if v["task"] == TASK]
    assert entry["schedule"] == 3600.0
    assert entry["options"] == {"queue": "maintenance"}
    assert dedicated == {}

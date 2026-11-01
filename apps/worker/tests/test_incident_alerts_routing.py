"""Incident clock alerts run in the worker (lead decision D49 part 2; ADR-0050; FR-DPDP-031,
SEC-052): a registered task on the consumed ``maintenance`` queue, every 5 minutes, on the shared
tier only (the control plane runs there; ADR-0017)."""

from __future__ import annotations

from app.core.config import DeploymentMode, Settings
from app.platform.tasks import beat_schedule
from sos_worker.celery_app import QUEUES, celery_app

TASK = "platform.incident_clock_alerts"


def test_D49_FR_DPDP_031_incident_clock_alerts_registered_on_a_consumed_queue() -> None:
    celery_app.loader.import_default_modules()
    assert TASK in celery_app.tasks
    # beat and send_task honour task_routes, not the task's own queue.
    queue = celery_app.amqp.router.route({}, TASK)["queue"].name
    assert queue == "maintenance"
    assert queue in QUEUES


def test_D49_SEC_052_incident_clock_alerts_every_5_minutes_on_the_shared_tier_only() -> None:
    shared = beat_schedule(Settings(deployment_mode=DeploymentMode.SHARED))
    dedicated = beat_schedule(Settings(deployment_mode=DeploymentMode.DEDICATED))
    (entry,) = [v for v in shared.values() if v["task"] == TASK]
    assert entry["schedule"] == 300.0
    assert entry["options"] == {"queue": "maintenance"}
    assert TASK not in {v["task"] for v in dedicated.values()}

"""Invoice PDFs render on the Chromium workers (docs/16 §5.8, docs/04 §6 queue ``pdf``;
ADR-0025; FR-PLT-017): the task is registered, routed to ``pdf`` and scheduled by beat on the
shared tier only (dedicated hosts never invoice; ADR-0017)."""

from __future__ import annotations

from app.core.config import DeploymentMode, Settings
from app.platform.tasks import beat_schedule
from sos_worker.celery_app import celery_app

TASK = "billing.render_invoice_pdfs"


def test_FR_PLT_017_invoice_pdf_task_registered_and_routed_to_pdf() -> None:
    celery_app.loader.import_default_modules()
    assert TASK in celery_app.tasks
    # beat and send_task honour task_routes, not the task's own queue.
    assert celery_app.amqp.router.route({}, TASK)["queue"].name == "pdf"


def test_ADR_0017_invoice_pdf_sweep_scheduled_only_on_the_shared_tier() -> None:
    shared = beat_schedule(Settings(deployment_mode=DeploymentMode.SHARED))
    dedicated = beat_schedule(Settings(deployment_mode=DeploymentMode.DEDICATED))
    entries = [v for v in shared.values() if v["task"] == TASK]
    assert len(entries) == 1
    assert entries[0]["options"] == {"queue": "pdf"}
    assert TASK not in {v["task"] for v in dedicated.values()}

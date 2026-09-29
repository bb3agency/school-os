"""The certificate PDF task is registered and routed to the Chromium workers (FR-CERT-010;
docs/04 §6: anything with a PDF runs on "pdf")."""

from __future__ import annotations

from app.ops import service as ops
from sos_worker.celery_app import celery_app


def test_FR_CERT_010_render_task_registered_and_routed_to_pdf() -> None:
    celery_app.loader.import_default_modules()
    assert "certificates.render" in celery_app.tasks
    # send_task (used by the outbox dispatcher) honours task_routes, not the task's own queue.
    assert celery_app.amqp.router.route({}, "certificates.render")["queue"].name == "pdf"


def test_FR_CERT_010_outbox_event_reaches_the_render_task() -> None:
    celery_app.loader.import_default_modules()
    assert ops.OUTBOX_ROUTES["certificate.render_requested"] == "certificates.render"

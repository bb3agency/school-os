"""The tuition fee certificate PDF task is registered and routed to the Chromium workers (FM-06;
FR-FEE-053; docs/04 §6: anything with a PDF runs on "pdf")."""

from __future__ import annotations

from app.ops import service as ops
from sos_worker.celery_app import celery_app


def test_FR_FEE_053_render_task_registered_and_routed_to_pdf() -> None:
    celery_app.loader.import_default_modules()
    assert "fees.render_tax_certificate" in celery_app.tasks
    assert celery_app.amqp.router.route({}, "fees.render_tax_certificate")["queue"].name == "pdf"


def test_FR_FEE_053_outbox_event_reaches_the_render_task() -> None:
    celery_app.loader.import_default_modules()
    assert ops.OUTBOX_ROUTES["fee.tax_certificate.issued"] == "fees.render_tax_certificate"

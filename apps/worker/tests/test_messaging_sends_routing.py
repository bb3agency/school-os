"""WhatsApp sending jobs are registered, routed to "maintenance" and scheduled; confirmed
broadcasts, automatic runs and issued receipts reach the sender through the outbox (R2-3G;
FR-MSG-011..016)."""

from __future__ import annotations

from app.ops import service as ops
from sos_worker.celery_app import celery_app

TASKS = (
    "messaging.send_due",
    "messaging.receipt_message",
    "messaging.notice_withdrawn",
    "messaging.send_sweep",
    "messaging.automatic_runs",
)


def test_FR_MSG_016_sending_tasks_registered_and_routed_to_maintenance() -> None:
    celery_app.loader.import_default_modules()
    for name in TASKS:
        assert name in celery_app.tasks, name
        assert celery_app.amqp.router.route({}, name)["queue"].name == "maintenance", name


def test_FR_MSG_015_outbox_routes() -> None:
    celery_app.loader.import_default_modules()
    assert ops.OUTBOX_ROUTES["messaging.send"] == "messaging.send_due"
    assert ops.OUTBOX_ROUTES["fee.receipt.message"] == "messaging.receipt_message"
    assert ops.OUTBOX_ROUTES["fee.receipt.issued"] == "fees.render_receipt"
    # R2-3J (FR-NOTICE-009): a withdrawn notice's queued messages are cancelled by the sender.
    assert ops.OUTBOX_ROUTES["notice.withdrawn"] == "messaging.notice_withdrawn"


def test_FR_MSG_011_sender_and_automatic_runs_are_scheduled() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["messaging-send-sweep"]["task"] == "messaging.send_sweep"
    assert schedule["messaging-send-sweep"]["schedule"] == 60.0
    assert schedule["messaging-automatic-runs"]["task"] == "messaging.automatic_runs"

"""DEK re-encryption is registered on the maintenance queue and consumes its outbox events
(SEC-012; docs/04 §6, 07 §8)."""

from __future__ import annotations

from app.ops import service as ops
from sos_worker.celery_app import TASK_MODULES, celery_app


def test_SEC_012_reencrypt_task_registered_routed_and_wired() -> None:
    celery_app.loader.import_default_modules()
    assert "app.students.tasks" in TASK_MODULES
    assert "maintenance.reencrypt_tenant" in celery_app.tasks
    assert celery_app.amqp.router.route({}, "maintenance.reencrypt_tenant")["queue"].name == (
        "maintenance"
    )
    assert ops.OUTBOX_ROUTES["keys.rotated"] == "maintenance.reencrypt_tenant"
    assert ops.OUTBOX_ROUTES["keys.reencrypt_requested"] == "maintenance.reencrypt_tenant"

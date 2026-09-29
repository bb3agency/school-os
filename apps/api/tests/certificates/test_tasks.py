"""Certificate PDF task: IDs-only arguments, retries, and giving up marks the PDF failed
(FR-CERT-010; docs/04 §6 task contract)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.certificates import service, tasks
from app.core import pdf

C = sys.modules["sos_test_certificates_support"]


def _payload(certificate_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant_id or uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "payload": {"certificate_id": str(certificate_id), "user_id": str(uuid.uuid4())},
    }


def test_FR_CERT_010_failures_retry_then_mark_the_pdf_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[uuid.UUID] = []
    failed: list[tuple[uuid.UUID, str]] = []

    def boom(tenant_id: uuid.UUID, certificate_id: uuid.UUID, user_id: uuid.UUID) -> str:
        attempts.append(certificate_id)
        raise RuntimeError("renderer down")

    def give_up(tenant_id: uuid.UUID, certificate_id: uuid.UUID, code: str) -> None:
        failed.append((certificate_id, code))

    monkeypatch.setattr(service, "render_pdf", boom)
    monkeypatch.setattr(service, "mark_pdf_failed", give_up)
    certificate_id = uuid.uuid4()
    result = tasks.render.apply(kwargs=_payload(certificate_id), retries=0)
    assert result.failed()
    assert len(attempts) == tasks.MAX_RETRIES + 1
    assert failed == [(certificate_id, "render_failed")]


@pytest.mark.db
def test_FR_CERT_010_task_renders_once(school: Any, admin_engine: Engine) -> None:
    cert = C.issue(school, C.student(school), "bonafide")
    renderer = C.FakeRenderer()
    pdf.set_renderer(renderer)
    try:
        payload = _payload(cert.id, school.tenant_id)
        assert tasks.render.apply(kwargs=payload).get() == "ready"
        # Delivered twice (at-least-once): nothing happens the second time.
        assert tasks.render.apply(kwargs=payload).get() == "ready"
    finally:
        pdf.set_renderer(None)
    assert len(renderer.pages) == 1
    assert C.row(admin_engine, cert.id)["pdf_status"] == "ready"

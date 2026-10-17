"""Helpers for the WhatsApp messaging tests (R2-1C; synthetic data only, invariant 11).

Loaded by path from tests/messaging and the security suites. Schools come from the shared
synthetic world (tests/api/world.py); students and guardians are created through the real
student service (tests/students/student_world.py) with synthetic mobile numbers (90xxxxxxxx).
Webhook bodies are synthetic Meta-shaped fixtures signed with the synthetic app secret.
"""

from __future__ import annotations

import importlib.util
import json
import secrets
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.messaging import runtime, service
from app.messaging.providers.fake import STATE
from app.messaging.schemas import ManualConnectIn
from app.messaging.webhook import signature

TESTS = Path(__file__).resolve().parents[1]
WEBHOOK = "/api/v1/webhooks/meta"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")


def digits(n: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


def synthetic_mobile() -> str:
    return "90" + digits(8)


def owner_ctx(school: Any) -> Any:
    return SW.ctx_for(school.tenant_id, school.people["owner"], "owner")


def live_account(school: Any) -> Any:
    with tenant_session(school.tenant_id) as db:
        from app.messaging import repository as repo

        return repo.live_account(db)


def connect(school: Any) -> tuple[uuid.UUID, str]:
    """Connect the school (manual token, fake provider) unless it is connected; templates
    created and approved. Returns (account id, WABA id)."""
    SW.configure_keyring()
    existing = live_account(school)
    if existing is not None:
        return existing.id, existing.waba_id
    waba, number = "1" + digits(14), "2" + digits(14)
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as db:
        out = service.connect_manual(
            db,
            owner_ctx(school),
            ManualConnectIn(
                token=SecretStr("synthetic-system-user-token-" + digits(12)),
                waba_id=waba,
                phone_number_id=number,
            ),
        )
    assert out.id is not None
    service.provision_templates(school.tenant_id, out.id)
    service.sync_templates(school.tenant_id)
    return out.id, waba


def disconnect(school: Any) -> None:
    owner = school.people["owner"]
    if live_account(school) is None:
        return
    with tenant_session(school.tenant_id, owner.user_id) as db:
        service.disconnect(db, owner_ctx(school), expected_version=None)


def guardian(
    school: Any, *, section_key: str = "section_9a", phone: str | None = "new"
) -> tuple[uuid.UUID, uuid.UUID, str | None]:
    """A fresh synthetic student with one guardian. Returns (student, guardian, mobile)."""
    SW.configure_keyring()
    student = SW.create(
        school,
        name=f"Synthetica Messaging {uuid.uuid4().hex[:6]}",
        section_key=section_key,
        admission_no=f"MS/{uuid.uuid4().hex[:8]}",
    )
    mobile = synthetic_mobile() if phone == "new" else phone
    fields: dict[str, Any] = {"full_name": "Synthetic Parent"}
    if mobile is not None:
        fields["phone"] = mobile
    guardian_id: uuid.UUID = SW.add_guardian(school, student, **fields)
    return student, guardian_id, mobile


def opt_in(api: Any, who: Any, guardian_id: uuid.UUID, category: str = "notices", **kw: Any) -> Any:
    body = {
        "category": category,
        "status": "opted_in",
        "method": "in_person",
        "decided_on": time.strftime("%Y-%m-%d"),
        **kw,
    }
    return api.call(who, "POST", f"/api/v1/guardians/{guardian_id}/optins", json=body)


# --- Meta-shaped synthetic webhook fixtures -----------------------------------------------------


def envelope(waba: str, field: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": waba, "changes": [{"field": field, "value": value}]}],
    }


def inbound(
    waba: str, wa_id: str, *, text_body: str | None = None, button: str | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "from": wa_id,
        "id": "wamid.IN" + digits(16),
        "timestamp": str(int(time.time())),
    }
    if button is not None:
        message.update(type="button", button={"payload": button, "text": "Stop messages"})
    else:
        message.update(type="text", text={"body": text_body or ""})
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "919000000000", "phone_number_id": "1"},
        "contacts": [{"profile": {"name": "Synthetic Parent"}, "wa_id": wa_id}],
        "messages": [message],
    }
    return envelope(waba, "messages", value)


def status(
    waba: str, wamid: str, state: str, *, ts: int | None = None, code: int | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": wamid,
        "status": state,
        "timestamp": str(ts if ts is not None else int(time.time())),
        "recipient_id": "919000000000",
        "pricing": {"billable": True, "category": "utility"},
    }
    if code is not None:
        item["errors"] = [{"code": code, "title": "Synthetic error"}]
    return envelope(waba, "messages", {"messaging_product": "whatsapp", "statuses": [item]})


def secret() -> bytes:
    value = runtime.settings().meta_app_secret
    assert value is not None
    return value.get_secret_value().encode()


def post(
    client: TestClient,
    payload: dict[str, Any] | bytes,
    *,
    sign_with: bytes | None = None,
    header: str | None = None,
) -> httpx.Response:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    sig = header if header is not None else signature(sign_with or secret(), body)
    response: httpx.Response = client.post(
        WEBHOOK,
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )
    return response


def deliver_webhook(api: Any, school: Any, payload: dict[str, Any]) -> dict[str, int]:
    """Post a signed delivery and apply the inbox like the worker does."""
    response = post(api.client, payload)
    assert response.status_code == 200, response.text
    return service.process_inbox(school.tenant_id)


def rows(admin: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params)]


def fake_sent() -> list[dict[str, Any]]:
    return list(STATE.sent)


__all__ = [
    "connect",
    "deliver_webhook",
    "disconnect",
    "envelope",
    "fake_sent",
    "guardian",
    "inbound",
    "live_account",
    "opt_in",
    "owner_ctx",
    "post",
    "rows",
    "secret",
    "status",
    "synthetic_mobile",
]

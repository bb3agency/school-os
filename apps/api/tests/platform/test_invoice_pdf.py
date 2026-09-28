"""Invoice PDFs end to end: render sweep, storage, idempotency, download route, audit, BOLA
(docs/16 §5.8; FR-PLT-016, FR-PLT-017, FR-PLT-028, FR-PLT-029, SEC-027; ADR-0017, ADR-0025).

The renderer is a fake (records the HTML, returns PDF bytes) and the store is in memory; the
real Chromium render and text extraction are in ``test_invoice_pdf_template.py``. Invoices are
issued in far-future financial years so numbering never collides with other tests. All data is
synthetic.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core import pdf as core_pdf
from app.core.config import Environment, Settings
from app.core.db import platform_session
from app.platform import billing, invoice_files
from app.platform import repository as platform_repo
from app.platform.invoice_storage import MemoryInvoiceStore, set_invoice_store

from .conftest import Api, MakeOperator, Operator, billing_account_payload, provision_payload

pytestmark = pytest.mark.db

PDF = b"%PDF-1.7\n% synthetic invoice render\n"


class FakeRenderer:
    def __init__(self, hook: Callable[[], object] | None = None) -> None:
        self.pages: list[str] = []
        self.hook = hook

    def render(self, html: str) -> bytes:
        self.pages.append(html)
        if self.hook is not None:
            hook, self.hook = self.hook, None
            hook()
        return PDF


@pytest.fixture
def store() -> Iterator[MemoryInvoiceStore]:
    mem = MemoryInvoiceStore()
    set_invoice_store(mem)
    try:
        yield mem
    finally:
        set_invoice_store(None)


@pytest.fixture
def renderer() -> Iterator[FakeRenderer]:
    fake = FakeRenderer()
    core_pdf.set_renderer(fake)
    try:
        yield fake
    finally:
        core_pdf.set_renderer(None)


def _issued(
    api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID], *, year: int
) -> tuple[uuid.UUID, str]:
    body = provision_payload(make_plan(base_price_inr="5000.00"), start_as="active")
    body["billing_account"] = billing_account_payload(state_code="37")
    res = api.call("POST", "/tenants", owner, json=body)
    assert res.status_code == 201, res.text
    sub = uuid.UUID(res.json()["subscription_id"])
    draft = billing.create_manual_draft(owner.actor, sub, dt.date(year, 1, 1))
    out = billing.issue_invoice(owner.actor, draft.id, today=dt.date(year, 5, 2))
    assert out.invoice_number
    return out.id, out.invoice_number


def _draft(api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]) -> uuid.UUID:
    res = api.call(
        "POST", "/tenants", owner, json=provision_payload(make_plan(), start_as="active")
    )
    sub = uuid.UUID(res.json()["subscription_id"])
    return billing.create_manual_draft(owner.actor, sub, dt.date(2069, 1, 1)).id


def _pdf_row(invoice_id: uuid.UUID) -> dict[str, Any] | None:
    with platform_session() as s:
        row = (
            s.execute(
                text("SELECT * FROM platform.invoice_pdfs WHERE invoice_id = :i"),
                {"i": invoice_id},
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row else None


def _events(action: str, invoice_id: uuid.UUID) -> list[dict[str, Any]]:
    with platform_session() as s:
        return [
            dict(r)
            for r in s.execute(
                text(
                    "SELECT actor_type, actor_id, summary, subject_tenant_id "
                    "FROM platform.audit_events WHERE action = :a AND resource_id = :i"
                ),
                {"a": action, "i": invoice_id},
            ).mappings()
        ]


@pytest.fixture
def billing_admin(make_operator: MakeOperator) -> Operator:
    return make_operator("billing_admin")


# --- render -----------------------------------------------------------------------------------


def test_FR_PLT_017_issued_invoice_is_rendered_stored_and_audited_once(
    *,
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
    renderer: FakeRenderer,
) -> None:
    invoice_id, number = _issued(api, owner, make_plan, year=2091)
    assert invoice_files.render_invoice_pdf(invoice_id) == "rendered"
    row = _pdf_row(invoice_id)
    assert row is not None
    assert row["template_version"] == "v0"
    assert row["object_key"].startswith(f"platform/invoices/2091-92/{invoice_id}/")
    assert not row["object_key"].startswith("t/")
    assert store.objects == {row["object_key"]: PDF}
    assert row["size_bytes"] == len(PDF)
    assert len(row["sha256"]) == 64
    page = renderer.pages[0]
    assert number in page
    assert "GSTIN: 37ABCDE1234F1Z5" in page
    assert "5,900.00" in page
    events = _events("invoice.pdf_rendered", invoice_id)
    assert len(events) == 1
    assert events[0]["actor_type"] == "system"
    assert events[0]["summary"] == {
        "invoice_number": number,
        "template_version": "v0",
        "size_bytes": len(PDF),
    }
    # Idempotent: a second render (and the sweep) finds the stored PDF and renders nothing.
    assert invoice_files.render_invoice_pdf(invoice_id) == "exists"
    # (Other tests' invoices may be pending too; only this invoice's pages and objects count.)
    invoice_files.render_pending(limit=10_000)
    assert len([p for p in renderer.pages if number in p]) == 1
    assert len(_events("invoice.pdf_rendered", invoice_id)) == 1
    assert len([k for k in store.objects if f"/{invoice_id}/" in k]) == 1


def test_FR_PLT_016_concurrent_render_keeps_exactly_one_document(
    *,
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
) -> None:
    """A render that loses the insert (another render stored first) deletes its own object."""
    invoice_id, _ = _issued(api, owner, make_plan, year=2092)
    inner = FakeRenderer()
    outer = FakeRenderer(
        hook=lambda: invoice_files.render_invoice_pdf(invoice_id, renderer=inner, store=store)
    )
    assert invoice_files.render_invoice_pdf(invoice_id, renderer=outer, store=store) == "exists"
    row = _pdf_row(invoice_id)
    assert row is not None
    assert list(store.objects) == [row["object_key"]]
    assert len(store.deleted) == 1
    assert store.deleted[0] != row["object_key"]
    assert len(_events("invoice.pdf_rendered", invoice_id)) == 1


def test_FR_PLT_016_drafts_are_never_rendered(
    *,
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
    renderer: FakeRenderer,
) -> None:
    draft = _draft(api, owner, make_plan)
    assert invoice_files.render_invoice_pdf(draft) == "not_numbered"
    assert invoice_files.render_invoice_pdf(uuid.uuid4()) == "not_found"
    with platform_session() as s:
        pending = platform_repo.invoices_without_pdf(s, 10_000)
    assert draft not in pending
    assert renderer.pages == []
    assert store.objects == {}


def test_FR_PLT_017_sweep_renders_pending_invoices_and_survives_a_failure(
    *,
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
    renderer: FakeRenderer,
) -> None:
    first, _ = _issued(api, owner, make_plan, year=2093)
    second, _ = _issued(api, owner, make_plan, year=2094)

    class Broken:
        def render(self, html: str) -> bytes:
            raise core_pdf.RenderError("pdf_render_failed")

    failed = invoice_files.render_pending(limit=10_000, renderer=Broken())
    assert failed.failed >= 2
    assert failed.rendered == 0
    assert _pdf_row(first) is None
    assert store.objects == {}
    done = invoice_files.render_pending(limit=10_000)
    assert done.rendered >= 2
    assert done.failed == 0
    assert _pdf_row(first) is not None
    assert _pdf_row(second) is not None


def test_FR_PLT_017_placeholder_supplier_address_refused_in_prod(
    *,
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
    renderer: FakeRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice_id, _ = _issued(api, owner, make_plan, year=2095)
    prod = Settings.model_construct(env=Environment.PROD)
    monkeypatch.setattr(invoice_files, "get_settings", lambda: prod)
    with pytest.raises(invoice_files.SupplierDetailsMissing):
        invoice_files.render_invoice_pdf(invoice_id)
    assert renderer.pages == []
    assert _pdf_row(invoice_id) is None


def test_FR_PLT_016_stored_invoice_pdfs_are_append_only(
    *,
    api: Api,
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
    renderer: FakeRenderer,
) -> None:
    invoice_id, _ = _issued(api, owner, make_plan, year=2096)
    invoice_files.render_invoice_pdf(invoice_id)
    for stmt in (
        "UPDATE platform.invoice_pdfs SET size_bytes = 1 WHERE invoice_id = :i",
        "DELETE FROM platform.invoice_pdfs WHERE invoice_id = :i",
    ):
        with pytest.raises(DBAPIError, match="permission denied"), platform_session() as s:
            s.execute(text(stmt), {"i": invoice_id})
    assert _pdf_row(invoice_id) is not None


# --- download route ---------------------------------------------------------------------------


def test_FR_PLT_029_download_url_is_presigned_short_lived_and_audited(
    *,
    api: Api,
    owner: Operator,
    billing_admin: Operator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
    renderer: FakeRenderer,
) -> None:
    invoice_id, number = _issued(api, owner, make_plan, year=2097)
    pending = api.call("GET", f"/invoices/{invoice_id}/download-url", billing_admin)
    assert (pending.status_code, pending.json()["code"]) == (409, "invoice_pdf_pending")
    invoice_files.render_pending(limit=10_000)
    res = api.call("GET", f"/invoices/{invoice_id}/download-url", billing_admin)
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store"
    body = res.json()
    row = _pdf_row(invoice_id)
    assert row is not None
    assert row["object_key"] in body["url"]
    assert body["filename"] == "invoice-" + number.replace("/", "-") + ".pdf"
    assert body["content_type"] == "application/pdf"
    assert (body["template_version"], body["size_bytes"], body["sha256"]) == (
        "v0",
        len(PDF),
        row["sha256"],
    )
    expires = dt.datetime.fromisoformat(body["expires_at"])
    assert expires <= dt.datetime.now(dt.UTC) + dt.timedelta(seconds=301)
    events = _events("invoice.pdf_downloaded", invoice_id)
    assert len(events) == 1
    assert events[0]["actor_id"] == billing_admin.id
    assert events[0]["summary"] == {"invoice_number": number, "template_version": "v0"}


def test_FR_PLT_028_download_url_permissions_and_object_checks(
    *,
    api: Api,
    owner: Operator,
    make_operator: MakeOperator,
    make_plan: Callable[..., uuid.UUID],
    store: MemoryInvoiceStore,
    renderer: FakeRenderer,
) -> None:
    invoice_id, _ = _issued(api, owner, make_plan, year=2098)
    invoice_files.render_pending(limit=10_000)
    path = f"/invoices/{invoice_id}/download-url"
    # docs/16 §6: platform.invoices.read = platform_owner, billing_admin, platform_viewer.
    for role, status in (
        ("platform_owner", 200),
        ("billing_admin", 200),
        ("platform_viewer", 200),
        ("platform_engineer", 403),
        ("support_agent", 403),
    ):
        res = api.call("GET", path, make_operator(role))
        assert res.status_code == status, f"{role}: {res.text}"
    # A school's staff token never reaches the control plane.
    staff = api.client.get(
        "/api/v1/platform" + path, headers=api.headers(None, tenant_subject="staff-sub")
    )
    assert staff.status_code == 401
    # Unknown ids are 404; drafts have no document.
    viewer = make_operator("platform_viewer")
    assert api.call("GET", f"/invoices/{uuid.uuid4()}/download-url", viewer).status_code == 404
    draft = _draft(api, owner, make_plan)
    res = api.call("GET", f"/invoices/{draft}/download-url", viewer)
    assert (res.status_code, res.json()["code"]) == (409, "invoice_draft")
    # Denied and refused calls are not recorded as downloads.
    assert len(_events("invoice.pdf_downloaded", invoice_id)) == 3

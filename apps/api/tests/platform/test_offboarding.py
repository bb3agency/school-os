"""Offboarding after the two-person approval, end to end through the control plane
(FR-PLT-005, US-1303 AC3, SEC-029; docs/16 §5.5; ADR-0029).

Shared tier: approval creates the run with its 30-day deadline; nothing is deleted before the
export is confirmed; the beat job deletes every row and file, verifies, crypto-shreds and the pdf
job issues the certificate and sets the school ``deleted``; a failure is recorded as a code and
retried; a crashed runner's lease expires and the run resumes; deadlines alert once. Dedicated
tier: operators confirm the KMS deletion and host teardown. The certificate holds no personal
data and its download is audited. The control plane never touches a tenant table itself.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.core.errors import Conflict
from app.platform import offboarding
from app.platform.deletion_certificate import content_sha256
from app.platform.invoice_storage import MemoryInvoiceStore, set_invoice_store
from app.tenancy import service as tenancy

from .conftest import Api, MakeOperator, Operator, provision_payload

pytestmark = pytest.mark.db

PDF = b"%PDF-1.7\n% synthetic certificate render\n"
# The chain and its stored verification (R-19) stay until the audit retention ends.
RETAINED = {"audit.events", "audit.chain_heads", "audit.chain_verifications"}


def _load(name: str, rel: str) -> ModuleType:
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / rel
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


PS = _load("sos_test_purge_support", "tenancy/purge_support.py")


class FakeRenderer:
    def __init__(self) -> None:
        self.pages: list[str] = []

    def render(self, html: str) -> bytes:
        self.pages.append(html)
        return PDF


@pytest.fixture
def cert_store() -> Iterator[MemoryInvoiceStore]:
    mem = MemoryInvoiceStore()
    set_invoice_store(mem)
    yield mem
    set_invoice_store(None)


@pytest.fixture
def files() -> Iterator[Any]:
    from app.documents import storage

    mem = _load("sos_test_documents_support_platform", "documents/support.py").MemoryStore()
    storage.set_object_store(mem)
    yield mem
    storage.set_object_store(None)


@pytest.fixture
def owners(make_operator: MakeOperator) -> tuple[Operator, Operator]:
    return make_operator("platform_owner"), make_operator("platform_owner")


def _offboard(api: Api, first: Operator, second: Operator, tid: str) -> None:
    req = api.call(
        "POST", f"/tenants/{tid}/offboarding", first, json={"reason": "School's written request"}
    )
    assert req.status_code == 202, req.text
    ok = api.call("POST", f"/tenants/{tid}/offboarding:approve", second)
    assert ok.status_code == 200, ok.text


@pytest.fixture
def school(
    api: Api,
    owners: tuple[Operator, Operator],
    make_plan: Callable[..., uuid.UUID],
    admin_engine: Engine,
    files: Any,
) -> uuid.UUID:
    first, _second = owners
    res = api.call("POST", "/tenants", first, json=provision_payload(make_plan()))
    assert res.status_code == 201, res.text
    tid = uuid.UUID(res.json()["tenant_id"])
    assert api.call("POST", f"/tenants/{tid}/activate", first).status_code == 200
    PS.populate_school(admin_engine, tid, existing=True)
    for i in range(2):
        files.put(f"t/{tid}/docs/{uuid.uuid4()}/v1/original.pdf", b"%PDF-" + bytes([i]), "x")
    return tid


def _platform_events(tenant_id: uuid.UUID) -> list[str]:
    with platform_session() as s:
        return list(
            s.execute(
                text(
                    "SELECT action FROM platform.audit_events WHERE subject_tenant_id = :t "
                    "ORDER BY seq"
                ),
                {"t": tenant_id},
            ).scalars()
        )


def _confirm(api: Api, op: Operator, tid: uuid.UUID) -> Any:
    return api.call(
        "POST",
        f"/tenants/{tid}/offboarding:confirm-export",
        op,
        json={"basis": "school_confirmed", "reference": "Letter 2026/09/12"},
    )


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_PLT_005_shared_school_is_deleted_certified_and_marked_deleted(  # noqa: PLR0915, PLR0917
    api: Api,
    owners: tuple[Operator, Operator],
    school: uuid.UUID,
    admin_engine: Engine,
    files: Any,
    cert_store: MemoryInvoiceStore,
    make_plan: Callable[..., uuid.UUID],
) -> None:
    first, second = owners
    other = api.call("POST", "/tenants", first, json=provision_payload(make_plan())).json()
    other_id = uuid.UUID(other["tenant_id"])
    PS.populate_school(admin_engine, other_id, existing=True)
    other_before = PS.row_counts(admin_engine, other_id)

    _offboard(api, first, second, str(school))
    view = api.call("GET", f"/tenants/{school}/offboarding", first)
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["state"] == "awaiting_export"
    approved = dt.datetime.fromisoformat(body["approved_at"])
    assert dt.datetime.fromisoformat(body["deadline_at"]) - approved == dt.timedelta(days=30)

    # The export gate: the job deletes nothing before the export is confirmed.
    offboarding.process_due()
    assert offboarding.get_offboarding(school).state == "awaiting_export"
    assert PS.row_counts(admin_engine, school)["sis.students"] == 2

    confirmed = _confirm(api, second, school)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "scheduled"
    again = _confirm(api, second, school)
    assert (again.status_code, again.json()["code"]) == (409, "export_already_confirmed")

    assert offboarding.process_due().advanced >= 1  # the database is shared by the tests
    assert offboarding.get_offboarding(school).state == "keys_destroyed"
    left = {t: n for t, n in PS.row_counts(admin_engine, school).items() if n}
    assert set(left) <= RETAINED, left
    assert files.count_prefix(f"t/{school}/") == 0
    assert PS.row_counts(admin_engine, other_id) == other_before

    renderer = FakeRenderer()
    assert offboarding.certify_pending(renderer=renderer).advanced >= 1
    assert any("డేటా" in page for page in renderer.pages)  # Telugu labels

    detail = api.call("GET", f"/tenants/{school}", first).json()
    assert detail["tenant_status"] == "deleted"
    run = detail["offboarding"]
    assert run["state"] == "completed"
    assert run["inventory"]["students"] >= 2
    assert run["objects_before"] == 2
    assert run["keys_destroyed"] == 1
    assert run["remaining"] == {}
    assert run["certificate"] is not None
    with admin_engine.connect() as c:
        status: Any = c.execute(
            text("SELECT status FROM core.tenants WHERE id = :t"), {"t": school}
        ).scalar_one()
    assert status == "deleted"

    with platform_session() as s:
        cert = (
            s.execute(
                text("SELECT * FROM platform.deletion_certificates WHERE tenant_id = :t"),
                {"t": school},
            )
            .mappings()
            .one()
        )
    content = cert["content"]
    assert cert["content_sha256"] == content_sha256(content)
    assert cert["object_key"].startswith("platform/deletion-certificates/")
    assert cert["object_key"] in cert_store.objects
    assert content["school_name"] == "Synthetic Public School"
    assert content["requested_by"] == str(first.id)
    assert content["approved_by"] == str(second.id)
    retained = {r["category"]: r for r in content["retained"]}
    assert set(retained) == {"school_audit_log", "audit_archives", "backups", "billing_records"}
    assert retained["school_audit_log"]["delete_after"]
    assert content["pending"] == [
        {"category": "staff_sign_in_accounts", "reason": "identity_rework"}
    ]
    flat = repr(content).lower()
    for personal in ("synthetic clerk", "synthetic student", "@", "display_name", "email"):
        assert personal not in flat, personal

    dl = api.call("GET", f"/tenants/{school}/deletion-certificate/download-url", first)
    assert dl.status_code == 200, dl.text
    assert dl.json()["filename"].startswith("certificate-of-deletion-")
    events = _platform_events(school)
    for action in (
        "tenant.offboard_approved",
        "tenant.export_confirmed",
        "tenant.deletion_started",
        "tenant.data_deleted",
        "tenant.keys_destroyed",
        "tenant.deleted",
        "tenant.deletion_certified",
        "tenant.deletion_certificate_downloaded",
    ):
        assert action in events, action
    # A second certificate attempt changes nothing.
    assert offboarding.issue_certificate(school, renderer=renderer) == "exists"


def test_FR_PLT_005_failure_is_recorded_and_the_run_resumes(
    api: Api,
    owners: tuple[Operator, Operator],
    school: uuid.UUID,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = owners
    _offboard(api, first, second, str(school))
    assert _confirm(api, first, school).status_code == 200
    real = tenancy.purge_tenant

    def boom(tenant_id: uuid.UUID) -> Any:
        raise Conflict("Synthetic failure.", code="synthetic_failure")

    monkeypatch.setattr(tenancy, "purge_tenant", boom)
    with pytest.raises(Conflict):
        offboarding.advance(school)
    run = offboarding.get_offboarding(school)
    assert (run.state, run.failed_step, run.last_error) == (
        "deleting",
        "purge",
        "synthetic_failure",
    )
    assert not run.in_progress  # the lease was released
    assert PS.row_counts(admin_engine, school)["core.tenant_keys"] == 1  # keys never go first
    assert "tenant.deletion_failed" in _platform_events(school)

    # A crashed runner: its lease has not expired yet, so nobody else runs the school.
    monkeypatch.setattr(tenancy, "purge_tenant", real)
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.offboarding_runs SET lease_id = gen_random_uuid(), "
                "lease_expires_at = now() + interval '5 minutes' WHERE tenant_id = :t"
            ),
            {"t": school},
        )
    assert offboarding.advance(school) == "skipped"
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.offboarding_runs "
                "SET lease_expires_at = now() - interval '1 second' WHERE tenant_id = :t"
            ),
            {"t": school},
        )
    assert offboarding.advance(school) == "keys_destroyed"
    run = offboarding.get_offboarding(school)
    assert (run.state, run.failed_step, run.last_error) == ("keys_destroyed", None, None)
    assert run.attempts == 2


def test_FR_PLT_005_deadline_alerts_once(
    api: Api, owners: tuple[Operator, Operator], school: uuid.UUID
) -> None:
    first, second = owners
    _offboard(api, first, second, str(school))
    run = offboarding.get_offboarding(school)
    offboarding.check_deadlines(at=run.approved_at + dt.timedelta(days=25))
    late = run.approved_at + dt.timedelta(days=31)
    offboarding.check_deadlines(at=late)
    with platform_session() as s:
        alerted = s.execute(
            text(
                "SELECT due_soon_alerted_at IS NOT NULL, overdue_alerted_at IS NOT NULL "
                "FROM platform.offboarding_runs WHERE tenant_id = :t"
            ),
            {"t": school},
        ).one()
    assert tuple(alerted) == (True, True)
    assert _platform_events(school).count("tenant.deletion_overdue") == 1
    offboarding.check_deadlines(at=late + dt.timedelta(days=1))
    assert _platform_events(school).count("tenant.deletion_overdue") == 1


def test_FR_PLT_005_dedicated_school_is_certified_after_teardown(
    api: Api,
    owners: tuple[Operator, Operator],
    make_plan: Callable[..., uuid.UUID],
    cert_store: MemoryInvoiceStore,
) -> None:
    first, second = owners
    body = provision_payload(make_plan(tier="dedicated"), tier="dedicated", owner=None)
    res = api.call("POST", "/tenants", first, json=body)
    assert res.status_code == 201, res.text
    tid = uuid.UUID(res.json()["tenant_id"])
    with platform_session() as s:  # the host reported in and the school went live on it
        s.execute(
            text("UPDATE platform.deployments SET tenant_status = 'active' WHERE tenant_id = :t"),
            {"t": tid},
        )
    _offboard(api, first, second, str(tid))
    refs = {"kms_deletion_reference": "tf-run-101", "host_teardown_reference": "tf-run-102"}
    early = api.call("POST", f"/tenants/{tid}/offboarding:confirm-teardown", first, json=refs)
    assert (early.status_code, early.json()["code"]) == (409, "invalid_state")
    assert _confirm(api, first, tid).status_code == 200
    torn = api.call("POST", f"/tenants/{tid}/offboarding:confirm-teardown", first, json=refs)
    assert torn.status_code == 200, torn.text
    assert torn.json()["state"] == "keys_destroyed"
    assert offboarding.issue_certificate(tid, renderer=FakeRenderer()) == "issued"
    detail = api.call("GET", f"/tenants/{tid}", first).json()
    assert detail["tenant_status"] == "deleted"
    with platform_session() as s:
        content: Any = s.execute(
            text("SELECT content FROM platform.deletion_certificates WHERE tenant_id = :t"),
            {"t": tid},
        ).scalar_one()
    assert content["backups"]["method"] == "crypto_shredded"
    assert content["deleted"] == [{"category": "dedicated_host", "count": 1}]


def test_FR_PLT_005_shared_school_has_no_teardown_and_unapproved_school_no_run(
    api: Api, owners: tuple[Operator, Operator], school: uuid.UUID
) -> None:
    first, second = owners
    none = api.call("GET", f"/tenants/{school}/offboarding", first)
    assert (none.status_code, none.json()["code"]) == (409, "not_offboarding")
    pending = api.call("GET", f"/tenants/{school}/deletion-certificate/download-url", first)
    assert (pending.status_code, pending.json()["code"]) == (409, "certificate_pending")
    _offboard(api, first, second, str(school))
    assert _confirm(api, first, school).status_code == 200
    refs = {"kms_deletion_reference": "tf-run-1", "host_teardown_reference": "tf-run-2"}
    res = api.call("POST", f"/tenants/{school}/offboarding:confirm-teardown", first, json=refs)
    assert (res.status_code, res.json()["code"]) == (409, "not_dedicated")
    bad = api.call(
        "POST",
        f"/tenants/{school}/offboarding:confirm-export",
        first,
        json={"basis": "school_confirmed", "reference": "line one\nline two"},
    )
    assert bad.status_code == 422


def test_FR_PLT_005_audit_chain_is_kept_until_its_retention(
    api: Api,
    owners: tuple[Operator, Operator],
    school: uuid.UUID,
    admin_engine: Engine,
    cert_store: MemoryInvoiceStore,
) -> None:
    first, second = owners
    _offboard(api, first, second, str(school))
    assert _confirm(api, first, school).status_code == 200
    assert offboarding.advance(school) == "keys_destroyed"
    assert offboarding.issue_certificate(school, renderer=FakeRenderer()) == "issued"
    run = offboarding.get_offboarding(school)
    assert run.audit_delete_after is not None
    assert run.audit_delete_after - run.completed_at >= dt.timedelta(days=365)  # type: ignore[operator]
    before = PS.row_counts(admin_engine, school)["audit.events"]
    assert before > 0
    # Not due yet: nothing happens.
    assert offboarding.purge_expired_audit() == 0
    # "Due" by the platform's clock, but the database still refuses events younger than a
    # year: the chain stays (fail closed) and the run is retried later.
    assert offboarding.purge_expired_audit(at=run.audit_delete_after) == 0
    assert PS.row_counts(admin_engine, school)["audit.events"] == before
    assert offboarding.get_offboarding(school).audit_deleted_at is None

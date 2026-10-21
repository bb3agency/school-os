"""Public handles and the resolver (ADR-0047; FR-PUB-002, FR-PUB-005..007, FR-PUB-012; SEC-037,
SEC-039). Synthetic data only.

Issue, resolve, revoke, expiry, hash-only storage, the resolver's catalog (two return columns,
grants, pinned search_path), isolation between schools (a handle of school A never opens school
B's data; a school cannot write a handle for another school), suspended schools, the app role's
narrow grants and delete policy, the purge job and the audit trail.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.db import context_free_session, tenant_session
from app.public import service, tasks, tokens
from app.public.config import PARENT_KINDS

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
W = sys.modules["sos_test_api_world"]

NOW = dt.datetime.now(dt.UTC)


def _events(admin: Engine, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        e
        for e in W.audit_events(admin, tenant_id)
        if e["resource_id"] == resource_id and e["resource_type"] == "public_handle"
    ]


# --- catalog ------------------------------------------------------------------------------------


def test_SEC_039_resolver_returns_exactly_two_columns_and_is_pinned(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT p.proretset, p.prosecdef, p.proconfig, r.rolname AS owner, "
                "pg_get_function_result(p.oid) AS result, "
                "has_function_privilege('sos_app', p.oid, 'EXECUTE') AS app_exec, "
                "has_function_privilege('sos_platform', p.oid, 'EXECUTE') AS platform_exec, "
                "has_function_privilege('sos_readonly', p.oid, 'EXECUTE') AS readonly_exec "
                "FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
                "WHERE p.oid = to_regprocedure('core.resolve_public_handle(text,bytea)')"
            )
        ).one()
    assert row.proretset
    assert row.prosecdef
    assert row.owner == "sos_definer"
    assert row.proconfig == ["search_path=pg_catalog, pg_temp"]
    assert row.result == "TABLE(tenant_id uuid, handle_id uuid)"
    assert (row.app_exec, row.platform_exec, row.readonly_exec) == (True, False, False)


def test_SEC_039_definer_access_only_on_handles_and_tenants_for_the_resolver(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as c:
        policies = {
            r.policyname: r
            for r in c.execute(
                text(
                    "SELECT policyname, permissive, roles::text[] AS roles, cmd FROM pg_policies "
                    "WHERE schemaname = 'core' AND tablename = 'public_handles'"
                )
            )
        }
        flags = c.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'core.public_handles'::regclass"
            )
        ).one()
    assert set(policies) == {
        "tenant_isolation",
        "definer_access",
        "offboarding_purge",
        "purge_expired_only",
    }
    assert flags.relrowsecurity
    assert flags.relforcerowsecurity
    assert policies["purge_expired_only"].permissive == "RESTRICTIVE"
    assert policies["purge_expired_only"].cmd == "DELETE"
    assert policies["purge_expired_only"].roles == ["sos_app"]
    assert policies["offboarding_purge"].roles == ["sos_purger"]


def test_SEC_037_app_role_grants_are_narrow_and_readonly_sees_nothing(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as c:
        table = {
            (r.grantee, r.privilege_type)
            for r in c.execute(
                text(
                    "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_schema = 'core' AND table_name = 'public_handles'"
                )
            )
        }
        columns: set[str] = set(
            c.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE table_schema = 'core' AND table_name = 'public_handles' "
                    "AND grantee = 'sos_app' AND privilege_type = 'UPDATE'"
                )
            ).scalars()
        )
    app = {p for g, p in table if g == "sos_app"}
    assert app == {"SELECT", "INSERT", "DELETE"}
    assert columns == {
        "revoked_at",
        "revoked_reason",
        "first_viewed_at",
        "last_viewed_at",
        "view_count",
    }
    assert not {p for g, p in table if g in ("sos_readonly", "sos_platform")}
    assert {p for g, p in table if g == "sos_definer"} == {"SELECT"}
    assert {p for g, p in table if g == "sos_purger"} == {"SELECT", "DELETE"}


# --- issue and resolve ------------------------------------------------------------------------


def test_FR_PUB_005_issue_stores_only_the_hash_and_resolves_to_the_school(
    world: Any, admin_engine: Engine
) -> None:
    _, guardian = P.student_with_guardian(world.a)
    issued = P.issue(world.a, "parent_notice", guardian_id=guardian)
    assert issued.key is not None
    assert len(issued.key) == 43
    row = P.handle_row(admin_engine, issued.handle_id)
    assert row["key_hash"] == P.key_hash(issued.key)
    assert issued.key not in repr(row)
    assert row["tenant_id"] == world.a.tenant_id
    assert row["guardian_id"] == guardian
    assert row["expires_at"] - row["created_at"] == dt.timedelta(days=30)
    found = service.resolve("parent_notice", issued.key)
    assert found == service.ResolvedHandle(world.a.tenant_id, issued.handle_id, "parent_notice")
    assert service.resolve_any(PARENT_KINDS, issued.key) == found
    # Same key, other kind: nothing.
    assert service.resolve("parent_dues", issued.key) is None


@pytest.mark.parametrize(
    ("kind", "days"),
    [("parent_receipt", 90), ("parent_dues", 7), ("parent_optin", 7)],
)
def test_FR_PUB_006_lifetimes_per_kind(
    kind: Any, days: int, world: Any, admin_engine: Engine
) -> None:
    issued = P.issue(world.a, kind, target_type="receipt")
    row = P.handle_row(admin_engine, issued.handle_id)
    assert row["expires_at"] - row["created_at"] == dt.timedelta(days=days)


def test_ADR_0047_provider_routes_and_certificate_codes_never_expire(
    world: Any, admin_engine: Engine
) -> None:
    route = P.issue(world.a, "razorpay_route", target_type="payment_connection")
    assert route.key is not None
    assert len(route.key) == 24
    assert route.expires_at is None
    assert service.resolve("razorpay_route", route.key) is not None
    code = P.issue(world.a, "cert_verify", key="ABCD2345EFGH6789", target_type="certificate")
    assert code.key is None  # the caller's own code is never echoed back
    assert P.handle_row(admin_engine, code.handle_id)["expires_at"] is None
    assert service.resolve("cert_verify", "ABCD2345EFGH6789") is not None
    with pytest.raises(service.HandleKeyInUse):
        P.issue(world.b, "cert_verify", key="ABCD2345EFGH6789", target_type="certificate")


def test_FR_PUB_005_service_refuses_misuse(world: Any) -> None:
    with pytest.raises(ValueError, match="generated by the service"):
        P.issue(world.a, "parent_notice", key=tokens.new_parent_token())
    with pytest.raises(ValueError, match="only parent links name a guardian"):
        P.issue(world.a, "razorpay_route", guardian_id=uuid.uuid4(), target_type="connection")
    with pytest.raises(ValueError, match="short lower-case code"):
        P.issue(world.a, "parent_notice", target_type="Notice; DROP")


def test_FR_PUB_012_issue_and_revoke_are_audited_with_ids_only(
    world: Any, admin_engine: Engine
) -> None:
    _, guardian = P.student_with_guardian(world.a)
    issued = P.issue(world.a, "parent_dues", guardian_id=guardian, target_type="fee_account")
    with tenant_session(world.a.tenant_id, world.a.people["owner"].user_id) as db:
        assert service.revoke(db, issued.handle_id, reason="manual") is True
        assert service.revoke(db, issued.handle_id, reason="manual") is False
    events = _events(admin_engine, world.a.tenant_id, issued.handle_id)
    assert [e["action"] for e in events] == ["public.link.issued", "public.link.revoked"]
    issued_summary = events[0]["summary"]
    assert issued_summary["kind"] == "parent_dues"
    assert issued_summary["guardian_id"] == str(guardian)
    assert "expires_at" in issued_summary
    assert issued.key is not None
    assert issued.key not in str(events)
    assert events[1]["summary"] == {"kind": "parent_dues", "reason": "manual"}
    route = P.issue(world.a, "razorpay_route", target_type="payment_connection")
    assert [e["action"] for e in _events(admin_engine, world.a.tenant_id, route.handle_id)] == [
        "public.handle.issued"
    ]


# --- uniform misses ------------------------------------------------------------------------------


def test_FR_PUB_011_unknown_expired_revoked_and_malformed_resolve_to_nothing(
    world: Any, admin_engine: Engine
) -> None:
    assert service.resolve("parent_notice", tokens.new_parent_token()) is None
    assert service.resolve("parent_notice", "not-a-token") is None
    expired = tokens.new_parent_token()
    P.insert_raw(
        admin_engine,
        world.a.tenant_id,
        kind="parent_notice",
        key=expired,
        created_at=NOW - dt.timedelta(days=31),
        expires_at=NOW - dt.timedelta(seconds=1),
    )
    assert service.resolve("parent_notice", expired) is None
    revoked = P.issue(world.a, "parent_notice")
    assert revoked.key is not None
    with tenant_session(world.a.tenant_id) as db:
        service.revoke(db, revoked.handle_id, reason="notice_withdrawn", by_user=False)
    assert service.resolve("parent_notice", revoked.key) is None


def test_ADR_0047_D35_suspended_school_resolves_receipts_certificates_and_waba(
    world: Any, admin_engine: Engine
) -> None:
    school = W.School(W.provision_school())
    owner = W.add_member(admin_engine, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    notice = P.issue(school, "parent_notice")
    receipt = P.issue(school, "parent_receipt", target_type="receipt")
    code = P.issue(school, "cert_verify", key="SUSP2345EFGH6789", target_type="certificate")
    waba = "1" + uuid.uuid4().hex[:14].translate(str.maketrans("abcdef", "123456"))
    P.issue(school, "meta_waba", key=waba, target_type="messaging_account")
    assert notice.key is not None
    assert receipt.key is not None
    P.set_tenant_status(admin_engine, school.tenant_id, "suspended")
    try:
        assert service.resolve("parent_notice", notice.key) is None
        assert service.resolve("parent_receipt", receipt.key) is not None
        assert service.resolve("cert_verify", "SUSP2345EFGH6789") is not None
        # D35 (0063_r2_closeout): the WhatsApp account still resolves, so a parent's STOP is
        # recorded (messaging records STOP only and sends nothing).
        assert service.resolve("meta_waba", waba) is not None
        assert code.handle_id
        P.set_tenant_status(admin_engine, school.tenant_id, "offboarding")
        assert service.resolve("parent_receipt", receipt.key) is None
        assert service.resolve("meta_waba", waba) is None
    finally:
        P.set_tenant_status(admin_engine, school.tenant_id, "active")


# --- isolation -----------------------------------------------------------------------------------


def test_SEC_039_a_handle_of_school_a_never_opens_school_b(world: Any) -> None:
    issued = P.issue(world.a, "parent_notice")
    assert issued.key is not None
    found = service.resolve("parent_notice", issued.key)
    assert found is not None
    assert found.tenant_id == world.a.tenant_id
    with tenant_session(world.b.tenant_id) as db:
        assert service.load_handle(db, issued.handle_id) is None
    with tenant_session(world.a.tenant_id) as db:
        view = service.load_handle(db, issued.handle_id, PARENT_KINDS)
    assert view is not None
    assert view.tenant_id == world.a.tenant_id
    with tenant_session(world.a.tenant_id) as db:
        assert service.load_handle(db, issued.handle_id, ("cert_verify",)) is None


def test_SEC_039_a_school_cannot_write_a_handle_for_another_school(
    world: Any, app_engine: Engine
) -> None:
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_session(world.b.tenant_id) as db,
    ):
        db.execute(
            text(
                "INSERT INTO core.public_handles (id, tenant_id, kind, key_hash, target_type, "
                "target_id, expires_at) VALUES (:i, :t, 'parent_notice', :h, 'notice', :ti, "
                "now() + interval '1 day')"
            ),
            {
                "i": uuid.uuid4(),
                "t": world.a.tenant_id,
                "h": P.key_hash(tokens.new_parent_token()),
                "ti": uuid.uuid4(),
            },
        )


def test_SEC_039_the_app_role_without_school_context_sees_no_handles(world: Any) -> None:
    P.issue(world.a, "parent_notice")
    with context_free_session() as db:
        assert db.execute(text("SELECT count(*) FROM core.public_handles")).scalar_one() == 0


def test_SEC_037_app_role_cannot_rewrite_or_unrevoke_a_handle(world: Any) -> None:
    issued = P.issue(world.a, "parent_notice")
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(world.a.tenant_id) as db,
    ):
        db.execute(
            text(
                "UPDATE core.public_handles SET expires_at = now() + interval '9 days' "
                "WHERE id = :i"
            ),
            {"i": issued.handle_id},
        )
    with tenant_session(world.a.tenant_id) as db:
        service.revoke(db, issued.handle_id, reason="manual", by_user=False)
    with (
        pytest.raises(DBAPIError, match="stays revoked"),
        tenant_session(world.a.tenant_id) as db,
    ):
        db.execute(
            text(
                "UPDATE core.public_handles SET revoked_at = NULL, revoked_reason = NULL "
                "WHERE id = :i"
            ),
            {"i": issued.handle_id},
        )


def test_FR_PUB_006_app_role_deletes_only_long_expired_handles(
    world: Any, admin_engine: Engine
) -> None:
    fresh = P.issue(world.a, "parent_notice")
    with tenant_session(world.a.tenant_id) as db:
        deleted = db.execute(
            text("DELETE FROM core.public_handles WHERE id = :i"), {"i": fresh.handle_id}
        )
        assert deleted.rowcount == 0  # type: ignore[attr-defined]
    assert P.handle_row(admin_engine, fresh.handle_id)["id"] == fresh.handle_id


# --- views, revocation by guardian, purge -----------------------------------------------------


def test_FR_PUB_012_first_view_is_audited_later_views_counted(
    world: Any, admin_engine: Engine
) -> None:
    issued = P.issue(world.a, "parent_notice")
    for _ in range(3):
        with tenant_session(world.a.tenant_id) as db:
            view = service.load_handle(db, issued.handle_id)
            assert view is not None
            service.record_view(db, view)
    row = P.handle_row(admin_engine, issued.handle_id)
    assert row["view_count"] == 3
    assert row["first_viewed_at"] is not None
    assert row["last_viewed_at"] >= row["first_viewed_at"]
    actions = [e["action"] for e in _events(admin_engine, world.a.tenant_id, issued.handle_id)]
    assert actions.count("public.link.viewed") == 1


def test_FR_PUB_007_guardian_links_are_revoked_on_unlink_and_phone_change(
    world: Any, admin_engine: Engine, api: Any
) -> None:
    student, guardian = P.student_with_guardian(world.a)
    first = P.issue(world.a, "parent_notice", guardian_id=guardian)
    second = P.issue(world.a, "parent_dues", guardian_id=guardian, target_type="fee_account")
    office = world.person("office_admin")
    version = P.SW.version(admin_engine, "sis.guardians", guardian)
    res = api.call(
        office,
        "PATCH",
        f"/api/v1/students/{student}/guardians/{guardian}",
        json={"phone": "9876543210"},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert res.status_code == 200, res.text
    for issued in (first, second):
        row = P.handle_row(admin_engine, issued.handle_id)
        assert (row["revoked_reason"], row["revoked_at"] is not None) == ("phone_changed", True)
    third = P.issue(world.a, "parent_notice", guardian_id=guardian)
    version = P.SW.version(admin_engine, "sis.guardians", guardian)
    res = api.call(
        office,
        "DELETE",
        f"/api/v1/students/{student}/guardians/{guardian}",
        headers={"If-Match": f'W/"{version}"'},
    )
    assert res.status_code == 204, res.text
    row = P.handle_row(admin_engine, third.handle_id)
    assert row["revoked_reason"] == "guardian_unlinked"
    # The guardian row is gone (no other student): the FK keeps the handle, guardian cleared.
    assert row["guardian_id"] is None


def test_FR_PUB_006_purge_deletes_only_handles_expired_over_30_days(
    world: Any, admin_engine: Engine
) -> None:
    old = P.insert_raw(
        admin_engine,
        world.a.tenant_id,
        kind="parent_dues",
        key=tokens.new_parent_token(),
        created_at=NOW - dt.timedelta(days=60),
        expires_at=NOW - dt.timedelta(days=31),
    )
    recent = P.insert_raw(
        admin_engine,
        world.a.tenant_id,
        kind="parent_dues",
        key=tokens.new_parent_token(),
        created_at=NOW - dt.timedelta(days=20),
        expires_at=NOW - dt.timedelta(days=13),
    )
    route = P.issue(world.a, "razorpay_route", target_type="payment_connection")
    totals = tasks.purge_all()
    assert totals["failed"] == 0
    assert totals["deleted"] >= 1
    with admin_engine.connect() as c:
        left: set[uuid.UUID] = set(
            c.execute(
                text("SELECT id FROM core.public_handles WHERE id = ANY(:i)"),
                {"i": [old, recent, route.handle_id]},
            ).scalars()
        )
    assert left == {recent, route.handle_id}


def test_ADR_0029_offboarding_inventory_counts_handles(world: Any) -> None:
    P.issue(world.a, "parent_notice")
    with tenant_session(world.a.tenant_id) as db:
        counts = service.tenant_data_counts(db)
    assert counts["core.public_handles"] >= 1

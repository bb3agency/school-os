"""``make seed-synthetic`` against a real database (docs/12 §3, §4.4; NFR-MNT-002, FR-TEN-003,
FR-TEN-010, FR-IAM-010..012, SEC-008).

One dataset is seeded per module with a unique code prefix (the session database is shared with
other suites), then re-seeded to prove idempotency, and seeded again under a second prefix with
the same seed to prove determinism. Synthetic data only.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.audit import service as audit
from app.authz.catalog import system_roles
from app.authz.kv import InMemoryKV, set_kv_store
from app.core.config import Environment, KeyWrapperKind, Settings, get_settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import tenant_session
from app.devtools import plan as synth
from app.devtools import seed_synthetic as cli
from app.devtools import seeder
from app.devtools.plan import StaffSpec

pytestmark = pytest.mark.db

SEED = 7
CI_SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _wrapper() -> LocalDevKeyWrapper:
    return LocalDevKeyWrapper(CI_SETTINGS)


def _prefix() -> str:
    return f"t{uuid.uuid4().hex[:8]}"


@dataclass
class Run:
    code: int
    out: str
    err: str

    @property
    def summary(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.out)
        return data


def run_seed(admin_engine: Engine, *args: str) -> Run:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        list(args),
        settings=CI_SETTINGS,
        stdout=out,
        stderr=err,
        wrapper=_wrapper(),
        owner_bootstrap=seeder.PlatformOwnerBootstrap(),
    )
    return Run(code, out.getvalue(), err.getvalue())


@dataclass
class Seeded:
    prefix: str
    plan: synth.DatasetPlan
    first: Run
    second: Run


@pytest.fixture(scope="module")
def kv() -> Iterator[None]:
    set_kv_store(InMemoryKV())
    yield
    set_kv_store(None)


@pytest.fixture(scope="module")
def seeded(admin_engine: Engine, app_engine: Engine, platform_engine: Engine, kv: None) -> Seeded:
    prefix = _prefix()
    first = run_seed(admin_engine, "--code-prefix", prefix, "--seed", str(SEED))
    assert first.code == 0, first.err
    second = run_seed(admin_engine, "--code-prefix", prefix, "--seed", str(SEED))
    assert second.code == 0, second.err
    return Seeded(prefix, synth.build_plan(seed=SEED, code_prefix=prefix), first, second)


def _staff_rows(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    sql = text(
        "SELECT u.idp_subject, u.display_name, u.email, u.preferred_language, m.id AS mid, "
        "m.status, array_agg(r.key ORDER BY r.key) AS roles "
        "FROM core.memberships m JOIN core.users u ON u.id = m.user_id "
        "JOIN core.membership_roles mr ON mr.tenant_id = m.tenant_id AND mr.membership_id = m.id "
        "JOIN core.roles r ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id "
        "WHERE m.tenant_id = :t GROUP BY u.id, m.id ORDER BY u.idp_subject"
    )
    with admin.connect() as c:
        return [dict(r._mapping) for r in c.execute(sql, {"t": tenant_id})]


# --- environment guard -------------------------------------------------------------------------


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_docs12_s3_refuses_outside_local_and_ci(env: Environment) -> None:
    settings = Settings(
        env=env,
        key_wrapper=KeyWrapperKind.KMS,
        database_url=SecretStr("postgresql+psycopg://sos_app:secret@db.internal/schoolos"),
        platform_database_url=SecretStr(
            "postgresql+psycopg://sos_platform:secret@db.internal/schoolos"
        ),
        service_token_key=SecretStr("x" * 48),
    )
    calls: list[uuid.UUID] = []

    def bootstrap(tenant_id: uuid.UUID, spec: StaffSpec) -> None:
        calls.append(tenant_id)

    out, err = io.StringIO(), io.StringIO()
    code = cli.main([], settings=settings, stdout=out, stderr=err, owner_bootstrap=bootstrap)
    assert code == cli.EXIT_REFUSED == 2
    assert "disabled outside local/ci" in err.getvalue()
    assert out.getvalue() == ""
    assert calls == []


@pytest.mark.parametrize(
    ("env", "wrapper"), [("staging", "local-dev"), ("prod", "local-dev"), ("prod", "kms")]
)
def test_docs12_s3_module_entrypoint_refuses_outside_local_and_ci(env: str, wrapper: str) -> None:
    """``python -m`` exits 2 without a traceback, even when the settings themselves are invalid."""
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("SOS_")}
    child_env.update({"SOS_ENV": env, "SOS_KEY_WRAPPER": wrapper})
    res = subprocess.run(
        [sys.executable, "-m", "app.devtools.seed_synthetic"],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 2, res.stderr
    assert "disabled outside local/ci" in res.stderr
    assert "Traceback" not in res.stderr
    assert res.stdout == ""


def test_docs12_s3_invalid_settings_are_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOS_ENV", "prod")
    monkeypatch.setenv("SOS_KEY_WRAPPER", "local-dev")
    get_settings.cache_clear()
    try:
        out, err = io.StringIO(), io.StringIO()
        assert cli.main([], stdout=out, stderr=err) == cli.EXIT_REFUSED
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert "disabled outside local/ci" in err.getvalue()


def test_cli_rejects_bad_plan_arguments() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["--dataset-version", "v9"], settings=CI_SETTINGS, stdout=out, stderr=err)
    assert code == cli.EXIT_REFUSED
    assert "dataset version" in err.getvalue()


# --- the seeded dataset -------------------------------------------------------------------------


def test_docs12_s3_summary_counts_ids_only(seeded: Seeded) -> None:
    summary = seeded.first.summary
    assert summary["dataset_version"] == "v1"
    assert summary["seed"] == SEED
    assert [t["code"] for t in summary["tenants"]] == [f"{seeded.prefix}-a", f"{seeded.prefix}-b"]
    roles = system_roles()
    for t, plan in zip(summary["tenants"], seeded.plan.tenants, strict=True):
        assert t["tenant_id"] == str(plan.tenant_id)
        assert t["outcome"] == "created"
        counts = t["counts"]
        assert counts["academic_years"] == 2
        assert counts["classes"] == 15
        assert counts["sections"] == 2 * 54
        assert counts["members"] == counts["members_active"] == len(plan.staff)
        for role in roles:
            assert counts[f"role.{role}"] >= 1
        assert counts["role.class_teacher"] == 54
        assert t["created"]["tenants"] == 1
        assert t["created"]["members"] == len(plan.staff)
        assert t["created"]["class_teacher_assignments"] == 54


def test_docs12_s3_rerun_is_idempotent(seeded: Seeded, admin_engine: Engine) -> None:
    first, second = seeded.first.summary, seeded.second.summary
    for a, b in zip(first["tenants"], second["tenants"], strict=True):
        assert b["outcome"] == "unchanged"
        assert b["created"] == {}
        assert a["counts"] == b["counts"]
        assert a["tenant_id"] == b["tenant_id"]
    with admin_engine.connect() as c:
        for plan in seeded.plan.tenants:
            users: int = c.execute(
                text("SELECT count(*) FROM core.users WHERE idp_subject LIKE :p"),
                {"p": f"synthetic|{plan.code}|%"},
            ).scalar_one()
            assert users == len(plan.staff)
            tenants: int = c.execute(
                text("SELECT count(*) FROM core.tenants WHERE code = :c"), {"c": plan.code}
            ).scalar_one()
            assert tenants == 1


def test_docs12_s3_tenants_are_active_shared_and_marked_synthetic(
    seeded: Seeded, admin_engine: Engine
) -> None:
    with admin_engine.connect() as c:
        for plan in seeded.plan.tenants:
            row = c.execute(
                text(
                    "SELECT code, name, status, plan_tier, deployment_mode, boards "
                    "FROM core.tenants WHERE id = :i"
                ),
                {"i": plan.tenant_id},
            ).one()
            assert row.code == plan.code
            assert "Synthetic" in row.name
            assert row.status == "active"
            assert (row.plan_tier, row.deployment_mode) == ("shared", "shared")
            assert list(row.boards) == list(plan.boards)
            years = c.execute(
                text(
                    "SELECT label, is_current FROM core.academic_years "
                    "WHERE tenant_id = :t ORDER BY label"
                ),
                {"t": plan.tenant_id},
            ).all()
            assert [tuple(y) for y in years] == [("2025-26", False), ("2026-27", True)]


def test_FR_IAM_010_every_system_role_has_an_active_synthetic_user(
    seeded: Seeded, admin_engine: Engine
) -> None:
    for plan in seeded.plan.tenants:
        rows = _staff_rows(admin_engine, plan.tenant_id)
        assert len(rows) == len(plan.staff)
        held = Counter(role for r in rows if r["status"] == "active" for role in r["roles"])
        for role in system_roles():
            assert held[role] >= 1, role
        for r in rows:
            assert r["idp_subject"].startswith(f"synthetic|{plan.code}|")
            assert r["email"].endswith(".example.invalid")
            assert len(r["roles"]) == 1


def test_FR_IAM_012_class_teachers_scoped_to_and_assigned_their_section(
    seeded: Seeded, admin_engine: Engine
) -> None:
    sql = text(
        "SELECT m.id AS mid, u.idp_subject, "
        "array_agg(ms.scope_type ORDER BY ms.scope_type) AS types, "
        "array_agg(ms.scope_ref) AS refs "
        "FROM core.memberships m JOIN core.users u ON u.id = m.user_id "
        "JOIN core.membership_scopes ms ON ms.tenant_id = m.tenant_id AND ms.membership_id = m.id "
        "WHERE m.tenant_id = :t GROUP BY m.id, u.idp_subject"
    )
    sections_sql = text(
        "SELECT s.id, c.code, s.name, s.class_teacher_membership_id AS ct, y.is_current "
        "FROM core.sections s "
        "JOIN core.classes c ON c.tenant_id = s.tenant_id AND c.id = s.class_id "
        "JOIN core.academic_years y ON y.tenant_id = s.tenant_id AND y.id = s.academic_year_id "
        "WHERE s.tenant_id = :t"
    )
    classes_sql = text("SELECT id, code FROM core.classes WHERE tenant_id = :t")
    for plan in seeded.plan.tenants:
        with admin_engine.connect() as c:
            scopes = {r.idp_subject: r for r in c.execute(sql, {"t": plan.tenant_id})}
            sections = c.execute(sections_sql, {"t": plan.tenant_id}).all()
            class_ids = {r.code: r.id for r in c.execute(classes_sql, {"t": plan.tenant_id})}
        current = {(s.code, s.name): s for s in sections if s.is_current}
        assert len(current) == 54
        for spec in plan.staff:
            if spec.role == "class_teacher":
                assert spec.section is not None
                row = scopes[spec.subject]
                section = current[spec.section]
                assert list(row.types) == ["section"]
                assert list(row.refs) == [section.id]
                assert section.ct == row.mid
            elif spec.role == "teacher":
                row = scopes[spec.subject]
                assert set(row.types) == {"class"}
                assert set(row.refs) == {class_ids[c] for c in spec.classes}
            elif spec.role == "owner":
                assert list(scopes[spec.subject].types) == ["school"]
            else:
                assert spec.subject not in scopes
        # every current-year section has its own class teacher; previous-year ones have none
        assert len({s.ct for s in current.values()}) == 54
        assert all(s.ct is None for s in sections if not s.is_current)


def test_docs12_s4_4_tenants_share_overlapping_names(seeded: Seeded, admin_engine: Engine) -> None:
    a, b = seeded.plan.tenants
    names_a = {r["display_name"] for r in _staff_rows(admin_engine, a.tenant_id)}
    names_b = {r["display_name"] for r in _staff_rows(admin_engine, b.tenant_id)}
    overlap = names_a & names_b
    assert a.owner.display_name in overlap
    assert len(overlap) >= len(system_roles())
    assert names_a != names_b


def test_docs12_s3_deterministic_for_the_same_seed(
    seeded: Seeded, admin_engine: Engine, kv: None
) -> None:
    other = _prefix()
    run = run_seed(admin_engine, "--code-prefix", other, "--seed", str(SEED))
    assert run.code == 0, run.err
    again = synth.build_plan(seed=SEED, code_prefix=other)

    def people(tenant_id: uuid.UUID, code: str) -> list[tuple[str, str, str]]:
        rows = _staff_rows(admin_engine, tenant_id)
        return sorted(
            (
                r["idp_subject"].removeprefix(f"synthetic|{code}|"),
                r["display_name"],
                r["preferred_language"],
            )
            for r in rows
        )

    for t1, t2 in zip(seeded.plan.tenants, again.tenants, strict=True):
        assert people(t1.tenant_id, t1.code) == people(t2.tenant_id, t2.code)
        # and both equal the plan
        expected = sorted(
            (f"{s.role}|{s.ordinal}", s.display_name, s.preferred_language) for s in t1.staff
        )
        assert people(t1.tenant_id, t1.code) == expected
    counts_1 = [t["counts"] for t in seeded.first.summary["tenants"]]
    counts_2 = [t["counts"] for t in run.summary["tenants"]]
    assert counts_1 == counts_2


def test_FR_AUD_001_seeded_audit_chains_verify(seeded: Seeded, admin_engine: Engine) -> None:
    for plan in seeded.plan.tenants:
        with tenant_session(plan.tenant_id) as session:
            result = audit.verify_chain(session, plan.tenant_id)
        assert result.ok, result
        assert result.checked > len(plan.staff)
        with admin_engine.connect() as c:
            actions: set[str] = set(
                c.execute(
                    text("SELECT DISTINCT action FROM audit.events WHERE tenant_id = :t"),
                    {"t": plan.tenant_id},
                ).scalars()
            )
        assert {
            "role.created",
            "tenant.key.created",
            "membership.created",
            "membership.role_granted",
            "user.invited",
            "membership.status_changed",
            "academic_year.created",
            "section.created",
            "section.class_teacher_assigned",
        } <= actions


def test_SEC_008_output_and_logs_contain_no_names(
    admin_engine: Engine,
    app_engine: Engine,
    platform_engine: Engine,
    kv: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prefix = _prefix()
    capsys.readouterr()
    run = run_seed(admin_engine, "--code-prefix", prefix, "--seed", "99", "--tenants", "1")
    captured = capsys.readouterr()
    assert run.code == 0, run.err
    printed = run.out + run.err + captured.out + captured.err
    assert "devtools.seed_synthetic.tenant" in printed, "log lines were captured"
    plan = synth.build_plan(seed=99, code_prefix=prefix, tenants=1)
    tenant = plan.tenants[0]
    secrets = {tenant.name}
    for spec in tenant.staff:
        secrets.update({spec.display_name, spec.email, spec.subject})
        secrets.update(spec.display_name.split(" "))
    for secret in secrets:
        assert secret not in printed, secret


def test_seed_resumes_a_school_left_in_provisioning(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine, kv: None
) -> None:
    """A run interrupted after registering the school finishes it on the next run."""
    prefix = _prefix()
    plan = synth.build_plan(seed=SEED, code_prefix=prefix, tenants=1)

    def failing(tenant_id: uuid.UUID, spec: StaffSpec) -> None:
        raise seeder.SeedError("simulated interruption")

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ["--code-prefix", prefix, "--tenants", "1"],
        settings=CI_SETTINGS,
        stdout=out,
        stderr=err,
        wrapper=_wrapper(),
        owner_bootstrap=failing,
    )
    assert code == cli.EXIT_FAILED
    assert "simulated interruption" in err.getvalue()
    with admin_engine.connect() as c:
        status: str = c.execute(
            text("SELECT status FROM core.tenants WHERE id = :i"), {"i": plan.tenants[0].tenant_id}
        ).scalar_one()
    assert status == "provisioning"
    run = run_seed(admin_engine, "--code-prefix", prefix, "--tenants", "1", "--seed", str(SEED))
    assert run.code == 0, run.err
    t = run.summary["tenants"][0]
    assert t["outcome"] == "updated"
    assert t["counts"]["members_active"] == len(plan.tenants[0].staff)


def test_seed_refuses_a_code_taken_by_another_tenant(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine, kv: None
) -> None:
    prefix = _prefix()
    with admin_engine.begin() as c:
        c.execute(
            text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, :n, 'active')"),
            {"i": uuid.uuid4(), "c": f"{prefix}-a", "n": "Synthetic Other School"},
        )
    run = run_seed(admin_engine, "--code-prefix", prefix, "--tenants", "1")
    assert run.code == cli.EXIT_FAILED
    assert "used by another tenant" in run.err


def test_ADR_0019_first_owner_goes_through_invite_and_acceptance(
    seeded: Seeded, admin_engine: Engine
) -> None:
    """Default path: control-plane owner invite, then acceptance on first sign-in."""
    for tenant in seeded.plan.tenants:
        with admin_engine.connect() as c:
            accepted = c.execute(
                text(
                    "SELECT count(*) FROM audit.events "
                    "WHERE tenant_id = :t AND action = 'membership.invitation_accepted'"
                ),
                {"t": tenant.tenant_id},
            ).scalar_one()
            invited = c.execute(
                text(
                    "SELECT count(*) FROM platform.audit_events "
                    "WHERE subject_tenant_id = :t AND action = 'tenant.owner_invite_created'"
                ),
                {"t": tenant.tenant_id},
            ).scalar_one()
        assert accepted == 1
        assert invited == 1

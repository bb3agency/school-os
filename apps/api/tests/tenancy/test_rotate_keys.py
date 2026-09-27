"""Operator command ``python -m app.tenancy.rotate_keys`` (SEC-012; docs/10 §9.1 runbook).

The command runs as ``sos_app`` in the school's own ``tenant_session``; the admin engine is used
only to arrange and inspect rows. Synthetic schools and data only.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core.config import DeploymentMode, Environment, KeyWrapperKind, Settings
from app.tenancy import rotate_keys as cli

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load(
    "sos_test_student_world",
    Path(__file__).resolve().parents[1] / "students" / "student_world.py",
)
W = SW.W
HEALTH = "Synthetic command asthma note"


def ci_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": Environment.CI,
        "key_wrapper": KeyWrapperKind.LOCAL_DEV,
        "local_dev_master_key": SecretStr("synthetic-ci-master-key-0123456789abcdef"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    SW.configure_keyring()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    SW.create(
        s,
        name="Synthetica Command Student",
        section_key="section_9a",
        extra=[SW.ValueIn(attribute_key="health_notes", source="parent_form", value=HEALTH)],
    )
    return s


def run_cli(*args: str, settings: Settings | None = None, engine: Engine | None = None) -> Any:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        list(args),
        settings=settings or ci_settings(),
        engine=engine,
        wrapper=W.wrapper(),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def key_rows(admin: Engine, tenant_id: uuid.UUID) -> list[tuple[int, bool]]:
    with admin.connect() as c:
        return [
            (int(r.key_version), r.retired_at is not None)
            for r in c.execute(
                text(
                    "SELECT key_version, retired_at FROM core.tenant_keys WHERE tenant_id = :t "
                    "ORDER BY key_version"
                ),
                {"t": tenant_id},
            )
        ]


def test_SEC_012_dry_run_shows_the_plan_and_writes_nothing(
    school: Any, admin_engine: Engine
) -> None:
    code, out, _ = run_cli("--tenant", str(school.tenant_id))
    assert code == cli.EXIT_PENDING
    assert f"tenant={school.tenant_id} current_key_version=1 values_on_older_versions=0" in out
    assert "key_version=1 state=current" in out
    assert "values=1" in out
    assert "plan: add key_version=2 hmac_key=carried" in out
    assert key_rows(admin_engine, school.tenant_id) == [(1, False)]
    assert HEALTH not in out


def test_SEC_012_rotate_reencrypt_and_retire_through_the_command(
    school: Any, admin_engine: Engine
) -> None:
    tid = str(school.tenant_id)
    code, out, _ = run_cli("--tenant", tid, "--apply")
    assert code == cli.EXIT_OK, out
    assert "rotated: key_version=2 hmac_key=carried" in out
    assert key_rows(admin_engine, school.tenant_id) == [(1, False), (2, False)]

    code, out, _ = run_cli("--tenant", tid, "--reencrypt")
    assert code == cli.EXIT_PENDING
    assert "plan: re-encrypt 1 values to key_version=2" in out

    code, out, _ = run_cli("--tenant", tid, "--reencrypt", "--apply", "--batch-size", "1")
    assert code == cli.EXIT_OK, out
    assert "reencrypted: key_version=2" in out
    assert "remaining=0" in out

    code, out, _ = run_cli("--tenant", tid, "--retire", "--apply")
    assert code == cli.EXIT_PENDING, "the key cache window has not passed yet"
    assert "kept key_version=1 reason=key_cache_window" in out
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenant_keys SET created_at = created_at - interval '20 minutes' "
                "WHERE tenant_id = :t"
            ),
            {"t": school.tenant_id},
        )
    code, out, _ = run_cli("--tenant", tid, "--retire")
    assert code == cli.EXIT_PENDING
    assert "plan: retire key_version=1" in out
    code, out, _ = run_cli("--tenant", tid, "--retire", "--apply")
    assert code == cli.EXIT_OK, out
    assert "retired key_version=1" in out
    assert key_rows(admin_engine, school.tenant_id) == [(1, True), (2, False)]
    assert HEALTH not in out


def test_SEC_012_refuses_roles_that_bypass_rls_or_are_not_sos_app(
    school: Any, admin_engine: Engine, readonly_engine: Engine
) -> None:
    tid = str(school.tenant_id)
    code, _, err = run_cli("--tenant", tid, "--apply", engine=admin_engine)
    assert (code, err) == (cli.EXIT_REFUSED, "refused: db_role_bypasses_rls\n")
    code, _, err = run_cli("--tenant", tid, "--apply", engine=readonly_engine)
    assert (code, err) == (cli.EXIT_REFUSED, "refused: wrong_db_role\n")
    assert key_rows(admin_engine, school.tenant_id) == [(1, False)]


def test_SEC_012_refuses_a_missing_unknown_or_ineligible_school(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine
) -> None:
    code, _, err = run_cli()
    assert (code, err) == (cli.EXIT_REFUSED, "refused: tenant_required\n")
    code, _, err = run_cli("--tenant", str(uuid.uuid4()), "--apply")
    assert (code, err) == (cli.EXIT_REFUSED, "refused: tenant_not_eligible\n")
    provisioning = W.tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Provisioning School", wrapper=W.wrapper()
    ).tenant_id
    code, _, err = run_cli("--tenant", str(provisioning), "--apply")
    assert (code, err) == (cli.EXIT_REFUSED, "refused: tenant_not_eligible\n")
    assert key_rows(admin_engine, provisioning) == [(1, False)]


def test_SEC_012_dedicated_host_handles_only_its_school(school: Any, admin_engine: Engine) -> None:
    host = ci_settings(
        deployment_mode=DeploymentMode.DEDICATED, dedicated_tenant_id=str(school.tenant_id)
    )
    code, out, _ = run_cli(settings=host)
    assert code == cli.EXIT_PENDING
    assert str(school.tenant_id) in out
    code, _, err = run_cli("--tenant", str(uuid.uuid4()), settings=host)
    assert (code, err) == (cli.EXIT_REFUSED, "refused: tenant_mismatch\n")


def test_SEC_012_invalid_arguments_exit_2() -> None:
    assert run_cli("--tenant", "not-a-uuid")[0] == cli.EXIT_INVALID
    assert run_cli("--reencrypt", "--retire")[0] == cli.EXIT_INVALID
    assert run_cli("--retire", "--new-hmac-key")[0] == cli.EXIT_INVALID
    assert run_cli("--batch-size", "0")[0] == cli.EXIT_INVALID

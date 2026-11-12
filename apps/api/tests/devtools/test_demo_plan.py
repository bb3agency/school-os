"""The demo school plan and the demo CLI's guards (FA-12; FR-DEMO-001..004, FR-DEMO-013..015).
No database: the plan is pure and the guard runs before anything is imported."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import uuid

import pytest
from pydantic import SecretStr

from app.core.config import Environment, KeyWrapperKind, Settings, get_settings
from app.devtools import demo, plan, seed_demo
from app.devtools.plan import StaffSpec
from app.devtools.seed_demo import DemoTenant, next_generation


def test_FR_DEMO_001_one_school_with_the_demo_name_and_boards() -> None:
    p = demo.build_demo_plan()
    assert len(p.tenants) == 1
    school = p.tenants[0]
    assert school.code == "demo-g1-a"
    assert school.name == demo.DEMO_SCHOOL_NAME
    assert "Synthetic" in school.name
    assert "Demo" in school.name
    assert school.boards == ("BSEAP", "CBSE")
    assert school.tenant_id == plan.tenant_id_for("demo-g1-a")


def test_FR_DEMO_002_people_carry_the_generator_markers() -> None:
    school = demo.build_demo_plan().tenants[0]
    roles = {s.role for s in school.staff}
    assert {"owner", "principal", "office_admin", "accountant", "class_teacher"} <= roles
    for s in school.staff:
        assert s.subject.startswith(f"synthetic|{school.code}|")
        assert s.email.endswith(".example.invalid")


def test_FR_DEMO_003_deterministic_per_seed_and_generation() -> None:
    a = demo.build_demo_plan(seed=7, generation=3)
    b = demo.build_demo_plan(seed=7, generation=3)
    assert a == b
    other = demo.build_demo_plan(seed=7, generation=4)
    assert other.tenants[0].code == "demo-g4-a"
    assert other.tenants[0].tenant_id != a.tenants[0].tenant_id


def test_FR_DEMO_003_generation_codes_round_trip() -> None:
    assert demo.generation_of(demo.code_for(12)) == 12
    for code in ("demo-a", "demo-g0-a", "demo-g1-b", "synth-a", "xdemo-g1-a", "demo-g1-a-x"):
        assert demo.generation_of(code) is None
    with pytest.raises(demo.DemoPlanError):
        demo.code_for(0)


def _t(gen: int, status: str) -> DemoTenant:
    return DemoTenant(uuid.uuid4(), demo.code_for(gen), gen, status)


def test_FR_DEMO_004_generations() -> None:
    assert next_generation([], after_reset=False) == 1
    assert next_generation([], after_reset=True) == 1
    assert next_generation([_t(1, "offboarding"), _t(2, "active")], after_reset=False) == 2
    assert next_generation([_t(1, "offboarding"), _t(2, "active")], after_reset=True) == 3
    # Nothing live (all purged): the next run starts a new generation.
    assert next_generation([_t(1, "offboarding")], after_reset=False) == 2


def test_FR_DEMO_009_scenario_data_is_marked_demo() -> None:
    assert all("DEMO" in c.reference_no for c in demo.CERTIFICATES)
    states = {c.valid_until_days is None or c.valid_until_days < 0 for c in demo.CERTIFICATES}
    assert states == {True, False}
    # No contact items in the disclosure draft: no phone number or e-mail is invented.
    assert not {"school_email", "contact_phone"} & set(demo.DISCLOSURE_INPUTS)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_FR_DEMO_014_refuses_outside_local_and_ci(env: Environment) -> None:
    settings = Settings(
        env=env,
        key_wrapper=KeyWrapperKind.KMS,
        database_url=SecretStr(
            "postgresql+psycopg://sos_app:secret@db.internal/schoolos?sslmode=verify-full"
        ),
        platform_database_url=SecretStr(
            "postgresql+psycopg://sos_platform:secret@db.internal/schoolos?sslmode=verify-full"
        ),
        service_token_key=SecretStr("x" * 48),
    )
    calls: list[uuid.UUID] = []

    def bootstrap(tenant_id: uuid.UUID, spec: StaffSpec) -> None:
        calls.append(tenant_id)

    for argv in ([], ["--reset"]):
        out, err = io.StringIO(), io.StringIO()
        code = seed_demo.main(
            argv, settings=settings, stdout=out, stderr=err, owner_bootstrap=bootstrap
        )
        assert code == seed_demo.EXIT_REFUSED == 2
        assert "disabled outside local/ci" in err.getvalue()
        assert out.getvalue() == ""
    assert calls == []


@pytest.mark.parametrize(("env", "wrapper"), [("staging", "kms"), ("prod", "local-dev")])
def test_FR_DEMO_014_module_entrypoint_refuses(env: str, wrapper: str) -> None:
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("SOS_")}
    child_env.update({"SOS_ENV": env, "SOS_KEY_WRAPPER": wrapper})
    res = subprocess.run(
        [sys.executable, "-m", "app.devtools.seed_demo", "--reset"],
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


def test_FR_DEMO_014_invalid_settings_are_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOS_ENV", "prod")
    monkeypatch.setenv("SOS_KEY_WRAPPER", "local-dev")
    get_settings.cache_clear()
    try:
        out, err = io.StringIO(), io.StringIO()
        assert seed_demo.main(["--reset"], stdout=out, stderr=err) == seed_demo.EXIT_REFUSED
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert "disabled outside local/ci" in err.getvalue()


def test_FR_DEMO_014_bad_arguments() -> None:
    ci = Settings(
        env=Environment.CI,
        key_wrapper=KeyWrapperKind.LOCAL_DEV,
        local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
    )
    out, err = io.StringIO(), io.StringIO()
    assert seed_demo.main(["--students", "0"], settings=ci, stdout=out, stderr=err) == 2
    assert "--students" in err.getvalue()

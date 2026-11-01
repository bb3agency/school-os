"""Read-only synthetic-data check (docs/12 §3; invariant 11; docs/14 §3 synthetic purge).

A school is seeded with ``make seed-synthetic``'s generator (``--students``, no documents; one
document is added for a synthetic staff member), next to a school that carries no marker and a
school that looks real but holds one ``SYN-`` student. The session database is shared with other
suites, so assertions look only at these schools. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.authz.kv import InMemoryKV, set_kv_store
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import tenant_session
from app.devtools import seed_synthetic, seeder
from app.devtools import synthetic_check as check
from app.devtools.plan import build_plan
from app.students import crypto

pytestmark = pytest.mark.db

SEED = 11
CI_SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)


def _staging_settings(env: Environment) -> Settings:
    return Settings(
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


@dataclass
class Schools:
    synthetic: uuid.UUID
    clean: uuid.UUID
    mixed: uuid.UUID
    engine: Engine


def _insert_school(admin: Engine, name: str, admission_nos: list[str]) -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, :n, 'active')"),
            {"i": tid, "c": f"chk-{tid.hex[:12]}", "n": name},
        )
        for number in admission_nos:
            c.execute(
                text("INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:i, :t, :a)"),
                {"i": uuid.uuid4(), "t": tid, "a": number},
            )
    return tid


@pytest.fixture(scope="module")
def schools(
    test_database: object,
    admin_engine: Engine,
    app_engine: Engine,
    platform_engine: Engine,
) -> Iterator[Schools]:
    set_kv_store(InMemoryKV())
    crypto.set_key_wrapper(LocalDevKeyWrapper(CI_SETTINGS))
    prefix = f"k{uuid.uuid4().hex[:8]}"
    argv = ["--code-prefix", prefix, "--tenants", "1", "--seed", str(SEED)]
    argv += ["--students", "40", "--no-documents"]
    out, err = io.StringIO(), io.StringIO()
    engine = check.read_only_engine(test_database.app_url)  # type: ignore[attr-defined]
    try:
        code = seed_synthetic.main(
            argv,
            settings=CI_SETTINGS,
            stdout=out,
            stderr=err,
            wrapper=LocalDevKeyWrapper(CI_SETTINGS),
            owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        )
        assert code == 0, err.getvalue()
        synthetic = build_plan(seed=SEED, tenants=1, code_prefix=prefix).tenants[0].tenant_id
        with admin_engine.begin() as c:
            author: uuid.UUID = c.execute(
                text(
                    "SELECT u.id FROM core.users u JOIN core.memberships m ON m.user_id = u.id "
                    "WHERE m.tenant_id = :t AND u.idp_subject LIKE 'synthetic|%' LIMIT 1"
                ),
                {"t": synthetic},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, "
                    "issued_on, sensitivity, created_by) VALUES (:i, :t, 'circular', "
                    "'circular', 'Synthetic circular', :d, 'C1', :u)"
                ),
                {"i": uuid.uuid4(), "t": synthetic, "d": dt.date(2026, 10, 1), "u": author},
            )
        clean = _insert_school(admin_engine, "Pilot Readiness Check School", ["ADM-0001"])
        mixed = _insert_school(admin_engine, "Second Check School", ["ADM-0002", "SYN-09999"])
        yield Schools(synthetic, clean, mixed, engine)
    finally:
        engine.dispose()
        set_kv_store(None)


def _by_id(report: check.Report) -> dict[str, check.SchoolReport]:
    return {s.tenant_id: s for s in report.schools}


def test_docs12_s3_reports_synthetic_school_and_rows(schools: Schools) -> None:
    report = check.scan(schools.engine, Environment.CI)
    found = _by_id(report)
    syn = found[str(schools.synthetic)]
    assert syn.generator_id
    assert syn.synthetic_name
    assert syn.staff.synthetic == syn.staff.total > 0
    assert syn.students.synthetic == syn.students.total == 40
    assert syn.documents.synthetic == syn.documents.total == 1
    assert syn.has_synthetic_data

    clean = found[str(schools.clean)]
    assert not clean.synthetic_school
    assert (clean.staff.synthetic, clean.students.synthetic, clean.documents.synthetic) == (0, 0, 0)
    assert clean.students.total == 1
    assert not clean.has_synthetic_data
    assert clean not in report.flagged

    mixed = found[str(schools.mixed)]
    assert not mixed.synthetic_school
    assert mixed.students.synthetic == 1
    assert mixed.students.total == 2
    assert mixed in report.flagged


def test_invariant11_local_ci_reports_without_failing(schools: Schools) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = check.main([], settings=CI_SETTINGS, engine=schools.engine, stdout=out, stderr=err)
    assert code == check.EXIT_CLEAN, err.getvalue()
    text_out = out.getvalue()
    assert str(schools.synthetic) in text_out
    assert str(schools.mixed) in text_out
    assert str(schools.clean) not in text_out
    assert "Synthetic" not in text_out  # codes and ids only, no names
    assert "result: synthetic data present (ci" in text_out

    out = io.StringIO()
    code = check.main(["--fail-if-found"], settings=CI_SETTINGS, engine=schools.engine, stdout=out)
    assert code == check.EXIT_FOUND
    assert check.PURGE_MESSAGE in out.getvalue()


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_invariant11_staging_prod_must_purge(schools: Schools, env: Environment) -> None:
    out = io.StringIO()
    code = check.main(
        ["--json"], settings=_staging_settings(env), engine=schools.engine, stdout=out
    )
    assert code == check.EXIT_FOUND
    data = json.loads(out.getvalue())
    assert data["result"] == check.PURGE_MESSAGE
    assert data["env"] == env.value
    flagged = {s["tenant_id"]: s for s in data["flagged"]}
    assert flagged[str(schools.synthetic)]["synthetic_school"] is True
    assert flagged[str(schools.mixed)]["students"] == {"synthetic": 1, "total": 2}
    assert str(schools.clean) not in flagged


def test_clean_database_passes_everywhere() -> None:
    clean = check.SchoolReport(
        tenant_id=str(uuid.uuid4()),
        code="real-school",
        status="active",
        generator_id=False,
        synthetic_name=False,
        staff=check.Counts(4, 0),
        students=check.Counts(10, 0),
        documents=check.Counts(2, 0),
    )
    report = check.Report(env="prod", schools=(clean,))
    for env in Environment:
        code, verdict = check._outcome(report, env, fail_if_found=True)
        assert code == check.EXIT_CLEAN
        assert verdict.startswith("no synthetic data found")


def test_invariant11_the_check_cannot_write(schools: Schools, app_engine: Engine) -> None:
    """Both the engine (default_transaction_read_only) and each transaction refuse writes."""
    update = text("UPDATE sis.students SET version = version + 1")
    with tenant_session(schools.synthetic, engine=schools.engine) as session:
        with pytest.raises(DBAPIError, match="read-only transaction"):
            session.execute(update)
        session.rollback()
    with tenant_session(schools.synthetic, engine=app_engine) as session:
        session.execute(check._READ_ONLY)
        with pytest.raises(DBAPIError, match="read-only transaction"):
            session.execute(update)
        session.rollback()
    with tenant_session(schools.synthetic, engine=app_engine) as session:
        assert session.execute(text("SELECT count(*) FROM sis.students")).scalar_one() == 40


def test_unreachable_database_exits_3_without_echoing_the_url() -> None:
    engine = check.read_only_engine("postgresql+psycopg://sos_app:s3cret-pw@127.0.0.1:1/none")
    out, err = io.StringIO(), io.StringIO()
    try:
        code = check.main([], settings=CI_SETTINGS, engine=engine, stdout=out, stderr=err)
    finally:
        engine.dispose()
    assert code == check.EXIT_ERROR
    assert "s3cret-pw" not in err.getvalue()
    assert out.getvalue() == ""

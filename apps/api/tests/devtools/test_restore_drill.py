"""``make restore-drill`` (SEC-024, NFR-AVL-003, NFR-AVL-005; docs/10 §9, §15.3; docs/11 R6).

The db test runs a small drill end to end in a child process (its own two throwaway Postgres
containers, so the session database and the process-wide engines are untouched): seed synthetic
schools, back up as backup.sh, restore as restore.sh, verify. Synthetic data only.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.config import Environment, KeyWrapperKind, Settings
from app.devtools import restore_drill as drill

REPO_ROOT = Path(__file__).resolve().parents[4]


# --- environment guard -------------------------------------------------------------------------


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_SEC_024_drill_refuses_outside_local_and_ci(env: Environment) -> None:
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
    out, err = io.StringIO(), io.StringIO()
    assert drill.main([], settings=settings, stdout=out, stderr=err) == drill.EXIT_REFUSED
    assert "disabled outside local/ci" in err.getvalue()
    assert out.getvalue() == ""


@pytest.mark.parametrize(("env", "wrapper"), [("staging", "local-dev"), ("prod", "kms")])
def test_SEC_024_module_entrypoint_refuses_outside_local_and_ci(env: str, wrapper: str) -> None:
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("SOS_")}
    child_env.update({"SOS_ENV": env, "SOS_KEY_WRAPPER": wrapper})
    res = subprocess.run(
        [sys.executable, "-m", "app.devtools.restore_drill"],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 2, res.stderr
    assert "disabled outside local/ci" in res.stderr
    assert "Traceback" not in res.stderr


def test_SEC_024_drill_needs_two_schools() -> None:
    out, err = io.StringIO(), io.StringIO()
    settings = Settings(env=Environment.CI)
    assert drill.main(["--tenants", "1"], settings=settings, stdout=out, stderr=err) == 2
    assert "--tenants must be >= 2" in err.getvalue()


# --- pure parts --------------------------------------------------------------------------------


def test_SEC_024_rls_problems_flags_tenant_tables_without_forced_rls() -> None:
    catalog = {
        "sis.students": {"rls": True, "force": True, "has_tenant": True, "policies": ["p"]},
        "sis.leaky": {"rls": True, "force": False, "has_tenant": True, "policies": ["p"]},
        "fin.no_policy": {"rls": True, "force": True, "has_tenant": True, "policies": []},
        "core.users": {"rls": False, "force": False, "has_tenant": False, "policies": []},
        "ops.alembic_version": {"rls": False, "force": False, "has_tenant": False, "policies": []},
        "platform.plans": {"rls": False, "force": False, "has_tenant": False, "policies": []},
    }
    assert drill.rls_problems(catalog) == [
        "sis.leaky: RLS not ENABLED+FORCED",
        "fin.no_policy: no policy",
        "core.users: RLS not ENABLED+FORCED",
        "core.users: no policy",
    ]
    assert drill.tenant_table_count(catalog) == 4


def _result(ok: bool) -> drill.DrillResult:
    result = drill.DrillResult(
        started_at="2026-11-01T10:00:00+00:00",
        environment={"git_commit": "abc1234", "postgres_image": drill.PG_IMAGE},
        dataset={"synthetic": True, "tenants": 2, "students_per_school": 5, "seed": 1},
        timings_s={"backup": 1.0, "restore": 2.0, "verify": 0.5},
        source={"rows": {"sis.students": 10, "fin.ledger": 0}},
        restored={"rows": {"sis.students": 10, "fin.ledger": 0}},
    )
    result.check("per-table row counts equal", ok, "1 | 2")
    drill._rto(result)
    return result


def test_SEC_024_report_states_local_synthetic_scope_rto_and_ops_steps() -> None:
    md = drill.render_markdown(_result(True))
    assert "Result: **PASS**" in md
    assert "synthetic data" in md
    assert "RPO cannot be measured locally" in md
    assert "Staging PITR drill" in md
    assert "WAL-G" in md
    assert "| `sis.students` | 10 | 10 |" in md
    assert "fin.ledger" not in md  # tables without rows are summarised, not listed
    assert "1 / 2" in md  # pipes in details do not break the table
    assert "Measured recovery (restore + verify): **2.5 s**" in md
    assert "Result: **FAIL**" in drill.render_markdown(_result(False))


def test_SEC_024_rto_is_not_met_when_the_drill_errored() -> None:
    result = _result(True)
    result.error = "CalledProcessError: exit 1"
    drill._rto(result)
    assert not result.ok
    assert result.rto["within_shared"] is False
    assert result.rto["within_dedicated"] is False


def test_SEC_024_drill_backs_up_like_backup_sh() -> None:
    """The drill's pg_dump flags are the ones backup.sh uses (keep the two in step)."""
    script = (REPO_ROOT / "deploy" / "dedicated" / "scripts" / "backup.sh").read_text("utf-8")
    assert "pg_dump -U postgres -d schoolos -Fc -Z 6" in script
    assert "pg_restore --list" in script
    source = Path(drill.__file__).read_text("utf-8")
    assert '["pg_dump", "-U", "postgres", "-d", DB_NAME, "-Fc", "-Z", "6"]' in source
    restore = (REPO_ROOT / "deploy" / "dedicated" / "scripts" / "restore.sh").read_text("utf-8")
    assert "TEMPLATE template0" in restore
    assert 'pg_restore -U postgres -d "$db" --exit-on-error' in restore
    assert "--exit-on-error" in source
    assert "TEMPLATE template0" in source


# --- end to end ----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.timeout(900)
def test_SEC_024_small_restore_drill_passes(tmp_path: Path) -> None:
    child_env = dict(os.environ)
    child_env.update({"SOS_ENV": "ci", "SOS_LOG_LEVEL": "WARNING"})
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.devtools.restore_drill",
            "--tenants",
            "2",
            "--students",
            "5",
            "--report-dir",
            str(tmp_path),
            "--name",
            "drill",
        ],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=880,
        check=False,
        cwd=REPO_ROOT,
    )
    assert res.returncode == 0, res.stderr[-4000:]
    summary = json.loads(res.stdout.strip().splitlines()[-1])
    assert summary["ok"] is True
    assert summary["checks"] == {
        "source audit chains verify": True,
        "alembic revision equal": True,
        "per-table row counts equal": True,
        "RLS ENABLE+FORCE on every tenant table": True,
        "RLS flags and policies equal to the source": True,
        "no sos_* role has BYPASSRLS or SUPERUSER": True,
        "audit chains verify on the copy": True,
        "tenant_session isolation on the copy": True,
    }
    assert set(summary["timings_s"]) >= {"backup", "restore", "verify"}
    report = json.loads((tmp_path / "drill.json").read_text("utf-8"))
    assert report["dataset"]["synthetic"] is True
    assert report["restored"]["rows"] == report["source"]["rows"]
    assert report["restored"]["rows"]["sis.students"] == 10
    reads = report["restored"]["tenant_reads"]
    assert reads == {
        "own_school": 5,
        "other_school_sees_first": 0,
        "unknown_school_sees_any": 0,
        "no_context_sees_any": 0,
    }
    assert report["rto"]["within_dedicated"] is True
    md = (tmp_path / "drill.md").read_text("utf-8")
    assert "Result: **PASS**" in md

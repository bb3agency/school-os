"""Shared test fixtures: a real PostgreSQL 16 + pgvector database with the production role model.

Database lifecycle (session scope):
1. Start ``pgvector/pgvector:0.8.6-pg16-bookworm`` via testcontainers, or reuse the admin URL in
   ``SOS_TEST_ADMIN_DATABASE_URL`` (psql must then be on PATH).
2. Run ``infra/db/bootstrap.sql`` as the admin (roles, schemas, extensions, grants).
3. ``alembic upgrade head`` as ``sos_migrator``.
Tests then connect as ``sos_app`` / ``sos_platform`` exactly like production. Nothing is skipped
when Docker is missing: DB tests fail loudly instead (never weaken tests).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core import db as core_db
from app.core import ratelimit
from app.core.config import get_settings

# Synthetic supplier identity so tests that build staging/prod Settings pass the invoice guard
# (FR-PLT-016). Tests of the guard itself pass the dev placeholders explicitly.
os.environ.setdefault("SOS_BILLING_SUPPLIER_LEGAL_NAME", "Synthetic Test Supplier Private Limited")
os.environ.setdefault("SOS_BILLING_SUPPLIER_GSTIN", "37ABCDE1234F1Z5")
# Importing app.core.ratelimit above already built and cached Settings (its module-level
# get_logger() calls get_settings()), so without this the supplier identity set here was only
# seen after some other test happened to clear the cache (tests/devtools/test_seed_synthetic.py):
# test_invoice_pdf.py::test_FR_PLT_017_issued_* failed when run alone.
get_settings.cache_clear()

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_SQL = REPO_ROOT / "infra" / "db" / "bootstrap.sql"
ALEMBIC_INI = REPO_ROOT / "apps" / "api" / "alembic.ini"
PG_IMAGE = "pgvector/pgvector:0.8.6-pg16-bookworm"
DB_NAME = "schoolos"

PASSWORDS = {
    "app_password": "test-app-pw",
    "migrator_password": "test-migrator-pw",
    "platform_password": "test-platform-pw",
    "readonly_password": "test-readonly-pw",
}


@dataclass(frozen=True)
class TestDatabase:
    admin_url: str
    host: str
    port: int
    container_id: str | None = None

    def url_for(self, role: str, password: str, dbname: str = DB_NAME) -> str:
        return f"postgresql+psycopg://{role}:{password}@{self.host}:{self.port}/{dbname}"

    def create_fresh(self, dbname: str) -> str:
        """Create and bootstrap an empty database; return its migrator URL (not migrated)."""
        engine = create_engine(self.admin_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        engine.dispose()
        if self.container_id:
            run_bootstrap_in_container(self.container_id, dbname)
        else:
            admin = make_url(self.admin_url).set(database=dbname)
            run_bootstrap_with_local_psql(admin.render_as_string(hide_password=False))
        return self.url_for("sos_migrator", PASSWORDS["migrator_password"], dbname)

    @property
    def app_url(self) -> str:
        return self.url_for("sos_app", PASSWORDS["app_password"])

    @property
    def platform_url(self) -> str:
        return self.url_for("sos_platform", PASSWORDS["platform_password"])

    @property
    def migrator_url(self) -> str:
        return self.url_for("sos_migrator", PASSWORDS["migrator_password"])

    @property
    def readonly_url(self) -> str:
        return self.url_for("sos_readonly", PASSWORDS["readonly_password"])


def _psql_args() -> list[str]:
    args = ["psql", "-v", "ON_ERROR_STOP=1", "-q"]
    for key, value in PASSWORDS.items():
        args += ["-v", f"{key}={value}"]
    return args


def run_bootstrap_in_container(container_id: str, dbname: str = DB_NAME) -> None:
    sql = BOOTSTRAP_SQL.read_text(encoding="utf-8")
    subprocess.run(
        ["docker", "exec", "-i", container_id, *_psql_args(), "-U", "postgres", "-d", dbname],
        input=sql.encode(),
        check=True,
        capture_output=True,
    )


def run_bootstrap_with_local_psql(admin_url: str) -> None:
    if shutil.which("psql") is None:
        raise RuntimeError("SOS_TEST_ADMIN_DATABASE_URL requires psql on PATH")
    url = make_url(admin_url).set(drivername="postgresql")
    subprocess.run(
        [*_psql_args(), url.render_as_string(hide_password=False), "-f", str(BOOTSTRAP_SQL)],
        check=True,
        capture_output=True,
    )


def alembic_config(migrator_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["url"] = migrator_url
    return cfg


def upgrade_head(migrator_url: str) -> None:
    command.upgrade(alembic_config(migrator_url), "head")


@pytest.fixture(scope="session")
def test_database() -> Iterator[TestDatabase]:
    external = os.environ.get("SOS_TEST_ADMIN_DATABASE_URL")
    if external:
        url = make_url(external)
        db = TestDatabase(external, url.host or "localhost", url.port or 5432)
        run_bootstrap_with_local_psql(external)
        upgrade_head(db.migrator_url)
        yield db
        return

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        PG_IMAGE, username="postgres", password="test-admin-pw", dbname=DB_NAME, driver="psycopg"
    )
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(5432))
        admin_url = f"postgresql+psycopg://postgres:test-admin-pw@{host}:{port}/{DB_NAME}"
        run_bootstrap_in_container(container.get_wrapped_container().id)
        db = TestDatabase(admin_url, host, port, container.get_wrapped_container().id)
        upgrade_head(db.migrator_url)
        yield db


@pytest.fixture(scope="session")
def admin_engine(test_database: TestDatabase) -> Iterator[Engine]:
    """Superuser engine: ONLY for test setup/teardown and tamper simulations."""
    engine = create_engine(test_database.admin_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(test_database: TestDatabase) -> Iterator[Engine]:
    engine = create_engine(test_database.app_url, future=True, pool_size=5)
    core_db.set_engine("app", engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def platform_engine(test_database: TestDatabase) -> Iterator[Engine]:
    engine = create_engine(test_database.platform_url, future=True, pool_size=2)
    core_db.set_engine("platform", engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def readonly_engine(test_database: TestDatabase) -> Iterator[Engine]:
    engine = create_engine(test_database.readonly_url, future=True, pool_size=2)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def make_alembic_config() -> type[Config] | object:
    return alembic_config


@pytest.fixture(autouse=True)
def _full_rate_limit_budgets() -> None:
    """Every test starts with full rate-limit budgets, like a fresh minute. Limiting itself stays
    ON in tests (tests/core/test_ratelimit.py proves it); only the per-process counters of the
    previous test are forgotten, so suites that share one synthetic school do not add up."""
    limiter = ratelimit.get_rate_limiter()
    if isinstance(limiter.store, ratelimit.InMemoryRateLimitStore):
        limiter.store.reset()
    limiter.fallback.reset()


@pytest.fixture
def tenant_ids() -> tuple[uuid.UUID, uuid.UUID]:
    """Two fresh synthetic tenant IDs (A, B) for cross-tenant tests."""
    return uuid.uuid4(), uuid.uuid4()


@contextlib.contextmanager
def _coverage_paused() -> Iterator[None]:
    """Stop coverage tracing (``make test-api`` runs with ``--cov``) for a timed block.

    Branch tracing adds ~25% CPU time to tight Python loops, and more when the host is busy, so
    a CPU-time budget measured under ``--cov`` measures the tracer, not the code. The block's
    lines are covered by the functional tests; the budget itself is unchanged.
    """
    import coverage

    cov = coverage.Coverage.current()
    if cov is None:
        yield
        return
    cov.stop()
    try:
        yield
    finally:
        cov.start()


@pytest.fixture
def coverage_paused() -> Callable[[], contextlib.AbstractContextManager[None]]:
    """Context manager factory for timing tests: ``with coverage_paused(): ...``."""
    return _coverage_paused


def execute_admin(engine: Engine, sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql))


@pytest.fixture
def telugu_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch Telugu back on for one test (ADR-0036: ``SOS_TELUGU_ENABLED``, default off).

    The Telugu paths stay dormant, not deleted, so they keep their tests: a test that asserts
    Telugu output asks for this fixture. Every module reads the switch through
    ``app.core.languages``, so patching the settings that module sees is enough
    (``get_settings()`` itself is cached and shared).
    """
    from app.core import config, languages

    def settings_with_telugu() -> config.Settings:
        return config.get_settings().model_copy(update={"telugu_enabled": True})

    monkeypatch.setattr(languages, "get_settings", settings_with_telugu)

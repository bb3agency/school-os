"""``make restore-drill``: a local, repeatable backup-and-restore drill on SYNTHETIC data
(SEC-024, NFR-AVL-003, NFR-AVL-005; docs/10 §9 and §15.3; docs/11 R6, R11).

Usage::

    python -m app.devtools.restore_drill [--tenants N] [--students N] [--seed S]
                                         [--report-dir DIR] [--name NAME] [--no-report]

What it does, end to end, with throwaway Postgres containers only (testcontainers; both are
removed when the drill ends, nothing else on the machine is touched):

1. starts a scratch PostgreSQL (the image the test suite uses), runs ``infra/db/bootstrap.sql``
   as the admin, ``alembic upgrade head`` as ``sos_migrator`` and the audit partition step,
   exactly like ``make migrate``;
2. seeds synthetic schools through the module services (:mod:`app.devtools.seeder`: tenants,
   staff, students, guardians, enrolments, per-source values) and runs the data-quality checks
   for each school, then verifies every audit hash chain and records per-table row counts;
3. takes the backup the way ``deploy/dedicated/scripts/backup.sh`` does:
   ``pg_dump -U postgres -d schoolos -Fc -Z 6`` inside the database container, then
   ``pg_restore --list`` on the file and a SHA-256;
4. starts a second scratch PostgreSQL, bootstraps it like a fresh host and restores the dump
   the way ``restore.sh`` does (checksum check, ``CREATE DATABASE … TEMPLATE template0``,
   ``pg_restore --exit-on-error``, ``ANALYZE``);
5. verifies the copy: Alembic revision, exact per-table row counts, RLS ENABLE + FORCE on every
   tenant table (and the whole RLS/policy catalog equal to the source), no ``sos_*`` role with
   BYPASSRLS or SUPERUSER, every audit chain (schools and platform) verifies, a
   ``tenant_session`` read returns the school's rows and another school's context returns none;
6. records timings (backup, restore, verify) against the RTO targets (shared tier 4 h, dedicated
   tier 8 h). RPO depends on WAL archiving / PITR and cannot be measured by a local drill;
7. writes a Markdown report and a JSON file (``<report-dir>/<name>.md`` / ``.json``).

Refuses to run unless ``SOS_ENV`` is ``local`` or ``ci`` (exit code 2), like seed-synthetic.
Exit codes: 0 every check passed, 1 a check failed or the drill errored, 2 refused / bad args.
The report holds IDs, table names, counts and timings only (no names, no personal data).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import platform as host_platform
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TextIO

from pydantic import SecretStr, ValidationError

from app.core.config import Environment, KeyWrapperKind, Settings, get_settings

if TYPE_CHECKING:
    from sqlalchemy import Engine

ALLOWED_ENVS: Final = frozenset({Environment.LOCAL, Environment.CI})
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2
REFUSED_MESSAGE: Final = "restore-drill is disabled outside local/ci (SOS_ENV)\n"

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
BOOTSTRAP_SQL: Final = REPO_ROOT / "infra" / "db" / "bootstrap.sql"
ALEMBIC_INI: Final = REPO_ROOT / "apps" / "api" / "alembic.ini"
PG_IMAGE: Final = "pgvector/pgvector:0.8.6-pg16-bookworm"  # same as apps/api/tests/conftest.py
DB_NAME: Final = "schoolos"
ADMIN_PASSWORD: Final = f"drill-{secrets.token_hex(12)}"  # throwaway container only

APP_SCHEMAS: Final = ("core", "sis", "kb", "audit", "ops", "fin", "msg", "platform")
TENANT_SCHEMAS: Final = ("core", "sis", "kb", "audit", "ops", "fin", "msg")
GLOBAL_RLS_TABLES: Final = ("core.tenants", "core.users")  # no tenant_id column, RLS by variant

RTO_SHARED_S: Final = 4 * 3600  # docs/10 §10: data corruption / bad migration, RTO ≤ 4 h
RTO_DEDICATED_S: Final = 8 * 3600  # NFR-AVL-005, docs/10 §15.3: RTO ≤ 8 h
DEFAULT_TENANTS: Final = 2
DEFAULT_STUDENTS: Final = 400
DEFAULT_SEED: Final = 20261101
DEFAULT_REPORT_DIR: Final = "docs/security/drills"


# --- result types ------------------------------------------------------------------------------


@dataclasses.dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclasses.dataclass
class DrillResult:
    started_at: str
    environment: dict[str, str]
    dataset: dict[str, Any]
    timings_s: dict[str, float] = dataclasses.field(default_factory=dict)
    dump: dict[str, Any] = dataclasses.field(default_factory=dict)
    source: dict[str, Any] = dataclasses.field(default_factory=dict)
    restored: dict[str, Any] = dataclasses.field(default_factory=dict)
    checks: list[Check] = dataclasses.field(default_factory=list)
    rto: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.checks) and all(c.ok for c in self.checks)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    def as_json(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["ok"] = self.ok
        return data


# --- containers and SQL helpers ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Scratch:
    """One throwaway Postgres container and the role passwords bootstrapped into it."""

    container_id: str
    host: str
    port: int
    passwords: dict[str, str]

    def url(self, role: str, dbname: str = DB_NAME) -> str:
        password = ADMIN_PASSWORD if role == "postgres" else self.passwords[_PW_KEY[role]]
        return f"postgresql+psycopg://{role}:{password}@{self.host}:{self.port}/{dbname}"


_PW_KEY: Final = {
    "sos_app": "app_password",
    "sos_migrator": "migrator_password",
    "sos_platform": "platform_password",
    "sos_readonly": "readonly_password",
}


@contextmanager
def scratch_postgres(passwords: dict[str, str]) -> Iterator[Scratch]:
    """Start a throwaway container (removed on exit, also by the testcontainers reaper)."""
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415 - dev dependency

    container = PostgresContainer(
        PG_IMAGE, username="postgres", password=ADMIN_PASSWORD, dbname=DB_NAME, driver="psycopg"
    )
    with container:
        yield Scratch(
            container_id=container.get_wrapped_container().id,
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            passwords=passwords,
        )


def _docker_exec(
    scratch: Scratch,
    args: Sequence[str],
    *,
    stdin: Any = None,
    stdout: Any = subprocess.PIPE,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """``docker exec -i <container> <args>`` (the scratch container only; fixed argv)."""
    return subprocess.run(  # noqa: S603 - fixed argv, container started by this drill
        ["docker", "exec", "-i", scratch.container_id, *args],  # noqa: S607
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        input=input_bytes,
        check=True,
    )


def bootstrap(scratch: Scratch, dbname: str = DB_NAME) -> None:
    """``infra/db/bootstrap.sql`` as the admin (what the db init script does on a new host)."""
    args = ["psql", "-v", "ON_ERROR_STOP=1", "-q"]
    for key, value in scratch.passwords.items():
        args += ["-v", f"{key}={value}"]
    sql = BOOTSTRAP_SQL.read_text(encoding="utf-8").encode()
    _docker_exec(scratch, [*args, "-U", "postgres", "-d", dbname], input_bytes=sql)


def migrate(scratch: Scratch) -> str:
    """``alembic upgrade head`` + audit partitions as ``sos_migrator`` (``make migrate``)."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415
    from sqlalchemy import create_engine  # noqa: PLC0415

    from app.audit.partitions import ensure_partitions  # noqa: PLC0415

    url = scratch.url("sos_migrator")
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            ensure_partitions(conn)
    finally:
        engine.dispose()
    return script_head()


def script_head() -> str:
    from alembic.config import Config  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    if head is None:
        raise RuntimeError("no alembic head")
    return head


def _qualified(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def snapshot(admin: Engine) -> dict[str, Any]:
    """Catalog facts compared between the source and the restored copy (admin connection, so
    RLS does not filter the counts)."""
    from sqlalchemy import text  # noqa: PLC0415

    with admin.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT n.nspname AS schema, c.relname AS name, c.relrowsecurity AS rls, "
                "c.relforcerowsecurity AS force, "
                "EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid "
                "        AND a.attname = 'tenant_id' AND NOT a.attisdropped) AS has_tenant, "
                "ARRAY(SELECT p.polname::text FROM pg_policy p WHERE p.polrelid = c.oid "
                "      ORDER BY 1) AS policies, c.relkind "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r', 'p') ORDER BY 1, 2"
            ),
            {"schemas": list(APP_SCHEMAS)},
        ).mappings()
        rows: dict[str, int] = {}
        catalog: dict[str, dict[str, Any]] = {}
        for t in list(tables):
            fq = f"{t['schema']}.{t['name']}"
            catalog[fq] = {
                "rls": bool(t["rls"]),
                "force": bool(t["force"]),
                "has_tenant": bool(t["has_tenant"]),
                "policies": list(t["policies"]),
            }
            if t["relkind"] == "r":  # leaf tables only: a partitioned parent repeats its children
                count: object = conn.execute(
                    text(f"SELECT count(*) FROM {_qualified(t['schema'], t['name'])}")  # noqa: S608 - catalog names
                ).scalar_one()
                rows[fq] = int(str(count))
        revision: object = conn.execute(
            text("SELECT version_num FROM ops.alembic_version")
        ).scalar_one()
        bad_roles: list[str] = list(
            conn.execute(
                text(
                    "SELECT rolname FROM pg_roles WHERE rolname LIKE 'sos\\_%' "
                    "AND (rolbypassrls OR rolsuper) ORDER BY 1"
                )
            ).scalars()
        )
        server: object = conn.execute(text("SHOW server_version")).scalar_one()
    return {
        "alembic_revision": str(revision),
        "rows": rows,
        "catalog": catalog,
        "bad_roles": bad_roles,
        "server_version": str(server),
    }


def rls_problems(catalog: dict[str, dict[str, Any]]) -> list[str]:
    """Tenant tables (a ``tenant_id`` column in a tenant schema, or core.tenants/core.users)
    without ENABLE + FORCE RLS or without any policy."""
    problems: list[str] = []
    for fq, facts in catalog.items():
        schema = fq.split(".", 1)[0]
        tenant_table = (schema in TENANT_SCHEMAS and facts["has_tenant"]) or fq in GLOBAL_RLS_TABLES
        if not tenant_table:
            continue
        if not (facts["rls"] and facts["force"]):
            problems.append(f"{fq}: RLS not ENABLED+FORCED")
        if not facts["policies"]:
            problems.append(f"{fq}: no policy")
    return problems


def tenant_table_count(catalog: dict[str, dict[str, Any]]) -> int:
    return sum(
        1
        for fq, f in catalog.items()
        if (fq.split(".", 1)[0] in TENANT_SCHEMAS and f["has_tenant"]) or fq in GLOBAL_RLS_TABLES
    )


def verify_chains(app: Engine, platform_engine: Engine) -> tuple[bool, int, int, list[str]]:
    """Every school chain and the platform chain (``app.audit.verify_all``, as restore.sh)."""
    from app.audit import verify_all  # noqa: PLC0415

    tenants = verify_all.list_tenant_ids(engine=app)
    results = verify_all.verify_all(tenants, engine=app)
    platform_result = verify_all.verify_platform(engine=platform_engine)
    broken = [f"{t}: {r.reason}" for t, r in results.items() if not r.ok]
    if not platform_result.ok:
        broken.append(f"platform: {platform_result.reason}")
    checked = sum(r.checked for r in results.values()) + platform_result.checked
    return not broken, len(tenants), checked, broken


def tenant_reads(app: Engine, tenant_ids: Sequence[uuid.UUID]) -> dict[str, int]:
    """Students visible as sos_app: own context, another school's context, an unknown school
    and no context at all (the last three must be 0 for the first school's rows)."""
    from sqlalchemy import text  # noqa: PLC0415

    from app.core.db import tenant_session  # noqa: PLC0415

    first, other = tenant_ids[0], tenant_ids[1] if len(tenant_ids) > 1 else uuid.uuid4()
    count_all = text("SELECT count(*) FROM sis.students")
    count_first = text("SELECT count(*) FROM sis.students WHERE tenant_id = :t")
    out: dict[str, int] = {}
    with tenant_session(first, engine=app) as s:
        out["own_school"] = int(s.execute(count_all).scalar_one())
    with tenant_session(other, engine=app) as s:
        out["other_school_sees_first"] = int(s.execute(count_first, {"t": first}).scalar_one())
    with tenant_session(uuid.uuid4(), engine=app) as s:
        out["unknown_school_sees_any"] = int(s.execute(count_all).scalar_one())
    with app.connect() as conn:  # no app.tenant_id at all: RLS fails closed
        out["no_context_sees_any"] = int(conn.execute(count_all).scalar_one())
    return out


# --- the drill ---------------------------------------------------------------------------------


def _passwords() -> dict[str, str]:
    return {k: f"drill-{secrets.token_hex(12)}" for k in _PW_KEY.values()}


def _seed(
    settings: Settings, tenants: int, students: int, seed: int, prefix: str
) -> list[uuid.UUID]:
    """Synthetic schools through the module services, then a DQ run per school."""
    from app.authz.kv import InMemoryKV, set_kv_store  # noqa: PLC0415
    from app.core.crypto import LocalDevKeyWrapper  # noqa: PLC0415
    from app.core.db import tenant_session  # noqa: PLC0415
    from app.devtools import seeder  # noqa: PLC0415
    from app.devtools.plan import build_plan  # noqa: PLC0415
    from app.devtools.students import build_students  # noqa: PLC0415
    from app.dq import service as dq  # noqa: PLC0415
    from app.students import crypto  # noqa: PLC0415

    wrapper = LocalDevKeyWrapper(settings)
    set_kv_store(InMemoryKV())
    crypto.set_key_wrapper(wrapper)
    plan = build_plan(seed=seed, tenants=tenants, code_prefix=prefix)
    options = seeder.StudentOptions(
        build=lambda t: build_students(
            t, dataset_version=plan.dataset_version, seed=seed, profile="small", count=students
        ),
        pages=lambda t, school: [],
        documents=False,  # files live in S3, not in the database dump (docs/10 §15.3)
    )
    summary = seeder.seed(
        plan, wrapper=wrapper, owner_bootstrap=seeder.PlatformOwnerBootstrap(), students=options
    )
    for t in plan.tenants:
        spec = next(s for s in t.staff if s.role == seeder.STUDENT_SEEDER_ROLE and s.ordinal == 1)
        with tenant_session(t.tenant_id) as session:
            member = seeder._members_by_email(session)[spec.email]
        ctx = seeder._member_context(t.tenant_id, member)
        with tenant_session(t.tenant_id, member.id) as session:
            dq.run_checks(session, ctx)
    return [t.tenant_id for t in summary.tenants]


@contextmanager
def _engines(scratch: Scratch, dbname: str) -> Iterator[tuple[Engine, Engine, Engine]]:
    from sqlalchemy import create_engine  # noqa: PLC0415

    from app.core import db as core_db  # noqa: PLC0415

    admin = create_engine(scratch.url("postgres", dbname), pool_size=1)
    app = create_engine(scratch.url("sos_app", dbname), pool_size=2)
    plat = create_engine(scratch.url("sos_platform", dbname), pool_size=1)
    core_db.set_engine("app", app)
    core_db.set_engine("platform", plat)
    try:
        yield admin, app, plat
    finally:
        for e in (admin, app, plat):
            e.dispose()


def _timed(result: DrillResult, key: str, started: float) -> None:
    result.timings_s[key] = round(time.perf_counter() - started, 2)


def backup(scratch: Scratch, work: Path) -> tuple[Path, str]:
    """As backup.sh: ``pg_dump -U postgres -d schoolos -Fc -Z 6`` in the db container, then
    ``pg_restore --list`` on the file, then sha256."""
    file = work / f"schoolos-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}-drill.dump"
    with file.open("wb") as out:
        _docker_exec(
            scratch, ["pg_dump", "-U", "postgres", "-d", DB_NAME, "-Fc", "-Z", "6"], stdout=out
        )
    with file.open("rb") as dump:
        _docker_exec(scratch, ["pg_restore", "--list"], stdin=dump)
    return file, hashlib.sha256(file.read_bytes()).hexdigest()


def restore(scratch: Scratch, file: Path, sha256: str, dbname: str) -> None:
    """As restore.sh (drill path): checksum, CREATE DATABASE … TEMPLATE template0,
    ``pg_restore --exit-on-error`` from stdin, ANALYZE."""
    if hashlib.sha256(file.read_bytes()).hexdigest() != sha256:
        raise RuntimeError("checksum mismatch for the dump")
    psql = ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres"]
    _docker_exec(
        scratch, [*psql, "-d", "postgres", "-c", f'CREATE DATABASE "{dbname}" TEMPLATE template0']
    )
    with file.open("rb") as dump:
        _docker_exec(
            scratch, ["pg_restore", "-U", "postgres", "-d", dbname, "--exit-on-error"], stdin=dump
        )
    _docker_exec(scratch, [*psql, "-d", dbname, "-c", "ANALYZE"])


def run_drill(
    settings: Settings,
    *,
    tenants: int = DEFAULT_TENANTS,
    students: int = DEFAULT_STUDENTS,
    seed: int = DEFAULT_SEED,
    log: TextIO | None = None,
) -> DrillResult:
    """Run the whole drill; never raises (a failure is recorded in the result)."""
    err = log or sys.stderr
    now = dt.datetime.now(dt.UTC)
    result = DrillResult(
        started_at=now.isoformat(timespec="seconds"),
        environment={
            "sos_env": str(settings.env),
            "postgres_image": PG_IMAGE,
            "host_os": host_platform.platform(terse=True),
            "python": host_platform.python_version(),
            "git_commit": _git_commit(),
        },
        dataset={
            "synthetic": True,
            "tenants": tenants,
            "students_per_school": students,
            "seed": seed,
        },
    )

    def step(msg: str) -> None:
        err.write(f"restore-drill: {msg}\n")
        err.flush()

    try:
        with tempfile.TemporaryDirectory(prefix="sos-restore-drill-") as tmp:
            source = _source_phase(result, settings, Path(tmp), step, now)
            _target_phase(result, source, step, now)
    except Exception as exc:  # recorded in the report; the containers are already gone
        result.error = f"{type(exc).__name__}: {_short(exc)}"
        step(f"FAILED: {result.error}")
    _rto(result)
    return result


@dataclasses.dataclass(frozen=True)
class _Source:
    snap: dict[str, Any]
    head: str
    tenant_ids: list[uuid.UUID]
    reads: dict[str, int]
    dump: Path
    sha256: str


def _chain_detail(n: int, checked: int, broken: list[str]) -> str:
    return f"{n} school chains + platform, {checked} events" + (f"; {broken}" if broken else "")


def _source_phase(
    result: DrillResult,
    settings: Settings,
    work: Path,
    step: Callable[[str], None],
    now: dt.datetime,
) -> _Source:
    """Provision, seed, verify, snapshot and back up the source database."""
    tenants = int(result.dataset["tenants"])
    students = int(result.dataset["students_per_school"])
    step("starting the source database (throwaway container)")
    with scratch_postgres(_passwords()) as src:
        t = time.perf_counter()
        bootstrap(src)
        head = migrate(src)
        _timed(result, "source_provision", t)
        with _engines(src, DB_NAME) as (admin, app, plat):
            step(f"seeding {tenants} synthetic schools x {students} students")
            t = time.perf_counter()
            tenant_ids = _seed(
                settings, tenants, students, int(result.dataset["seed"]), f"drill{now:%m%d%H%M}"
            )
            _timed(result, "seed", t)
            ok, n, checked, broken = verify_chains(app, plat)
            result.check("source audit chains verify", ok, _chain_detail(n, checked, broken))
            snap = snapshot(admin)
            reads = tenant_reads(app, tenant_ids)
        result.source = _summary(snap, head)
        result.source["tenant_reads"] = reads
        result.dataset["tenant_ids"] = [str(x) for x in tenant_ids]

        step("backup (pg_dump -Fc -Z 6, as backup.sh)")
        t = time.perf_counter()
        file, sha = backup(src, work)
        _timed(result, "backup", t)
        result.dump = {"bytes": file.stat().st_size, "sha256": sha, "format": "custom (-Fc -Z 6)"}
    return _Source(snap, head, tenant_ids, reads, file, sha)


def _target_phase(
    result: DrillResult, source: _Source, step: Callable[[str], None], now: dt.datetime
) -> None:
    """Provision a fresh host, restore the dump as restore.sh does and verify the copy."""
    step("starting the target database (throwaway container, fresh-host bootstrap)")
    with scratch_postgres(_passwords()) as dst:
        t = time.perf_counter()
        bootstrap(dst)
        _timed(result, "target_provision", t)
        dbname = f"schoolos_drill_{now:%Y%m%dT%H%M%SZ}".lower()
        step(f"restore into {dbname} (as restore.sh)")
        t = time.perf_counter()
        restore(dst, source.dump, source.sha256, dbname)
        _timed(result, "restore", t)

        step("verifying the restored copy")
        t = time.perf_counter()
        with _engines(dst, dbname) as (admin, app, plat):
            restored = snapshot(admin)
            _compare(result, source.snap, restored, source.head)
            ok, n, checked, broken = verify_chains(app, plat)
            result.check(
                "audit chains verify on the copy",
                ok and n == len(source.tenant_ids),
                _chain_detail(n, checked, broken),
            )
            reads = tenant_reads(app, source.tenant_ids)
            result.check(
                "tenant_session isolation on the copy",
                reads["own_school"] == source.reads["own_school"] > 0
                and reads["other_school_sees_first"] == 0
                and reads["unknown_school_sees_any"] == 0
                and reads["no_context_sees_any"] == 0,
                json.dumps(reads, sort_keys=True),
            )
        _timed(result, "verify", t)
    result.restored = _summary(restored, source.head)
    result.restored["tenant_reads"] = reads


def _short(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return f"exit {exc.returncode}: {' | '.join(stderr[-5:])}"
    return str(exc).splitlines()[0][:500] if str(exc) else ""


def _summary(snap: dict[str, Any], head: str) -> dict[str, Any]:
    return {
        "alembic_revision": snap["alembic_revision"],
        "alembic_script_head": head,
        "server_version": snap["server_version"],
        "tables": len(snap["rows"]),
        "tenant_tables": tenant_table_count(snap["catalog"]),
        "total_rows": sum(snap["rows"].values()),
        "rows": snap["rows"],
    }


def _compare(result: DrillResult, src: dict[str, Any], dst: dict[str, Any], head: str) -> None:
    result.check(
        "alembic revision equal",
        src["alembic_revision"] == dst["alembic_revision"] == head,
        f"source {src['alembic_revision']}, copy {dst['alembic_revision']}, head {head}",
    )
    diff = sorted(
        f"{k}: {src['rows'].get(k)} -> {dst['rows'].get(k)}"
        for k in set(src["rows"]) | set(dst["rows"])
        if src["rows"].get(k) != dst["rows"].get(k)
    )
    result.check(
        "per-table row counts equal",
        not diff,
        f"{len(dst['rows'])} tables, {sum(dst['rows'].values())} rows"
        + (f"; differences: {diff[:10]}" if diff else ""),
    )
    problems = rls_problems(dst["catalog"])
    result.check(
        "RLS ENABLE+FORCE on every tenant table",
        not problems and tenant_table_count(dst["catalog"]) > 0,
        f"{tenant_table_count(dst['catalog'])} tenant tables"
        + (f"; {problems[:10]}" if problems else ""),
    )
    catalog_diff = sorted(
        k
        for k in set(src["catalog"]) | set(dst["catalog"])
        if src["catalog"].get(k) != dst["catalog"].get(k)
    )
    result.check(
        "RLS flags and policies equal to the source",
        not catalog_diff,
        f"{len(dst['catalog'])} tables compared"
        + (f"; differ: {catalog_diff[:10]}" if catalog_diff else ""),
    )
    result.check(
        "no sos_* role has BYPASSRLS or SUPERUSER",
        not dst["bad_roles"] and not src["bad_roles"],
        f"offending roles: {dst['bad_roles'] or 'none'}",
    )


def _rto(result: DrillResult) -> None:
    t = result.timings_s
    recovery = round(t.get("restore", 0.0) + t.get("verify", 0.0), 2)
    result.rto = {
        "measured_recovery_s": recovery,
        "measured_as": "restore + verify (download, host rebuild and app restart not included)",
        "shared_target_s": RTO_SHARED_S,
        "dedicated_target_s": RTO_DEDICATED_S,
        "within_shared": result.error is None and recovery <= RTO_SHARED_S,
        "within_dedicated": result.error is None and recovery <= RTO_DEDICATED_S,
        "rpo": "not measurable locally: depends on RDS PITR (shared) and WAL-G archiving "
        "(dedicated, archive_timeout 5 min); a nightly pg_dump alone gives RPO 24 h",
    }


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip()


# --- report ------------------------------------------------------------------------------------


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


def render_markdown(result: DrillResult) -> str:
    t = result.timings_s
    lines = [
        f"# Local restore drill, {result.started_at[:10]}",
        "",
        "> **Local drill on synthetic data.** Two throwaway PostgreSQL containers on a developer "
        "workstation, seeded by `app.devtools.seeder` (no real school data). It proves the backup "
        "format, the restore procedure and the integrity checks of `deploy/dedicated/scripts/"
        "backup.sh` / `restore.sh`; it is **not** the staging PITR drill or the dedicated-host "
        'WAL-G drill (see "Still to do by ops").',
        "",
        f"Result: **{'PASS' if result.ok else 'FAIL'}**"
        + (f" (error: `{result.error}`)" if result.error else ""),
        "",
        "Requirements: SEC-024, NFR-AVL-003, NFR-AVL-005 (docs/10 §9, §10, §15.3; docs/11 R6, "
        "R11). Generated by `make restore-drill` (`python -m app.devtools.restore_drill`).",
        "",
        "## Set-up",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Started (UTC) | {result.started_at} |",
        f"| Commit | `{result.environment.get('git_commit')}` |",
        f"| PostgreSQL image | `{result.environment.get('postgres_image')}` "
        f"(server {result.source.get('server_version', '?')}) |",
        f"| Host | {result.environment.get('host_os')}, "
        f"Python {result.environment.get('python')} |",
        f"| Dataset | synthetic, {result.dataset.get('tenants')} schools x "
        f"{result.dataset.get('students_per_school')} students, "
        f"seed {result.dataset.get('seed')} |",
        f"| Alembic head | `{result.source.get('alembic_script_head', '?')}` |",
        f"| Tables (leaf) | {result.source.get('tables', '?')} (tenant tables with RLS: "
        f"{result.source.get('tenant_tables', '?')}) |",
        f"| Rows (all tables) | {result.source.get('total_rows', '?')} |",
        f"| Dump | {result.dump.get('bytes', '?')} bytes, sha256 "
        f"`{str(result.dump.get('sha256', '?'))[:16]}…` |",
        "",
        "## Procedure",
        "",
        "1. Source: throwaway container, `infra/db/bootstrap.sql`, `alembic upgrade head` as "
        "`sos_migrator`, audit partitions (as `make migrate`).",
        "2. Seed synthetic schools through the module services, run the data-quality checks, "
        "verify every audit chain, record exact per-table row counts (as `postgres`).",
        "3. Backup as `backup.sh`: `pg_dump -U postgres -d schoolos -Fc -Z 6` inside the "
        "container, `pg_restore --list` on the file, SHA-256.",
        "4. Target: a second throwaway container bootstrapped like a fresh host (bootstrap.sql "
        "creates the roles; a dump carries no roles).",
        "5. Restore as `restore.sh` (drill path): checksum, `CREATE DATABASE … TEMPLATE "
        "template0`, `pg_restore --exit-on-error`, `ANALYZE`.",
        "6. Verify the copy (checks below).",
        "",
        "## Checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
        *[
            f"| {c.name} | {'pass' if c.ok else '**FAIL**'} | {c.detail.replace('|', '/')} |"
            for c in result.checks
        ],
        "",
        "## Timings",
        "",
        "| Step | Time |",
        "|---|---|",
        *[f"| {k.replace('_', ' ')} | {_fmt_s(v)} |" for k, v in t.items()],
        "",
        "## RTO and RPO",
        "",
        f"- Measured recovery (restore + verify): **{_fmt_s(result.rto['measured_recovery_s'])}**.",
        f"- Shared tier RTO ≤ 4 h (docs/10 §10): "
        f"{'within target' if result.rto['within_shared'] else '**not met**'}.",
        f"- Dedicated tier RTO ≤ 8 h (NFR-AVL-005): "
        f"{'within target' if result.rto['within_dedicated'] else '**not met**'}.",
        "- The local number excludes downloading the dump from S3 (ap-south-2), rebuilding a "
        "host with Terraform, restarting the stack and switching the connection secret, and it "
        "uses a small synthetic database; it is a lower bound, not RTO evidence for production.",
        "- **RPO cannot be measured locally.** It depends on RDS PITR (shared tier) and WAL-G "
        "continuous archiving with `archive_timeout` 5 min (dedicated tier); a nightly `pg_dump` "
        "alone gives RPO 24 h.",
        "",
        "## Row counts (tables with rows)",
        "",
        "| Table | Source | Restored |",
        "|---|---:|---:|",
        *[
            f"| `{k}` | {v} | {result.restored.get('rows', {}).get(k, '?')} |"
            for k, v in sorted(result.source.get("rows", {}).items())
            if v
        ],
        "",
        "Every other table had 0 rows on both sides; the JSON file next to this report has all "
        "tables.",
        "",
        "## Still to do by ops (not covered by a local drill)",
        "",
        "1. **Staging PITR drill (shared tier, docs/10 §9, docs/11 R6):** choose a PITR "
        "timestamp, restore to a new RDS instance in the staging account (via snapshot share), "
        "run the integrity checks (row counts, `python -m app.audit.verify_all` against the new "
        "instance), switch the connection via a secret update in a rehearsal, run post-restore "
        "checks, record the timings against RPO ≤ 15 min / RTO ≤ 4 h.",
        "2. **Dedicated-host drill (docs/10 §15.3, docs/11 R11):** on a scratch host, "
        "`sudo deploy/dedicated/scripts/restore.sh --latest` (logical dump drill; report under "
        "`s3://<backup bucket>/restore-drills/`), and a WAL-G point-in-time restore to a scratch "
        "host rebuilt from Terraform (RPO ≤ 15 min, RTO ≤ 8 h) before go-live and quarterly.",
        "3. **Cross-region:** restore from the ap-south-2 snapshot copy / backup bucket (docs/10 "
        "§10 region impairment) and confirm the KMS keys there can decrypt.",
        "4. **Files:** restore a file version from the locked replica bucket (docs/10 §10).",
        "5. Record each result under `docs/security/drills/` with this template.",
        "",
    ]
    return "\n".join(lines)


def write_report(result: DrillResult, report_dir: Path, name: str) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    md, js = report_dir / f"{name}.md", report_dir / f"{name}.json"
    md.write_text(render_markdown(result), encoding="utf-8", newline="\n")
    js.write_text(
        json.dumps(result.as_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return md, js


# --- CLI ---------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.devtools.restore_drill",
        description="Local backup/restore drill on SYNTHETIC data (local/ci only).",
    )
    p.add_argument("--tenants", type=int, default=DEFAULT_TENANTS, help="synthetic schools (>=2)")
    p.add_argument("--students", type=int, default=DEFAULT_STUDENTS, help="students per school")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed")
    p.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="where the report is written")
    p.add_argument(
        "--name",
        default=None,
        help="report file name without extension (default <UTC date>-local-restore-drill)",
    )
    p.add_argument("--no-report", action="store_true", help="print the JSON only")
    return p


def _allowed(settings: Settings | None) -> bool:
    if settings is None:
        try:
            settings = get_settings()
        except ValidationError:
            return False  # e.g. staging/prod guards rejected the configuration: fail closed
    return settings.env in ALLOWED_ENVS


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if not _allowed(settings):
        err.write(REFUSED_MESSAGE)
        return EXIT_REFUSED
    base = settings or get_settings()
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_REFUSED if exc.code else EXIT_OK
    if args.tenants < 2 or not 1 <= args.students <= 2000:
        err.write("restore-drill: --tenants must be >= 2 and --students 1..2000\n")
        return EXIT_REFUSED
    # The scratch databases need a local key wrapper; the key never leaves this process.
    drill_settings = base.model_copy(
        update={
            "key_wrapper": KeyWrapperKind.LOCAL_DEV,
            "local_dev_master_key": SecretStr(f"restore-drill-{secrets.token_hex(16)}"),
        }
    )
    result = run_drill(
        drill_settings, tenants=args.tenants, students=args.students, seed=args.seed, log=err
    )
    summary: dict[str, Any] = {
        "ok": result.ok,
        "error": result.error,
        "timings_s": result.timings_s,
        "checks": {c.name: c.ok for c in result.checks},
    }
    if not args.no_report:
        name = args.name or f"{result.started_at[:10]}-local-restore-drill"
        md, js = write_report(result, Path(args.report_dir), name)
        summary["report"] = [str(md), str(js)]
    out.write(json.dumps(summary, sort_keys=True) + "\n")
    return EXIT_OK if result.ok else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())

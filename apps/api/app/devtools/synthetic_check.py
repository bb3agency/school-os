"""``make synthetic-check``: read-only report of data created by ``make seed-synthetic``
(docs/12 §3; CLAUDE.md invariant 11; docs/14 §3 "synthetic data purged").

Usage::

    python -m app.devtools.synthetic_check [--database-url URL] [--json] [--fail-if-found]

Lists every school (any status, through the allowlisted ``core.list_tenant_ids``) and, per school
inside its own tenant transaction (RLS applies, as ``sos_app``), the rows that carry the
synthetic generator's markers (:mod:`app.devtools.plan`, :mod:`app.devtools.students`):

- the school itself: its id is the generator's UUIDv5 of ``(dataset_version, code)`` under
  :data:`app.devtools.plan.NAMESPACE`, or its name says "Synthetic" (every generated name does);
- staff: IdP subject ``synthetic|<code>|<role>|<n>`` or an e-mail under ``.example.invalid``;
- students: admission number ``SYN-<n>`` (:data:`app.devtools.students.ADMISSION_PREFIX`);
- documents (register pages, circulars) created by a synthetic staff member.

It never writes: the connection opens every transaction read only
(``default_transaction_read_only``) and each transaction is also set read only before its first
query, so an accidental write fails in the database. Output: school codes, ids and counts only
(no names of people). It does not need ``SOS_ENV`` to be local/ci: it is meant to run in staging
and prod before the first real data.

Exit codes: 0 no synthetic data (or found in local/ci, where it is expected); 1 synthetic data
found in staging/prod ("synthetic data present: purge before real data"), or anywhere with
``--fail-if-found``; 3 the check could not run (settings or database).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Final, TextIO

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Environment, Settings, get_settings
from app.core.db import context_free_session, tenant_session
from app.devtools.plan import DATASET_VERSIONS, EMAIL_DOMAIN, tenant_id_for
from app.devtools.students import ADMISSION_PREFIX
from app.tenancy import service as tenancy

EXIT_CLEAN: Final = 0
EXIT_FOUND: Final = 1
EXIT_ERROR: Final = 3
PRODUCTION_LIKE: Final = frozenset({Environment.STAGING, Environment.PROD})
STATEMENT_TIMEOUT_MS: Final = 30_000
SUBJECT_PREFIX: Final = "synthetic|"
NAME_MARKER: Final = "synthetic"
PURGE_MESSAGE: Final = "synthetic data present: purge before real data"

_READ_ONLY = text("SET LOCAL transaction_read_only = on")
_TENANT = text("SELECT code, name, status FROM core.tenants WHERE id = :t")
# LIKE patterns are bound parameters; "_" and "%" in the markers are escaped.
_STAFF = text(
    "SELECT count(*) AS total, "
    "count(*) FILTER (WHERE u.idp_subject LIKE :subject ESCAPE '\\' "
    "OR u.email ILIKE :email ESCAPE '\\') AS synthetic "
    "FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE m.tenant_id = :t"
)
_STUDENTS = text(
    "SELECT count(*) AS total, "
    "count(*) FILTER (WHERE admission_no LIKE :admission ESCAPE '\\') AS synthetic "
    "FROM sis.students WHERE tenant_id = :t"
)
_DOCUMENTS = text(
    "SELECT count(*) AS total, "
    "count(*) FILTER (WHERE d.created_by IN ("
    "  SELECT u.id FROM core.memberships m JOIN core.users u ON u.id = m.user_id "
    "  WHERE m.tenant_id = :t AND (u.idp_subject LIKE :subject ESCAPE '\\' "
    "  OR u.email ILIKE :email ESCAPE '\\'))) AS synthetic "
    "FROM kb.documents d WHERE d.tenant_id = :t"
)


def _like_prefix(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _like_suffix(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_PARAMS: Final = {
    "subject": _like_prefix(SUBJECT_PREFIX),
    "email": _like_suffix(f".{EMAIL_DOMAIN}"),
    "admission": _like_prefix(ADMISSION_PREFIX),
}


@dataclass(frozen=True, slots=True)
class Counts:
    total: int
    synthetic: int


@dataclass(frozen=True, slots=True)
class SchoolReport:
    tenant_id: str
    code: str
    status: str
    generator_id: bool
    """The id is the generator's UUIDv5 for this code."""
    synthetic_name: bool
    staff: Counts
    students: Counts
    documents: Counts

    @property
    def synthetic_school(self) -> bool:
        return self.generator_id or self.synthetic_name

    @property
    def has_synthetic_data(self) -> bool:
        rows = self.staff.synthetic + self.students.synthetic + self.documents.synthetic
        return self.synthetic_school or rows > 0


@dataclass(frozen=True, slots=True)
class Report:
    env: str
    schools: tuple[SchoolReport, ...]

    @property
    def flagged(self) -> tuple[SchoolReport, ...]:
        return tuple(s for s in self.schools if s.has_synthetic_data)


def _counts(row: object) -> Counts:
    mapping = row._mapping  # type: ignore[attr-defined]
    return Counts(total=int(mapping["total"]), synthetic=int(mapping["synthetic"]))


def _school(engine: Engine, tenant_id: uuid.UUID) -> SchoolReport | None:
    with tenant_session(
        tenant_id, engine=engine, statement_timeout_ms=STATEMENT_TIMEOUT_MS
    ) as session:
        session.execute(_READ_ONLY)
        tenant = session.execute(_TENANT, {"t": tenant_id}).first()
        if tenant is None:
            return None
        params = {"t": tenant_id, **_PARAMS}
        staff = _counts(session.execute(_STAFF, params).one())
        students = _counts(session.execute(_STUDENTS, params).one())
        documents = _counts(session.execute(_DOCUMENTS, params).one())
    code = str(tenant.code)
    return SchoolReport(
        tenant_id=str(tenant_id),
        code=code,
        status=str(tenant.status),
        generator_id=any(tenant_id == tenant_id_for(code, v) for v in DATASET_VERSIONS),
        synthetic_name=NAME_MARKER in str(tenant.name).casefold(),
        staff=staff,
        students=students,
        documents=documents,
    )


def scan(engine: Engine, env: Environment) -> Report:
    """Every school's synthetic markers, read only."""
    with context_free_session(engine=engine, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as s:
        s.execute(_READ_ONLY)
        tenant_ids = tenancy.list_tenant_ids(s, statuses=None)
    schools = [r for t in sorted(tenant_ids) if (r := _school(engine, t)) is not None]
    return Report(env=env.value, schools=tuple(schools))


def read_only_engine(url: str) -> Engine:
    """An engine whose every transaction starts read only (the database refuses writes)."""
    return create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        connect_args={"options": "-c default_transaction_read_only=on", "connect_timeout": 10},
    )


def _outcome(report: Report, env: Environment, *, fail_if_found: bool) -> tuple[int, str]:
    if not report.flagged:
        return EXIT_CLEAN, f"no synthetic data found ({len(report.schools)} schools checked)"
    if env in PRODUCTION_LIKE:
        return EXIT_FOUND, PURGE_MESSAGE
    if fail_if_found:
        return EXIT_FOUND, f"{PURGE_MESSAGE} (--fail-if-found)"
    return EXIT_CLEAN, f"synthetic data present ({env.value}: expected after make seed-synthetic)"


def render_text(report: Report, verdict: str, out: TextIO) -> None:
    out.write(f"synthetic-data check, SOS_ENV={report.env}, {len(report.schools)} schools\n")
    for s in report.flagged:
        markers = [
            m for m, on in (("generator id", s.generator_id), ("name", s.synthetic_name)) if on
        ]
        out.write(
            f"  {s.code} ({s.tenant_id}, {s.status}): school markers "
            f"[{', '.join(markers) or 'none'}]; synthetic "
            f"staff {s.staff.synthetic}/{s.staff.total}, "
            f"students {s.students.synthetic}/{s.students.total}, "
            f"documents {s.documents.synthetic}/{s.documents.total}\n"
        )
    out.write(f"result: {verdict}\n")


def render_json(report: Report, verdict: str, code: int, out: TextIO) -> None:
    data = {
        "env": report.env,
        "schools_checked": len(report.schools),
        "flagged": [{**asdict(s), "synthetic_school": s.synthetic_school} for s in report.flagged],
        "result": verdict,
        "exit_code": code,
    }
    out.write(json.dumps(data, sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.devtools.synthetic_check",
        description="Read-only: list schools and rows created by make seed-synthetic.",
    )
    p.add_argument(
        "--database-url",
        default=None,
        help="sos_app connection URL (default: SOS_DATABASE_URL from the settings)",
    )
    p.add_argument("--json", action="store_true", help="print one JSON document")
    p.add_argument(
        "--fail-if-found", action="store_true", help="exit 1 on synthetic data in local/ci too"
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_ERROR if exc.code else EXIT_CLEAN
    if settings is None:
        try:
            settings = get_settings()
        except ValidationError as exc:
            fields = ", ".join(str(e["loc"][0]) for e in exc.errors() if e.get("loc"))
            err.write(f"synthetic-check: settings are invalid ({fields or 'see guards'})\n")
            return EXIT_ERROR
    own_engine = engine is None
    if engine is None:
        url = args.database_url or settings.database_url.get_secret_value()
        engine = read_only_engine(url)
    try:
        report = scan(engine, settings.env)
    except SQLAlchemyError as exc:  # never echo the URL or the driver message (may hold secrets)
        err.write(f"synthetic-check: the database could not be read ({type(exc).__name__})\n")
        return EXIT_ERROR
    finally:
        if own_engine:
            engine.dispose()
    code, verdict = _outcome(report, settings.env, fail_if_found=args.fail_if_found)
    if args.json:
        render_json(report, verdict, code, out)
    else:
        render_text(report, verdict, out)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

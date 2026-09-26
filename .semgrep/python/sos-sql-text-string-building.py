# ruff: noqa
# mypy: ignore-errors
# Test fixture for `semgrep --test .semgrep`. Never imported or executed.
import sqlalchemy as sa
from sqlalchemy import text

table = "sis.students"
student_id = "0192f1c2-0000-7000-8000-000000000001"


def queries(session, conn, cur) -> None:
    # ruleid: sos-sql-text-string-building
    session.execute(text(f"SELECT * FROM sis.students WHERE id = '{student_id}'"))

    # ruleid: sos-sql-text-string-building
    text("SELECT * FROM %s" % table)

    # ruleid: sos-sql-text-string-building
    sa.text("SELECT * FROM {}".format(table))

    # ruleid: sos-sql-text-string-building
    text("SELECT * FROM " + table)

    # ruleid: sos-sql-text-string-building
    conn.exec_driver_sql(f"SET LOCAL app.tenant_id = '{student_id}'")

    # ruleid: sos-sql-text-string-building
    cur.execute("DELETE FROM sis.students WHERE id = '" + student_id + "'")

    q = f"SELECT * FROM {table}"
    # ruleid: sos-sql-text-string-building
    session.execute(text(q))

    q2 = "SELECT * FROM " + table
    # ruleid: sos-sql-text-string-building
    stmt = text(q2)

    # ok: sos-sql-text-string-building
    session.execute(text("SELECT * FROM sis.students WHERE id = :id"), {"id": student_id})

    # ok: sos-sql-text-string-building
    session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"),
        {"t": student_id},
    )

    # ok: sos-sql-text-string-building
    session.execute(sa.select(sa.column("id")).where(sa.column("id") == student_id))

    # ok: sos-sql-text-string-building
    message = f"Query for {table} finished"

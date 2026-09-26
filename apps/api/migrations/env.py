"""Alembic environment (docs/05 §14).

Runs as ``sos_migrator`` and immediately ``SET ROLE sos_owner`` so every object is owned by
``sos_owner`` (default privileges in infra/db/bootstrap.sql then apply). Lock and statement
timeouts keep a migration from stalling production traffic.
"""

from __future__ import annotations

from alembic import context
from app.core.config import get_settings
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Connection

config = context.config


def _url() -> str:
    override = config.attributes.get("url")
    if isinstance(override, str):
        return override
    return get_settings().migrator_database_url.get_secret_value()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        engine = create_engine(_url(), poolclass=pool.NullPool, future=True)
        with engine.connect() as connection:
            _run(connection)
        engine.dispose()
    else:
        _run(connectable)


def _run(connection: object) -> None:
    if not isinstance(connection, Connection):
        raise TypeError("alembic 'connection' attribute must be a SQLAlchemy Connection")
    connection.execute(text("SET ROLE sos_owner"))
    connection.execute(text("SET lock_timeout = '5s'"))
    connection.execute(text("SET statement_timeout = '15min'"))
    context.configure(
        connection=connection,
        version_table="alembic_version",
        version_table_schema="ops",
        transaction_per_migration=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
        # Default privileges would hand the app DML on the version table; take it back.
        connection.execute(text("REVOKE ALL ON ops.alembic_version FROM sos_app, sos_readonly"))
    connection.commit()


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is disabled; run migrations against a database.")
run_migrations_online()

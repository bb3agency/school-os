# ruff: noqa
# mypy: ignore-errors
# Test fixture for `semgrep --test .semgrep`. Never imported or executed.
from alembic import op


def upgrade() -> None:
    # ok: sos-migration-rls-bypass
    op.execute("ALTER TABLE sis.students ENABLE ROW LEVEL SECURITY")
    # ok: sos-migration-rls-bypass
    op.execute("ALTER TABLE sis.students FORCE ROW LEVEL SECURITY")
    # ok: sos-migration-rls-bypass
    op.execute("ALTER ROLE sos_app NOBYPASSRLS")
    # ruleid: sos-migration-rls-bypass
    op.execute("ALTER ROLE sos_app BYPASSRLS")
    # ruleid: sos-migration-rls-bypass
    op.execute("ALTER TABLE sis.students DISABLE ROW LEVEL SECURITY")
    # ruleid: sos-migration-rls-bypass
    op.execute("alter table sis.students no force row level security")
    op.execute(
        """
        ALTER TABLE sis.guardians
            # ruleid: sos-migration-rls-bypass
            DISABLE   ROW
            LEVEL SECURITY
        """
    )
    # ruleid: sos-migration-rls-bypass
    op.execute("CREATE ROLE sneaky LOGIN BYPASSRLS")
    # ruleid: sos-migration-rls-bypass
    op.execute("SET row_security = off")


def downgrade() -> None:
    # ok: sos-migration-rls-bypass
    op.execute("DROP TABLE sis.students")

"""Circulars, tasks and parent notices (M4; docs/05 §6.3; FR-CIR-*, FR-TASK-*, FR-NOTICE-*).

Tenant tables (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs; offboarding ``offboarding_purge`` policy and ``sos_purger``
grants like 0032, ADR-0029):

- ``kb.circular_readings``: one AI reading per circular document **version** (idempotent,
  ``UNIQUE (tenant_id, version_id)``): status ``queued -> running -> ready | needs_review`` (the
  error code says why: ``ai_disabled``, ``ai_budget_exhausted``, ``ai_unavailable``,
  ``no_text``, ...), the metadata the passages support (issuer, reference, date, subject), a
  short EN and TE summary with passage citations, the model and prompt used, counts, and who
  marked it reviewed. Deleted with the document (cascade).
- ``kb.circular_suggestions``: deadline suggestions of a reading, each with its passage
  citation (``source`` URI, passage, page, quote). ``suggested -> confirmed | dismissed``, decided
  once by a person (invariant 9); a confirmed one names its task.
- ``ops.tasks``: work with an owner (membership) and a due date; ``open``, ``in_progress``,
  ``done``, ``cancelled``; source ``manual`` or ``circular`` (document + citation; the document
  link is cleared if the document is deleted). No DELETE for the app: tasks are cancelled.
- ``ops.parent_notices``: bilingual notices (``draft -> approved``), optional source circular,
  whether the AI drafted it, who approved, and the rendered A4 PDF / PNG (object keys under the
  school's ``exports/`` prefix, kept 7 days by the bucket rule; re-rendered on request).

Also: ``kb.llm_calls`` accepts the features ``circulars`` / ``notices`` and the model roles
``circular`` / ``notice`` (metering, FR-CIR-007, FR-NOTICE-007), and the permission catalog gets
``circular.review``, ``task.read``, ``task.read_all``, ``task.manage``, ``notice.draft`` and
``notice.approve`` (rows written out here, like 0019). Role grants of existing schools are not
changed (system roles are tenant rows under FORCE RLS; see 0019); new schools get them from
``roles.yaml``.

Expand-only (invariant 12). Downgrade drops the four tables (lossy: readings, suggestions, tasks
and notices of M4), restores the metering checks as ``NOT VALID`` (rows metered for the new
features stay) and removes the new catalog keys a role does not hold.

Revision ID: 0034_circulars
Revises: 0032_offboarding
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_circulars"
down_revision = "0032_offboarding"
branch_labels = None
depends_on = None


OLD_FEATURES = "'ask','metadata','translation','extraction','embeddings','eval'"
OLD_ROLES = "'answer','router','metadata','translation','extraction','eval_judge'"
NEW_FEATURES = OLD_FEATURES + ",'circulars','notices'"
NEW_ROLES = OLD_ROLES + ",'circular','notice'"

TABLES_SQL = r"""
CREATE TABLE kb.circular_readings (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  document_id           uuid NOT NULL,
  version_id            uuid NOT NULL,
  version_no            int NOT NULL CHECK (version_no BETWEEN 1 AND 999999),
  status                text NOT NULL DEFAULT 'queued',
  error_code            text,
  issuer                text CHECK (char_length(issuer) BETWEEN 1 AND 500),
  reference_no          text CHECK (char_length(reference_no) BETWEEN 1 AND 500),
  issued_on             date,
  subject               text CHECK (char_length(subject) BETWEEN 1 AND 500),
  summary_en            text CHECK (char_length(summary_en) BETWEEN 1 AND 2000),
  summary_te            text CHECK (char_length(summary_te) BETWEEN 1 AND 2000),
  summary_sources       jsonb NOT NULL DEFAULT '[]'::jsonb,
  model                 text CHECK (model ~ '^[a-z0-9][a-z0-9._:/-]{0,99}$'),
  prompt                text CHECK (prompt ~ '^[a-z][a-z0-9_]{0,63}\.v[0-9]{1,4}$'),
  suggestions_dropped   int NOT NULL DEFAULT 0 CHECK (suggestions_dropped >= 0),
  passages_sent         int CHECK (passages_sent >= 0),
  passages_total        int CHECK (passages_total >= 0),
  attempts              int NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 100),
  requested_by          uuid,
  reviewed_by           uuid,
  reviewed_at           timestamptz,
  started_at            timestamptz,
  completed_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT circular_readings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT circular_readings_one_per_version UNIQUE (tenant_id, version_id),
  CONSTRAINT circular_readings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT circular_readings_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT circular_readings_version_fk FOREIGN KEY (tenant_id, version_id)
    REFERENCES kb.document_versions (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT circular_readings_requested_by_fk FOREIGN KEY (requested_by)
    REFERENCES core.users (id),
  CONSTRAINT circular_readings_reviewed_by_fk FOREIGN KEY (reviewed_by)
    REFERENCES core.users (id),
  CONSTRAINT circular_readings_status_check
    CHECK (status IN ('queued','running','ready','needs_review')),
  CONSTRAINT circular_readings_error_code_shape CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
  CONSTRAINT circular_readings_error_when_review
    CHECK ((status = 'needs_review') = (error_code IS NOT NULL)),
  CONSTRAINT circular_readings_sources_array CHECK (jsonb_typeof(summary_sources) = 'array'),
  CONSTRAINT circular_readings_reviewed_pair
    CHECK ((reviewed_at IS NULL) = (reviewed_by IS NULL)),
  CONSTRAINT circular_readings_reviewed_when_done
    CHECK (reviewed_at IS NULL OR status IN ('ready','needs_review')),
  CONSTRAINT circular_readings_completed_when_done
    CHECK ((completed_at IS NOT NULL) = (status IN ('ready','needs_review')))
);
CREATE INDEX circular_readings_document_idx
  ON kb.circular_readings (tenant_id, document_id, version_no DESC);
CREATE INDEX circular_readings_open_idx ON kb.circular_readings (tenant_id, status)
  WHERE status IN ('queued','running');
CREATE TRIGGER circular_readings_set_updated_at BEFORE UPDATE ON kb.circular_readings
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.tasks (
  id                        uuid PRIMARY KEY,
  tenant_id                 uuid NOT NULL,
  title                     text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
  details                   text CHECK (char_length(details) BETWEEN 1 AND 2000),
  owner_membership_id       uuid NOT NULL,
  due_on                    date NOT NULL,
  status                    text NOT NULL DEFAULT 'open',
  source                    text NOT NULL,
  document_id               uuid,
  citation                  jsonb,
  created_by                uuid NOT NULL,
  created_by_membership     uuid NOT NULL,
  completed_by              uuid,
  completed_at              timestamptz,
  cancelled_at              timestamptz,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  version                   int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT tasks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tasks_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tasks_owner_fk FOREIGN KEY (tenant_id, owner_membership_id)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tasks_creator_membership_fk FOREIGN KEY (tenant_id, created_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT tasks_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT tasks_completed_by_fk FOREIGN KEY (completed_by) REFERENCES core.users (id),
  CONSTRAINT tasks_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT tasks_status_check CHECK (status IN ('open','in_progress','done','cancelled')),
  CONSTRAINT tasks_source_check CHECK (source IN ('manual','circular')),
  CONSTRAINT tasks_citation_object
    CHECK (citation IS NULL OR (jsonb_typeof(citation) = 'object'
           AND citation ->> 'source' ~ '^sos://doc/[0-9a-f-]{36}/v[1-9][0-9]{0,5}#p[1-9][0-9]{0,4}$')),
  CONSTRAINT tasks_circular_has_citation CHECK (source = 'manual' OR citation IS NOT NULL),
  CONSTRAINT tasks_done_fields
    CHECK ((status = 'done') = (completed_at IS NOT NULL AND completed_by IS NOT NULL)),
  CONSTRAINT tasks_cancelled_fields CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL))
);
CREATE INDEX tasks_owner_due_idx ON ops.tasks (tenant_id, owner_membership_id, due_on, id);
CREATE INDEX tasks_due_idx ON ops.tasks (tenant_id, due_on, id)
  WHERE status IN ('open','in_progress');
CREATE INDEX tasks_document_idx ON ops.tasks (tenant_id, document_id)
  WHERE document_id IS NOT NULL;
CREATE TRIGGER tasks_set_updated_at BEFORE UPDATE ON ops.tasks
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE kb.circular_suggestions (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  reading_id      uuid NOT NULL,
  position        int NOT NULL CHECK (position BETWEEN 1 AND 100),
  title           text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 500),
  details         text CHECK (char_length(details) BETWEEN 1 AND 2000),
  due_on          date NOT NULL,
  citation        jsonb NOT NULL,
  status          text NOT NULL DEFAULT 'suggested',
  task_id         uuid,
  decided_by      uuid,
  decided_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT circular_suggestions_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT circular_suggestions_position_key UNIQUE (tenant_id, reading_id, position),
  CONSTRAINT circular_suggestions_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT circular_suggestions_reading_fk FOREIGN KEY (tenant_id, reading_id)
    REFERENCES kb.circular_readings (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT circular_suggestions_task_fk FOREIGN KEY (tenant_id, task_id)
    REFERENCES ops.tasks (tenant_id, id) ON DELETE SET NULL (task_id),
  CONSTRAINT circular_suggestions_decided_by_fk FOREIGN KEY (decided_by)
    REFERENCES core.users (id),
  CONSTRAINT circular_suggestions_status_check
    CHECK (status IN ('suggested','confirmed','dismissed')),
  CONSTRAINT circular_suggestions_citation_shape
    CHECK (jsonb_typeof(citation) = 'object' AND citation ->> 'source' ~ '^sos://doc/[0-9a-f-]{36}/v[1-9][0-9]{0,5}#p[1-9][0-9]{0,4}$'),
  CONSTRAINT circular_suggestions_decided
    CHECK ((status = 'suggested') = (decided_at IS NULL AND decided_by IS NULL)),
  CONSTRAINT circular_suggestions_task_when_confirmed
    CHECK (task_id IS NULL OR status = 'confirmed')
);
CREATE INDEX circular_suggestions_task_idx ON kb.circular_suggestions (tenant_id, task_id)
  WHERE task_id IS NOT NULL;
CREATE TRIGGER circular_suggestions_set_updated_at BEFORE UPDATE ON kb.circular_suggestions
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.parent_notices (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  source            text NOT NULL,
  document_id       uuid,
  status            text NOT NULL DEFAULT 'draft',
  ai_drafted        boolean NOT NULL DEFAULT false,
  draft_error       text CHECK (draft_error ~ '^[a-z][a-z0-9_]{0,63}$'),
  title_en          text NOT NULL DEFAULT '' CHECK (char_length(title_en) <= 300),
  body_en           text NOT NULL DEFAULT '' CHECK (char_length(body_en) <= 5000),
  title_te          text NOT NULL DEFAULT '' CHECK (char_length(title_te) <= 300),
  body_te           text NOT NULL DEFAULT '' CHECK (char_length(body_te) <= 5000),
  created_by        uuid NOT NULL,
  approved_by       uuid,
  approved_at       timestamptz,
  render_status     text,
  render_error      text CHECK (render_error ~ '^[a-z][a-z0-9_]{0,63}$'),
  pdf_key           text CHECK (pdf_key ~ '^t/[0-9a-f-]{36}/exports/[0-9a-f-]{36}/notice\.(pdf|png)$'),
  png_key           text CHECK (png_key ~ '^t/[0-9a-f-]{36}/exports/[0-9a-f-]{36}/notice\.(pdf|png)$'),
  rendered_at       timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT parent_notices_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT parent_notices_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT parent_notices_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id) ON DELETE SET NULL (document_id),
  CONSTRAINT parent_notices_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT parent_notices_approved_by_fk FOREIGN KEY (approved_by) REFERENCES core.users (id),
  CONSTRAINT parent_notices_source_check CHECK (source IN ('circular','staff_text','blank')),
  CONSTRAINT parent_notices_status_check CHECK (status IN ('draft','approved')),
  CONSTRAINT parent_notices_approved_fields
    CHECK ((status = 'approved') = (approved_at IS NOT NULL AND approved_by IS NOT NULL)),
  -- Both languages are filled before approval (FR-NOTICE-004).
  CONSTRAINT parent_notices_approved_complete
    CHECK (status = 'draft' OR (title_en <> '' AND body_en <> '' AND title_te <> ''
                                AND body_te <> '')),
  CONSTRAINT parent_notices_render_status_check
    CHECK (render_status IS NULL OR render_status IN ('queued','ready','failed')),
  CONSTRAINT parent_notices_render_only_approved
    CHECK (render_status IS NULL OR status = 'approved'),
  CONSTRAINT parent_notices_render_error_when_failed
    CHECK ((render_status = 'failed') = (render_error IS NOT NULL)),
  CONSTRAINT parent_notices_rendered_files
    CHECK ((render_status = 'ready') = (pdf_key IS NOT NULL AND png_key IS NOT NULL
                                         AND rendered_at IS NOT NULL)),
  CONSTRAINT parent_notices_keys_in_tenant CHECK (
    (pdf_key IS NULL OR starts_with(pdf_key, 't/' || tenant_id::text || '/exports/'))
    AND (png_key IS NULL OR starts_with(png_key, 't/' || tenant_id::text || '/exports/')))
);
CREATE INDEX parent_notices_list_idx ON ops.parent_notices (tenant_id, created_at DESC, id DESC);
CREATE TRIGGER parent_notices_set_updated_at BEFORE UPDATE ON ops.parent_notices
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

# Deleted children before parents by the circulars module's purge (ADR-0029).
TENANT_TABLES = (
    "kb.circular_suggestions",
    "kb.circular_readings",
    "ops.parent_notices",
    "ops.tasks",
)

GRANTS_SQL = r"""
-- Readings: the worker updates the workflow and result columns; rows go with their document.
REVOKE UPDATE, TRUNCATE ON kb.circular_readings FROM sos_app;
GRANT UPDATE (status, error_code, issuer, reference_no, issued_on, subject, summary_en,
              summary_te, summary_sources, model, prompt, suggestions_dropped, passages_sent,
              passages_total, attempts, requested_by, reviewed_by, reviewed_at, started_at,
              completed_at, updated_at, version)
  ON kb.circular_readings TO sos_app;
-- Suggestions are decided once; their text and citation never change after the reading.
REVOKE UPDATE, TRUNCATE ON kb.circular_suggestions FROM sos_app;
GRANT UPDATE (status, task_id, decided_by, decided_at, updated_at, version)
  ON kb.circular_suggestions TO sos_app;
-- Tasks are cancelled, never deleted; source and citation never change.
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tasks FROM sos_app;
GRANT UPDATE (title, details, owner_membership_id, due_on, status, completed_by, completed_at,
              cancelled_at, updated_at, version)
  ON ops.tasks TO sos_app;
-- Notices are kept (what the school told parents, and who approved it).
REVOKE DELETE, UPDATE, TRUNCATE ON ops.parent_notices FROM sos_app;
GRANT UPDATE (status, title_en, body_en, title_te, body_te, approved_by, approved_at,
              render_status, render_error, pdf_key, png_key, rendered_at, updated_at, version)
  ON ops.parent_notices TO sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "circular.review",
        "description": (
            "Read circulars again with AI, confirm or dismiss suggested deadlines as tasks, "
            "and mark circulars reviewed"
        ),
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "task.read",
        "description": "See and update the tasks assigned to you",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "task.read_all",
        "description": "See every task of the school",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "task.manage",
        "description": "Create, assign, change and cancel tasks",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "notice.draft",
        "description": "Draft and edit parent notices (with AI help) and download approved ones",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "notice.approve",
        "description": "Approve parent notices for posting",
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
)

UPSERT = sa.text(
    """
    INSERT INTO core.permissions (key, description, sensitivity, step_up, is_platform)
    VALUES (:key, :description, :sensitivity, :step_up, :is_platform)
    ON CONFLICT (key) DO UPDATE SET
      description = EXCLUDED.description,
      sensitivity = EXCLUDED.sensitivity,
      step_up = EXCLUDED.step_up,
      is_platform = EXCLUDED.is_platform
    """
)


def _metering_checks(features: str, roles: str, *, not_valid: bool) -> None:
    suffix = " NOT VALID" if not_valid else ""
    op.execute("ALTER TABLE kb.llm_calls DROP CONSTRAINT llm_calls_feature_check")
    op.execute(
        "ALTER TABLE kb.llm_calls ADD CONSTRAINT llm_calls_feature_check "
        f"CHECK (feature IN ({features})){suffix}"
    )
    op.execute("ALTER TABLE kb.llm_calls DROP CONSTRAINT llm_calls_role_check")
    op.execute(
        "ALTER TABLE kb.llm_calls ADD CONSTRAINT llm_calls_role_check "
        f"CHECK (role IN ({roles})){suffix}"
    )


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the
        # school is offboarding and the transaction-local purge flag names it.
        op.execute(f"GRANT SELECT, DELETE ON {table} TO sos_purger")
        op.execute(
            f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
            "USING (core.tenant_purge_allowed())"
        )
    op.execute(GRANTS_SQL)
    _metering_checks(NEW_FEATURES, NEW_ROLES, not_valid=False)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)


def downgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
    _metering_checks(OLD_FEATURES, OLD_ROLES, not_valid=True)
    op.execute("DROP TABLE IF EXISTS kb.circular_suggestions")
    op.execute("DROP TABLE IF EXISTS ops.parent_notices")
    op.execute("DROP TABLE IF EXISTS ops.tasks")
    op.execute("DROP TABLE IF EXISTS kb.circular_readings")

"""R3-1C CBSE Appendix IX disclosure: draft, frozen snapshots, the published document versions
and the public handle kind ``school_disclosure`` (ADR-0051, amends ADR-0047 decision 1;
FR-DISC-001..012; docs/05 §16.4).

New tenant tables in schema ``ops`` follow the usual shape: ``tenant_id`` first,
``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``, composite FKs, the
restrictive ``offboarding_purge`` policy and ``SELECT, DELETE`` for ``sos_purger`` (ADR-0029), no
``definer_access``, nothing for ``sos_platform``; the app role's default grants are narrowed:

- ``ops.disclosure_drafts``: one per school (``UNIQUE (tenant_id)``): the format key and version,
  the typed ``inputs`` and chosen ``documents`` (item key -> value / document ids; validated by
  the app against the format), ``version`` for ``If-Match``, and the school's current public
  link: ``link_handle_id`` (composite FK into ``core.public_handles``) with its raw 22-character
  ``link_key`` (the link is meant to be published on the school's website, so staff can copy it
  again; only its hash is in ``core.public_handles``) and ``link_created_at`` (all or none).
- ``ops.disclosure_snapshots``: one row per publication, **append-only** except the withdrawal
  columns (all or none, final), the per-day view counts and the file status columns: the frozen
  ``content`` (everything shown; derived values copied), ``content_sha256``, the format key and
  version, who published and when. No DELETE for the app (kept for the life of the school,
  FR-DISC-012). A trigger refuses changing a withdrawal once made.
- ``ops.disclosure_snapshot_documents``: the document versions a snapshot published (``n`` is the
  number in the public URL ``/documents/{n}``), with composite FKs into ``kb.documents`` and
  ``kb.document_versions`` so a published version cannot be deleted. Insert-only.

``core.public_handles.kind`` CHECK gains ``school_disclosure`` (never expires: the existing
``expiry_by_kind`` CHECK already requires that for non-parent kinds; no guardian), and a partial
unique index keeps one live disclosure handle per school. The resolver is unchanged (active
schools only, ADR-0051 decision 4).

The permission catalog gets ``disclosure.edit`` and ``disclosure.publish`` (step-up); owner,
principal (both) and office admin (edit) per docs/07 §6.2. Role grants of existing schools need
the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the three tables (lossy: drafts and the published
record), deletes every ``school_disclosure`` handle (as the owner, FORCE lifted for the one
statement and restored in the same transaction) before narrowing the CHECK back, and deletes the
two permissions unless a role still holds them.

Revision ID: 0066_disclosure
Revises: 0062_messaging_finish
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0066_disclosure"
down_revision = "0062_messaging_finish"
branch_labels = None
depends_on = None

# Children before parents (the order the module's offboarding purge deletes them in).
TENANT_TABLES = (
    "ops.disclosure_snapshot_documents",
    "ops.disclosure_snapshots",
    "ops.disclosure_drafts",
)

OLD_KINDS = (
    "'cert_verify','parent_notice','parent_receipt','parent_dues','parent_optin',"
    "'razorpay_route','meta_waba'"
)
NEW_KINDS = OLD_KINDS + ",'school_disclosure'"

HANDLES_SQL = f"""
ALTER TABLE core.public_handles DROP CONSTRAINT public_handles_kind_check;
ALTER TABLE core.public_handles ADD CONSTRAINT public_handles_kind_check
  CHECK (kind IN ({NEW_KINDS}));
CREATE UNIQUE INDEX public_handles_one_disclosure ON core.public_handles (tenant_id)
  WHERE kind = 'school_disclosure' AND revoked_at IS NULL;
"""

TABLES_SQL = r"""
CREATE TABLE ops.disclosure_drafts (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  format_key       text NOT NULL,
  format_version   int NOT NULL CHECK (format_version >= 1),
  inputs           jsonb NOT NULL DEFAULT '{}'::jsonb,
  documents        jsonb NOT NULL DEFAULT '{}'::jsonb,
  link_handle_id   uuid,
  link_key         text,
  link_created_at  timestamptz,
  updated_by       uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT disclosure_drafts_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT disclosure_drafts_one_per_school UNIQUE (tenant_id),
  CONSTRAINT disclosure_drafts_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT disclosure_drafts_link_fk FOREIGN KEY (tenant_id, link_handle_id)
    REFERENCES core.public_handles (tenant_id, id),
  CONSTRAINT disclosure_drafts_updated_by_fk FOREIGN KEY (updated_by) REFERENCES core.users (id),
  CONSTRAINT disclosure_drafts_format_key CHECK (format_key ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
  CONSTRAINT disclosure_drafts_inputs_object CHECK (jsonb_typeof(inputs) = 'object'),
  CONSTRAINT disclosure_drafts_documents_object CHECK (jsonb_typeof(documents) = 'object'),
  CONSTRAINT disclosure_drafts_link CHECK (
    (link_handle_id IS NULL) = (link_key IS NULL)
    AND (link_key IS NULL) = (link_created_at IS NULL)),
  CONSTRAINT disclosure_drafts_link_key_format CHECK (
    link_key IS NULL OR link_key ~ '^[A-Za-z0-9_-]{22}$')
);
CREATE TRIGGER disclosure_drafts_set_updated_at BEFORE UPDATE ON ops.disclosure_drafts
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.disclosure_snapshots (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  format_key         text NOT NULL,
  format_version     int NOT NULL CHECK (format_version >= 1),
  content            jsonb NOT NULL,
  content_sha256     bytea NOT NULL,
  published_by       uuid NOT NULL,
  published_at       timestamptz NOT NULL DEFAULT now(),
  withdrawn_at       timestamptz,
  withdrawn_by       uuid,
  withdraw_reason    text,
  views_by_day       jsonb NOT NULL DEFAULT '{}'::jsonb,
  pdf_status         text NOT NULL DEFAULT 'queued',
  html_status        text NOT NULL DEFAULT 'queued',
  pdf_key            text,
  html_key           text,
  files_rendered_at  timestamptz,
  files_error        text,
  CONSTRAINT disclosure_snapshots_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT disclosure_snapshots_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT disclosure_snapshots_published_by_fk FOREIGN KEY (published_by)
    REFERENCES core.users (id),
  CONSTRAINT disclosure_snapshots_withdrawn_by_fk FOREIGN KEY (withdrawn_by)
    REFERENCES core.users (id),
  CONSTRAINT disclosure_snapshots_format_key CHECK (format_key ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
  CONSTRAINT disclosure_snapshots_content_object CHECK (jsonb_typeof(content) = 'object'),
  CONSTRAINT disclosure_snapshots_sha256 CHECK (octet_length(content_sha256) = 32),
  CONSTRAINT disclosure_snapshots_withdrawal CHECK (
    (withdrawn_at IS NULL) = (withdrawn_by IS NULL)
    AND (withdrawn_at IS NULL) = (withdraw_reason IS NULL)),
  CONSTRAINT disclosure_snapshots_withdraw_reason CHECK (
    withdraw_reason IS NULL OR char_length(withdraw_reason) BETWEEN 10 AND 500),
  CONSTRAINT disclosure_snapshots_views_object CHECK (jsonb_typeof(views_by_day) = 'object'),
  CONSTRAINT disclosure_snapshots_pdf_status CHECK (pdf_status IN ('queued','ready','failed')),
  CONSTRAINT disclosure_snapshots_html_status CHECK (html_status IN ('queued','ready','failed')),
  CONSTRAINT disclosure_snapshots_pdf_ready CHECK ((pdf_status = 'ready') = (pdf_key IS NOT NULL)),
  CONSTRAINT disclosure_snapshots_html_ready CHECK (
    (html_status = 'ready') = (html_key IS NOT NULL)),
  CONSTRAINT disclosure_snapshots_files_error CHECK (
    files_error IS NULL OR files_error ~ '^[a-z][a-z_]{1,39}$')
);
CREATE INDEX disclosure_snapshots_published_idx
  ON ops.disclosure_snapshots (tenant_id, published_at DESC);

-- A withdrawal is final (defence in depth next to the column grants).
CREATE FUNCTION ops.tg_disclosure_snapshots_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.withdrawn_at IS NOT NULL AND (NEW.withdrawn_at IS DISTINCT FROM OLD.withdrawn_at
      OR NEW.withdrawn_by IS DISTINCT FROM OLD.withdrawn_by
      OR NEW.withdraw_reason IS DISTINCT FROM OLD.withdraw_reason) THEN
    RAISE EXCEPTION 'a withdrawn disclosure stays withdrawn' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.content <> OLD.content
     OR NEW.content_sha256 <> OLD.content_sha256 OR NEW.format_key <> OLD.format_key
     OR NEW.format_version <> OLD.format_version OR NEW.published_by <> OLD.published_by
     OR NEW.published_at <> OLD.published_at THEN
    RAISE EXCEPTION 'a published disclosure is frozen' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION ops.tg_disclosure_snapshots_guard() FROM PUBLIC;
CREATE TRIGGER disclosure_snapshots_guard BEFORE UPDATE ON ops.disclosure_snapshots
  FOR EACH ROW EXECUTE FUNCTION ops.tg_disclosure_snapshots_guard();

CREATE TABLE ops.disclosure_snapshot_documents (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  snapshot_id  uuid NOT NULL,
  n            int NOT NULL CHECK (n BETWEEN 1 AND 200),
  item_key     text NOT NULL,
  document_id  uuid NOT NULL,
  version_id   uuid NOT NULL,
  title        text NOT NULL,
  CONSTRAINT disclosure_snapshot_documents_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT disclosure_snapshot_documents_n_key UNIQUE (tenant_id, snapshot_id, n),
  CONSTRAINT disclosure_snapshot_documents_tenant_fk FOREIGN KEY (tenant_id)
    REFERENCES core.tenants (id),
  CONSTRAINT disclosure_snapshot_documents_snapshot_fk FOREIGN KEY (tenant_id, snapshot_id)
    REFERENCES ops.disclosure_snapshots (tenant_id, id),
  CONSTRAINT disclosure_snapshot_documents_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT disclosure_snapshot_documents_version_fk FOREIGN KEY (tenant_id, version_id)
    REFERENCES kb.document_versions (tenant_id, id),
  CONSTRAINT disclosure_snapshot_documents_item_key CHECK (item_key ~ '^[a-z][a-z0-9_]{1,63}$'),
  CONSTRAINT disclosure_snapshot_documents_title CHECK (char_length(title) BETWEEN 1 AND 300)
);
CREATE INDEX disclosure_snapshot_documents_version_idx
  ON ops.disclosure_snapshot_documents (tenant_id, version_id);
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON ops.disclosure_drafts, ops.disclosure_snapshots, ops.disclosure_snapshot_documents
  FROM sos_app;
GRANT UPDATE (inputs, documents, format_key, format_version, link_handle_id, link_key,
  link_created_at, updated_by, updated_at, version) ON ops.disclosure_drafts TO sos_app;
GRANT UPDATE (withdrawn_at, withdrawn_by, withdraw_reason, views_by_day, pdf_status, html_status,
  pdf_key, html_key, files_rendered_at, files_error) ON ops.disclosure_snapshots TO sos_app;
REVOKE ALL ON ops.disclosure_drafts, ops.disclosure_snapshots, ops.disclosure_snapshot_documents
  FROM sos_readonly;
GRANT SELECT (id, tenant_id, format_key, format_version, inputs, documents, link_handle_id,
  link_created_at, updated_by, created_at, updated_at, version)
  ON ops.disclosure_drafts TO sos_readonly;
GRANT SELECT ON ops.disclosure_snapshots, ops.disclosure_snapshot_documents TO sos_readonly;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "disclosure.edit",
        "description": (
            "Edit the CBSE Appendix IX disclosure draft, see what was published and download "
            "the published files"
        ),
        "sensitivity": "normal",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "disclosure.publish",
        "description": (
            "Publish the CBSE Appendix IX disclosure, withdraw it and change its public link "
            "(recent MFA sign-in)"
        ),
        "sensitivity": "critical",
        "step_up": True,
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

UNFORCE_HANDLES = "ALTER TABLE core.public_handles NO FORCE ROW LEVEL SECURITY"  # nosemgrep: sos-migration-rls-bypass -- owner deletes every school's disclosure handles on downgrade before narrowing the kind CHECK; FORCE is restored in the same transaction
REFORCE_HANDLES = "ALTER TABLE core.public_handles FORCE ROW LEVEL SECURITY"


def upgrade() -> None:
    op.execute(HANDLES_SQL)
    op.execute(TABLES_SQL)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        op.execute(f"GRANT SELECT, DELETE ON {table} TO sos_purger")
        op.execute(
            f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
            "USING (core.tenant_purge_allowed())"
        )
    op.execute(GRANTS_SQL)
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
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS ops.tg_disclosure_snapshots_guard()")
    op.execute(UNFORCE_HANDLES)
    op.execute("DELETE FROM core.public_handles WHERE kind = 'school_disclosure'")
    op.execute(REFORCE_HANDLES)
    op.execute("DROP INDEX IF EXISTS core.public_handles_one_disclosure")
    op.execute("ALTER TABLE core.public_handles DROP CONSTRAINT public_handles_kind_check")
    op.execute(
        "ALTER TABLE core.public_handles ADD CONSTRAINT public_handles_kind_check "
        f"CHECK (kind IN ({OLD_KINDS}))"
    )

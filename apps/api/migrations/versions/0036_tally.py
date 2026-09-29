"""Tally read connector (M6; ADR-0032 Proposed; docs/05 §7.4; FR-TALLY-001..010).

Everything here is behind the per-school feature flag ``tally.connector.enabled`` (default off).
Tenant tables in schema ``ops`` (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE +
FORCE with ``tenant_isolation``, composite FKs; offboarding ``offboarding_purge`` policy and
``sos_purger`` grants like 0032, ADR-0029):

- ``ops.tally_enrolment_codes``: one-time codes the owner creates (step-up) to enrol an edge
  agent. Only the SHA-256 of the code is stored; valid 30 minutes, used once.
- ``ops.tally_devices``: enrolled agents. The HMAC device secret is stored only wrapped by the
  school's key wrapper (KMS in staging/prod; an HMAC verifier needs the secret, so it cannot be a
  one-way hash, ADR-0032 §2), with a next key during a rotation. Revocation erases both keys.
- ``ops.tally_groups``: the ledger groups the agent reported (names only) and which of them the
  accountant selected: the agent syncs parties under selected groups only.
- ``ops.tally_syncs``: one row per accepted snapshot (``UNIQUE (tenant_id, device_id,
  batch_id)``: a repeated batch is answered from here, never applied twice); counts and the total
  due only.
- ``ops.tally_parties``: the latest closing balance of each party ledger under a selected group
  (ledger names are usually student or parent names: C2 personal data, readable with
  ``finance.read`` only). ``present = false`` when the last snapshot no longer had it.
- ``ops.tally_party_links``: party <-> student links made by a person (``tally.configure``); the
  AI never links (invariant 9). Deleted with the party or the student.

Also the permission catalog gets ``tally.device.manage`` and ``tally.configure`` (rows written out
here, like 0034). Role grants of existing schools are not changed (system roles are tenant rows
under FORCE RLS); the release runs the post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the six tables (lossy: synced Tally data of M6) and
removes the new catalog keys a role does not hold.

Revision ID: 0036_tally
Revises: 0035_student_insights
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_tally"
down_revision = "0035_student_insights"
branch_labels = None
depends_on = None


TABLES_SQL = r"""
CREATE TABLE ops.tally_enrolment_codes (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  code_hash       bytea NOT NULL CHECK (octet_length(code_hash) = 32),
  device_name     text NOT NULL CHECK (char_length(device_name) BETWEEN 1 AND 80),
  created_by      uuid NOT NULL,
  expires_at      timestamptz NOT NULL,
  used_at         timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tally_enrolment_codes_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_enrolment_codes_hash_key UNIQUE (tenant_id, code_hash),
  CONSTRAINT tally_enrolment_codes_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_enrolment_codes_created_by_fk FOREIGN KEY (created_by)
    REFERENCES core.users (id),
  CONSTRAINT tally_enrolment_codes_expiry CHECK (expires_at > created_at),
  CONSTRAINT tally_enrolment_codes_used_in_time CHECK (used_at IS NULL OR used_at <= expires_at)
);

CREATE TABLE ops.tally_devices (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  name                  text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  status                text NOT NULL DEFAULT 'active',
  enrolment_code_id     uuid NOT NULL,
  key_id                text CHECK (key_id ~ '^tdk-[a-z]{20}$'),
  key_ciphertext        bytea,
  next_key_id           text CHECK (next_key_id ~ '^tdk-[a-z]{20}$'),
  next_key_ciphertext   bytea,
  rotation_started_at   timestamptz,
  agent_version         text CHECK (agent_version ~ '^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,6}$'),
  platform              text CHECK (platform ~ '^[a-z0-9][a-z0-9._-]{0,39}$'),
  tally_product         text CHECK (char_length(tally_product) BETWEEN 1 AND 80),
  enrolled_by           uuid NOT NULL,
  enrolled_at           timestamptz NOT NULL DEFAULT now(),
  revoked_by            uuid,
  revoked_at            timestamptz,
  last_seen_at          timestamptz,
  last_sync_at          timestamptz,
  silent_notified_at    timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT tally_devices_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_devices_key_id_key UNIQUE (tenant_id, key_id),
  CONSTRAINT tally_devices_next_key_id_key UNIQUE (tenant_id, next_key_id),
  CONSTRAINT tally_devices_code_key UNIQUE (tenant_id, enrolment_code_id),
  CONSTRAINT tally_devices_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_devices_code_fk FOREIGN KEY (tenant_id, enrolment_code_id)
    REFERENCES ops.tally_enrolment_codes (tenant_id, id),
  CONSTRAINT tally_devices_enrolled_by_fk FOREIGN KEY (enrolled_by) REFERENCES core.users (id),
  CONSTRAINT tally_devices_revoked_by_fk FOREIGN KEY (revoked_by) REFERENCES core.users (id),
  CONSTRAINT tally_devices_status_check CHECK (status IN ('active','revoked')),
  CONSTRAINT tally_devices_key_pair CHECK ((key_id IS NULL) = (key_ciphertext IS NULL)),
  CONSTRAINT tally_devices_next_key_pair
    CHECK ((next_key_id IS NULL) = (next_key_ciphertext IS NULL)
           AND (next_key_id IS NULL) = (rotation_started_at IS NULL)),
  -- An active device has a key; a revoked one has none (erased at revocation, ADR-0032 §2).
  CONSTRAINT tally_devices_keys_by_status CHECK (
    (status = 'active' AND key_id IS NOT NULL)
    OR (status = 'revoked' AND key_id IS NULL AND next_key_id IS NULL)),
  CONSTRAINT tally_devices_revoked_fields
    CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
);
CREATE INDEX tally_devices_active_idx ON ops.tally_devices (tenant_id, last_seen_at)
  WHERE status = 'active';
CREATE TRIGGER tally_devices_set_updated_at BEFORE UPDATE ON ops.tally_devices
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.tally_groups (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  company         text NOT NULL CHECK (char_length(company) BETWEEN 1 AND 200),
  name            text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
  parent          text CHECK (char_length(parent) BETWEEN 1 AND 200),
  present         boolean NOT NULL DEFAULT true,
  selected        boolean NOT NULL DEFAULT false,
  selected_by     uuid,
  selected_at     timestamptz,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at    timestamptz NOT NULL DEFAULT now(),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT tally_groups_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_groups_name_key UNIQUE (tenant_id, company, name),
  CONSTRAINT tally_groups_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_groups_selected_by_fk FOREIGN KEY (selected_by) REFERENCES core.users (id),
  CONSTRAINT tally_groups_selected_fields
    CHECK (NOT selected OR (selected_by IS NOT NULL AND selected_at IS NOT NULL))
);
CREATE TRIGGER tally_groups_set_updated_at BEFORE UPDATE ON ops.tally_groups
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.tally_syncs (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  device_id       uuid NOT NULL,
  batch_id        uuid NOT NULL,
  company         text NOT NULL CHECK (char_length(company) BETWEEN 1 AND 200),
  as_of           date NOT NULL,
  groups          int NOT NULL CHECK (groups >= 0),
  parties         int NOT NULL CHECK (parties >= 0),
  created         int NOT NULL CHECK (created >= 0),
  updated         int NOT NULL CHECK (updated >= 0),
  missing         int NOT NULL CHECK (missing >= 0),
  total_due       numeric(14,2) NOT NULL,
  received_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tally_syncs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_syncs_batch_key UNIQUE (tenant_id, device_id, batch_id),
  CONSTRAINT tally_syncs_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_syncs_device_fk FOREIGN KEY (tenant_id, device_id)
    REFERENCES ops.tally_devices (tenant_id, id)
);
CREATE INDEX tally_syncs_received_idx ON ops.tally_syncs (tenant_id, received_at DESC);

CREATE TABLE ops.tally_parties (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  company          text NOT NULL CHECK (char_length(company) BETWEEN 1 AND 200),
  guid             text CHECK (guid ~ '^[0-9A-Za-z-]{1,80}$'),
  ledger_name      text NOT NULL CHECK (char_length(ledger_name) BETWEEN 1 AND 200),
  group_name       text NOT NULL CHECK (char_length(group_name) BETWEEN 1 AND 200),
  closing_balance  numeric(14,2) NOT NULL,
  as_of            date NOT NULL,
  present          boolean NOT NULL DEFAULT true,
  last_sync_id     uuid NOT NULL,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT tally_parties_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_parties_guid_key UNIQUE (tenant_id, company, guid),
  CONSTRAINT tally_parties_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_parties_sync_fk FOREIGN KEY (tenant_id, last_sync_id)
    REFERENCES ops.tally_syncs (tenant_id, id)
);
-- Ledgers without a GUID are matched by name (a name is unique within a Tally company).
CREATE UNIQUE INDEX tally_parties_name_key ON ops.tally_parties (tenant_id, company, ledger_name)
  WHERE guid IS NULL;
CREATE INDEX tally_parties_list_idx ON ops.tally_parties (tenant_id, present, ledger_name, id);
CREATE TRIGGER tally_parties_set_updated_at BEFORE UPDATE ON ops.tally_parties
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE ops.tally_party_links (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  party_id     uuid NOT NULL,
  student_id   uuid NOT NULL,
  linked_by    uuid NOT NULL,
  linked_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tally_party_links_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT tally_party_links_pair_key UNIQUE (tenant_id, party_id, student_id),
  CONSTRAINT tally_party_links_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT tally_party_links_party_fk FOREIGN KEY (tenant_id, party_id)
    REFERENCES ops.tally_parties (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT tally_party_links_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT tally_party_links_linked_by_fk FOREIGN KEY (linked_by) REFERENCES core.users (id)
);
CREATE INDEX tally_party_links_student_idx ON ops.tally_party_links (tenant_id, student_id);
"""

# Deleted children before parents by the tally module's purge (ADR-0029).
TENANT_TABLES = (
    "ops.tally_party_links",
    "ops.tally_parties",
    "ops.tally_syncs",
    "ops.tally_groups",
    "ops.tally_devices",
    "ops.tally_enrolment_codes",
)

GRANTS_SQL = r"""
-- Codes are used once; their hash, school and expiry never change; kept (no DELETE).
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tally_enrolment_codes FROM sos_app;
GRANT UPDATE (used_at) ON ops.tally_enrolment_codes TO sos_app;
-- Devices are revoked, never deleted; who enrolled them and with which code never changes.
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tally_devices FROM sos_app;
GRANT UPDATE (status, key_id, key_ciphertext, next_key_id, next_key_ciphertext,
              rotation_started_at, agent_version, platform, tally_product, revoked_by,
              revoked_at, last_seen_at, last_sync_at, silent_notified_at, updated_at, version)
  ON ops.tally_devices TO sos_app;
-- Groups: the catalog refreshes presence and parent; the accountant selects.
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tally_groups FROM sos_app;
GRANT UPDATE (parent, present, selected, selected_by, selected_at, last_seen_at, updated_at,
              version)
  ON ops.tally_groups TO sos_app;
-- Sync records are never rewritten; old ones are deleted by the retention job.
REVOKE UPDATE, TRUNCATE ON ops.tally_syncs FROM sos_app;
-- Parties are marked not present, never deleted while the connector runs.
REVOKE DELETE, UPDATE, TRUNCATE ON ops.tally_parties FROM sos_app;
GRANT UPDATE (guid, ledger_name, group_name, closing_balance, as_of, present, last_sync_id,
              updated_at, version)
  ON ops.tally_parties TO sos_app;
-- Links are made and removed by a person; never edited.
REVOKE UPDATE, TRUNCATE ON ops.tally_party_links FROM sos_app;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "tally.device.manage",
        "description": (
            "Enrol the Tally edge agent with a one-time code and revoke it (recent MFA sign-in)"
        ),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
    {
        "key": "tally.configure",
        "description": "Choose the Tally ledger groups to sync and link Tally ledgers to students",
        "sensitivity": "sensitive",
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

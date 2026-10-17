"""R2-2E UPI and bank statements: school UPI settings, bank statement imports and lines, UPI
confirmations (ADR-0042 decision 9, P7; owner decision D19; FR-FEE-014..017; docs/05 §15.1).

Every table lives in the tenant schema ``fin`` and follows the tenant-table rules of
``0058_fees_core``: ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge`` policy for
``sos_purger`` (ADR-0029), no ``definer_access``, nothing for ``sos_platform``.

- ``fin.upi_settings`` (one row per school): payee name, UPI ID (VPA; shape only, never checked
  with a bank) and ``confirm_within_days`` (1-30, default 7; FR-FEE-017). The static QR shown at
  the counter is generated from these two values (``upi://pay?pa=..&pn=..&cu=INR``, no amount):
  SchoolOS never receives money (ADR-0042 decision 2).
- ``fin.bank_statement_imports``: one row per statement file read; **append-only**. The file is
  deleted in the same transaction that parses it (``file_deleted_at`` NOT NULL); the same file
  (SHA-256) is read once only. No account number, holder name or balance is stored.
- ``fin.bank_statement_lines``: the **credits** of a statement (debits are never stored): value
  date, amount, the UPI reference (UTR, 12 digits) or other bank reference, and the narration
  encrypted with the school's DEK (AAD ``fin.bank_statement_lines|narration_ciphertext|<id>``;
  payer names; erased after 90 days, ``narration_erased_at``). ``match_status``: ``unmatched``,
  ``proposed`` (a receipt suggested by a matching rule), ``confirmed`` (a person confirmed it),
  ``ignored`` (a person said it is not a fee payment). Trigger ``bank_statement_lines_frozen``:
  the bank's facts never change; a confirmed line stays confirmed and points at another receipt
  only through a new confirmation (its first receipt was cancelled); an ignored line stays
  ignored; the narration may only be re-encrypted (key rotation) or erased. The same reference,
  amount and date is stored once (overlapping statements).
- ``fin.upi_confirmations``: **append-only** rows "this UPI receipt was seen in the bank"
  (receipts are never edited, FR-FEE-016). Trigger ``upi_confirmations_check``: the receipt is an
  issued ``upi_qr`` receipt, the line's amount equals the receipt's, a line carrying a UTR
  carries the receipt's UTR (a different UTR is never "corrected"), the line is not ignored, and
  no issued receipt already holds the line. One confirmation per receipt.

Also the ``fee.reconcile`` permission is upserted into ``core.permissions``; role grants of existing
schools come from ``python -m app.identity.sync_system_roles`` (ADR-0022).

Expand-only (invariant 12). Downgrade drops the four tables (lossy: UPI settings, statement lines
and confirmations go; receipts are untouched) and deletes ``fee.reconcile`` unless a role still
holds it.

Revision ID: 0060_fees_upi
Revises: 0059_payments_online
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0060_fees_upi"
down_revision = "0059_payments_online"
branch_labels = None
depends_on = None

# Children before parents (downgrade drops in this order; the purge registers the same order).
TENANT_TABLES = (
    "fin.upi_confirmations",
    "fin.bank_statement_lines",
    "fin.bank_statement_imports",
    "fin.upi_settings",
)

FUNCTIONS = (
    "fin.tg_bank_statement_lines_frozen()",
    "fin.tg_upi_confirmations_check()",
)

UPGRADE_SQL = r"""
-- UPI settings ---------------------------------------------------------------------------------

CREATE TABLE fin.upi_settings (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  payee_name           text NOT NULL,
  vpa                  text NOT NULL,
  confirm_within_days  int NOT NULL DEFAULT 7,
  updated_by           uuid NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1,
  CONSTRAINT upi_settings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT upi_settings_one_per_school UNIQUE (tenant_id),
  CONSTRAINT upi_settings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT upi_settings_updated_by_fk FOREIGN KEY (tenant_id, updated_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT upi_settings_payee_length CHECK (char_length(payee_name) BETWEEN 2 AND 60),
  CONSTRAINT upi_settings_vpa_shape
    CHECK (vpa ~ '^[A-Za-z0-9][A-Za-z0-9._-]{1,63}@[A-Za-z][A-Za-z0-9]{1,63}$'),
  CONSTRAINT upi_settings_confirm_days CHECK (confirm_within_days BETWEEN 1 AND 30),
  CONSTRAINT upi_settings_version_positive CHECK (version >= 1)
);
CREATE TRIGGER upi_settings_set_updated_at BEFORE UPDATE ON fin.upi_settings
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- Bank statement imports (append-only) --------------------------------------------------------

CREATE TABLE fin.bank_statement_imports (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  preset_key       text NOT NULL,
  preset_version   int NOT NULL,
  file_kind        text NOT NULL,
  file_sha256      bytea NOT NULL,
  period_from      date,
  period_to        date,
  line_count       int NOT NULL,
  credit_count     int NOT NULL,
  duplicate_count  int NOT NULL,
  proposed_count   int NOT NULL,
  imported_by      uuid NOT NULL,
  imported_at      timestamptz NOT NULL DEFAULT now(),
  file_deleted_at  timestamptz NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT bank_statement_imports_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT bank_statement_imports_file_once UNIQUE (tenant_id, file_sha256),
  CONSTRAINT bank_statement_imports_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT bank_statement_imports_imported_by_fk FOREIGN KEY (tenant_id, imported_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT bank_statement_imports_preset_shape CHECK (preset_key ~ '^[a-z][a-z0-9_-]{1,39}$'),
  CONSTRAINT bank_statement_imports_preset_version CHECK (preset_version >= 1),
  CONSTRAINT bank_statement_imports_kind_check CHECK (file_kind IN ('csv','xlsx')),
  CONSTRAINT bank_statement_imports_sha256_length CHECK (octet_length(file_sha256) = 32),
  CONSTRAINT bank_statement_imports_period
    CHECK ((period_from IS NULL) = (period_to IS NULL)
           AND (period_from IS NULL OR period_from <= period_to)),
  CONSTRAINT bank_statement_imports_counts CHECK (line_count BETWEEN 0 AND 5000
    AND credit_count BETWEEN 0 AND line_count AND duplicate_count BETWEEN 0 AND credit_count
    AND proposed_count BETWEEN 0 AND credit_count)
);
CREATE INDEX bank_statement_imports_list_idx
  ON fin.bank_statement_imports (tenant_id, imported_at DESC, id DESC);
CREATE TRIGGER bank_statement_imports_append_only BEFORE UPDATE OR DELETE
  ON fin.bank_statement_imports FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- Bank statement lines (credits only) ---------------------------------------------------------

CREATE TABLE fin.bank_statement_lines (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  import_id             uuid NOT NULL,
  line_no               int NOT NULL,
  value_date            date NOT NULL,
  amount                numeric(14,2) NOT NULL,
  ref_kind              text NOT NULL,
  utr_or_ref            text,
  narration_ciphertext  bytea,
  narration_erased_at   timestamptz,
  match_status          text NOT NULL DEFAULT 'unmatched',
  match_kind            text,
  receipt_id            uuid,
  ignore_reason         text,
  decided_by            uuid,
  decided_at            timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1,
  CONSTRAINT bank_statement_lines_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT bank_statement_lines_line_unique UNIQUE (tenant_id, import_id, line_no),
  CONSTRAINT bank_statement_lines_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT bank_statement_lines_import_fk FOREIGN KEY (tenant_id, import_id)
    REFERENCES fin.bank_statement_imports (tenant_id, id),
  CONSTRAINT bank_statement_lines_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT bank_statement_lines_decided_by_fk FOREIGN KEY (tenant_id, decided_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT bank_statement_lines_line_no_range CHECK (line_no BETWEEN 1 AND 1000000),
  CONSTRAINT bank_statement_lines_amount_positive CHECK (amount > 0),
  CONSTRAINT bank_statement_lines_ref_shape CHECK (
    (ref_kind = 'none' AND utr_or_ref IS NULL)
    OR (ref_kind = 'utr' AND utr_or_ref ~ '^[0-9]{12}$')
    OR (ref_kind = 'reference' AND utr_or_ref ~ '^[A-Za-z0-9]{6,35}$')),
  CONSTRAINT bank_statement_lines_narration_erased
    CHECK (narration_erased_at IS NULL OR narration_ciphertext IS NULL),
  CONSTRAINT bank_statement_lines_status_check
    CHECK (match_status IN ('unmatched','proposed','confirmed','ignored')),
  CONSTRAINT bank_statement_lines_match_kind_check
    CHECK (match_kind IS NULL OR match_kind IN ('utr_exact','amount_date','manual')),
  CONSTRAINT bank_statement_lines_match_shape CHECK (
    (match_status IN ('proposed','confirmed')) = (receipt_id IS NOT NULL)
    AND (match_status IN ('proposed','confirmed')) = (match_kind IS NOT NULL)),
  CONSTRAINT bank_statement_lines_ignore_shape CHECK (
    (match_status = 'ignored') = (ignore_reason IS NOT NULL)
    AND (ignore_reason IS NULL
         OR ignore_reason IN ('not_a_fee','recorded_elsewhere','duplicate_credit','other'))),
  CONSTRAINT bank_statement_lines_decided_shape CHECK (
    (match_status IN ('confirmed','ignored')) = (decided_by IS NOT NULL)
    AND (decided_by IS NULL) = (decided_at IS NULL)),
  CONSTRAINT bank_statement_lines_version_positive CHECK (version >= 1)
);
CREATE INDEX bank_statement_lines_import_idx
  ON fin.bank_statement_lines (tenant_id, import_id, line_no);
CREATE INDEX bank_statement_lines_status_idx
  ON fin.bank_statement_lines (tenant_id, match_status, value_date);
-- A receipt is proposed for, or confirmed by, one bank credit at a time.
CREATE UNIQUE INDEX bank_statement_lines_receipt_once ON fin.bank_statement_lines
  (tenant_id, receipt_id) WHERE receipt_id IS NOT NULL;
CREATE INDEX bank_statement_lines_narration_idx ON fin.bank_statement_lines (tenant_id, created_at)
  WHERE narration_ciphertext IS NOT NULL;
-- Overlapping statements: the same credit (reference, amount, date) is stored once.
CREATE UNIQUE INDEX bank_statement_lines_credit_once ON fin.bank_statement_lines
  (tenant_id, utr_or_ref, amount, value_date) WHERE utr_or_ref IS NOT NULL;
CREATE TRIGGER bank_statement_lines_set_updated_at BEFORE UPDATE ON fin.bank_statement_lines
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE FUNCTION fin.tg_bank_statement_lines_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF core.tenant_purge_allowed() THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'bank statement lines are never deleted'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'bank_statement_lines_frozen';
  END IF;
  IF NEW.tenant_id <> OLD.tenant_id OR NEW.import_id <> OLD.import_id
     OR NEW.line_no <> OLD.line_no OR NEW.value_date <> OLD.value_date
     OR NEW.amount <> OLD.amount OR NEW.ref_kind <> OLD.ref_kind
     OR NEW.utr_or_ref IS DISTINCT FROM OLD.utr_or_ref OR NEW.created_at <> OLD.created_at
     -- the narration is only re-encrypted (key rotation) or erased, never written again
     OR (OLD.narration_ciphertext IS NULL AND NEW.narration_ciphertext IS NOT NULL)
     OR (OLD.narration_erased_at IS NOT NULL
         AND NEW.narration_erased_at IS DISTINCT FROM OLD.narration_erased_at)
     -- a person's decision stands
     OR (OLD.match_status = 'ignored' AND NEW.match_status <> 'ignored')
     OR (OLD.match_status = 'confirmed' AND NEW.match_status <> 'confirmed')
     OR (OLD.match_status IN ('confirmed','ignored') AND NEW.match_status = OLD.match_status
         AND (NEW.ignore_reason IS DISTINCT FROM OLD.ignore_reason
              OR (NEW.receipt_id IS DISTINCT FROM OLD.receipt_id AND NOT EXISTS (
                    SELECT 1 FROM fin.upi_confirmations c
                    WHERE c.tenant_id = NEW.tenant_id AND c.statement_line_id = NEW.id
                      AND c.receipt_id = NEW.receipt_id))))
     -- confirming needs its confirmation row first
     OR (NEW.match_status = 'confirmed' AND OLD.match_status <> 'confirmed' AND NOT EXISTS (
           SELECT 1 FROM fin.upi_confirmations c
           WHERE c.tenant_id = NEW.tenant_id AND c.statement_line_id = NEW.id
             AND c.receipt_id = NEW.receipt_id))
  THEN
    RAISE EXCEPTION 'a bank statement line keeps the bank''s facts and a person''s decision'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'bank_statement_lines_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER bank_statement_lines_frozen BEFORE UPDATE OR DELETE ON fin.bank_statement_lines
  FOR EACH ROW EXECUTE FUNCTION fin.tg_bank_statement_lines_frozen();

-- UPI confirmations (append-only) -------------------------------------------------------------

CREATE TABLE fin.upi_confirmations (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  receipt_id         uuid NOT NULL,
  statement_line_id  uuid NOT NULL,
  match_kind         text NOT NULL,
  confirmed_by       uuid NOT NULL,
  confirmed_at       timestamptz NOT NULL DEFAULT now(),
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT upi_confirmations_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT upi_confirmations_receipt_once UNIQUE (tenant_id, receipt_id),
  CONSTRAINT upi_confirmations_pair_unique UNIQUE (tenant_id, statement_line_id, receipt_id),
  CONSTRAINT upi_confirmations_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT upi_confirmations_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT upi_confirmations_line_fk FOREIGN KEY (tenant_id, statement_line_id)
    REFERENCES fin.bank_statement_lines (tenant_id, id),
  CONSTRAINT upi_confirmations_confirmed_by_fk FOREIGN KEY (tenant_id, confirmed_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT upi_confirmations_match_kind_check
    CHECK (match_kind IN ('utr_exact','amount_date','manual'))
);
CREATE INDEX upi_confirmations_line_idx ON fin.upi_confirmations (tenant_id, statement_line_id);
CREATE TRIGGER upi_confirmations_append_only BEFORE UPDATE OR DELETE ON fin.upi_confirmations
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- FR-FEE-016: a confirmation states a fact about an issued UPI receipt; it never corrects one.
CREATE FUNCTION fin.tg_upi_confirmations_check() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  r record;
  l record;
BEGIN
  SELECT mode, status, amount, utr INTO r FROM fin.receipts
    WHERE tenant_id = NEW.tenant_id AND id = NEW.receipt_id FOR SHARE;
  SELECT amount, ref_kind, utr_or_ref, match_status INTO l FROM fin.bank_statement_lines
    WHERE tenant_id = NEW.tenant_id AND id = NEW.statement_line_id FOR UPDATE;
  IF r.mode IS DISTINCT FROM 'upi_qr' OR r.status IS DISTINCT FROM 'issued' THEN
    RAISE EXCEPTION 'only an issued UPI receipt is confirmed'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_receipt_state';
  END IF;
  IF l.amount IS DISTINCT FROM r.amount THEN
    RAISE EXCEPTION 'the bank amount differs from the receipt'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_amount_differs';
  END IF;
  IF l.ref_kind = 'utr' AND l.utr_or_ref IS DISTINCT FROM r.utr THEN
    RAISE EXCEPTION 'the bank UTR differs from the receipt'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_utr_differs';
  END IF;
  IF l.match_status = 'ignored' THEN
    RAISE EXCEPTION 'an ignored line is not confirmed'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_line_ignored';
  END IF;
  IF EXISTS (
    SELECT 1 FROM fin.upi_confirmations c JOIN fin.receipts x
      ON x.tenant_id = c.tenant_id AND x.id = c.receipt_id
    WHERE c.tenant_id = NEW.tenant_id AND c.statement_line_id = NEW.statement_line_id
      AND x.status = 'issued'
  ) THEN
    RAISE EXCEPTION 'this bank credit already confirms an issued receipt'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_line_taken';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER upi_confirmations_check BEFORE INSERT ON fin.upi_confirmations
  FOR EACH ROW EXECUTE FUNCTION fin.tg_upi_confirmations_check();
"""

# Columns sos_app may UPDATE (everything else is insert-only).
UPDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "fin.upi_settings": (
        "payee_name",
        "vpa",
        "confirm_within_days",
        "updated_by",
        "updated_at",
        "version",
    ),
    "fin.bank_statement_lines": (
        "narration_ciphertext",
        "narration_erased_at",
        "match_status",
        "match_kind",
        "receipt_id",
        "ignore_reason",
        "decided_by",
        "decided_at",
        "updated_at",
        "version",
    ),
}

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "fee.reconcile",
        "description": (
            "Import bank statements, confirm UPI receipts seen in the bank and work through "
            "reconciliation exceptions"
        ),
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


def _purge_sql(table: str) -> str:
    """ADR-0029: the offboarding purge deletes these rows as sos_purger (restrictive policy)."""
    return (
        f"GRANT SELECT, DELETE ON {table} TO sos_purger;\n"
        f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed());\n"
    )


def _grants_sql() -> str:
    lines: list[str] = []
    for table in TENANT_TABLES:
        lines.append(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM sos_app;")
        columns = UPDATE_COLUMNS.get(table)
        if columns:
            lines.append(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO sos_app;")
        lines.append(f"REVOKE ALL ON {table} FROM sos_readonly;")
        lines.append(f"GRANT SELECT ON {table} TO sos_readonly;")
    return "\n".join(lines)


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        op.execute(_purge_sql(table))
    op.execute(_grants_sql())
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
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")

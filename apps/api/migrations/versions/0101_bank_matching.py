"""FM-08 bank statement matching for cheques and bank transfers; cheque clearing status
(FR-FEE-126..150; ADR-0042 decision 9, P9; docs/05 §15.1).

Builds on ``0060_fees_upi``. The school is always the merchant; SchoolOS never holds money and
never posts to the ledger from a statement: a bank confirmation is a fact about a receipt, a person
confirms every one, and a returned cheque goes through the existing cancellation maker-checker.

- ``fin.bank_statement_lines`` gains two insert-only bank facts read from the statement:
  ``cheque_no`` (6 digits, from the cheque/reference column or a cheque narration) and
  ``transfer_ref`` (the NEFT/RTGS/IMPS reference of the narration, 6-35 letters and digits).
  ``sos_app`` gets no UPDATE on either (the column grants of 0060 stay as they are).
- ``match_kind`` (lines and confirmations) also allows ``cheque_exact`` and ``transfer_exact``.
- ``fin.upi_confirmations`` (the name is kept; it is the table of "seen in the bank" facts) now
  also confirms issued ``cheque`` and ``bank_transfer`` receipts. ``tg_upi_confirmations_check``
  is replaced: modes ``upi_qr``, ``cheque``, ``bank_transfer``; the UTR rule stays for UPI
  receipts; a line carrying a cheque number confirms only the cheque receipt with that number
  (``upi_confirmations_cheque_differs``); a returned cheque is never confirmed
  (``upi_confirmations_cheque_returned``).
- ``fin.cheque_status_events`` (new, **append-only**): ``deposited`` (a person took it to the
  bank), ``cleared`` (seen in a statement: ``source = 'statement'`` with the confirming line),
  ``returned`` (the bank returned it; ``cancellation_id`` names the ``cheque_dishonoured``
  cancellation request raised in the same transaction). One event per status per receipt; the
  order deposited -> cleared -> returned is checked by ``tg_cheque_status_events_check`` (a
  cheque can be returned after showing as cleared; nothing follows "returned").

Tenant-table rules as 0060 (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE
with ``tenant_isolation``, composite FKs, restrictive ``offboarding_purge`` for ``sos_purger``,
no ``definer_access``, nothing for ``sos_platform``). No new permission (``fee.collect`` and
``fee.reconcile`` cover it).

Expand-only (invariant 12). Downgrade drops ``fin.cheque_status_events`` (lossy: cheque deposit
and return history) and the two columns, and restores the 0060 checks and trigger; it is
**irreversible** (refused) while a confirmation of a cheque or bank-transfer receipt exists,
because the 0060 trigger and checks cannot hold such rows.

Revision ID: 0101_bank_matching
Revises: 0096_compliance_register
Create Date: 2026-11-12
"""

from __future__ import annotations

from alembic import op

revision = "0101_bank_matching"
down_revision = "0096_compliance_register"
branch_labels = None
depends_on = None

TABLE = "fin.cheque_status_events"

UPGRADE_SQL = r"""
ALTER TABLE fin.bank_statement_lines
  ADD COLUMN cheque_no text,
  ADD COLUMN transfer_ref text,
  ADD CONSTRAINT bank_statement_lines_cheque_no_shape
    CHECK (cheque_no IS NULL OR cheque_no ~ '^[0-9]{6}$'),
  ADD CONSTRAINT bank_statement_lines_transfer_ref_shape
    CHECK (transfer_ref IS NULL OR transfer_ref ~ '^[A-Za-z0-9]{6,35}$');
CREATE INDEX bank_statement_lines_cheque_idx ON fin.bank_statement_lines (tenant_id, cheque_no)
  WHERE cheque_no IS NOT NULL;

ALTER TABLE fin.bank_statement_lines DROP CONSTRAINT bank_statement_lines_match_kind_check;
ALTER TABLE fin.bank_statement_lines ADD CONSTRAINT bank_statement_lines_match_kind_check
  CHECK (match_kind IS NULL OR match_kind IN
    ('utr_exact','amount_date','manual','cheque_exact','transfer_exact'));
ALTER TABLE fin.upi_confirmations DROP CONSTRAINT upi_confirmations_match_kind_check;
ALTER TABLE fin.upi_confirmations ADD CONSTRAINT upi_confirmations_match_kind_check
  CHECK (match_kind IN ('utr_exact','amount_date','manual','cheque_exact','transfer_exact'));

-- Cheque status events (append-only) ----------------------------------------------------------

CREATE TABLE fin.cheque_status_events (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  receipt_id         uuid NOT NULL,
  status             text NOT NULL,
  event_date         date NOT NULL,
  source             text NOT NULL,
  statement_line_id  uuid,
  cancellation_id    uuid,
  recorded_by        uuid NOT NULL,
  recorded_at        timestamptz NOT NULL DEFAULT now(),
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT cheque_status_events_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT cheque_status_events_once UNIQUE (tenant_id, receipt_id, status),
  CONSTRAINT cheque_status_events_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT cheque_status_events_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT cheque_status_events_line_fk FOREIGN KEY (tenant_id, statement_line_id)
    REFERENCES fin.bank_statement_lines (tenant_id, id),
  CONSTRAINT cheque_status_events_cancellation_fk FOREIGN KEY (tenant_id, cancellation_id)
    REFERENCES fin.receipt_cancellations (tenant_id, id),
  CONSTRAINT cheque_status_events_recorded_by_fk FOREIGN KEY (tenant_id, recorded_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT cheque_status_events_status_check
    CHECK (status IN ('deposited','cleared','returned')),
  CONSTRAINT cheque_status_events_source_check CHECK (source IN ('manual','statement')),
  CONSTRAINT cheque_status_events_shape CHECK (
    (source = 'statement') = (statement_line_id IS NOT NULL)
    AND (source <> 'statement' OR status = 'cleared')
    AND (status = 'returned') = (cancellation_id IS NOT NULL))
);
CREATE INDEX cheque_status_events_receipt_idx
  ON fin.cheque_status_events (tenant_id, receipt_id, recorded_at);
CREATE TRIGGER cheque_status_events_append_only BEFORE UPDATE OR DELETE
  ON fin.cheque_status_events FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

CREATE FUNCTION fin.tg_cheque_status_events_check() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  r record;
  have text[];
BEGIN
  SELECT mode, status INTO r FROM fin.receipts
    WHERE tenant_id = NEW.tenant_id AND id = NEW.receipt_id FOR UPDATE;
  IF r.mode IS DISTINCT FROM 'cheque' THEN
    RAISE EXCEPTION 'only a cheque receipt has a cheque status'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cheque_status_events_not_cheque';
  END IF;
  SELECT coalesce(array_agg(status), ARRAY[]::text[]) INTO have FROM fin.cheque_status_events
    WHERE tenant_id = NEW.tenant_id AND receipt_id = NEW.receipt_id;
  IF 'returned' = ANY (have)
     OR (NEW.status = 'deposited' AND (cardinality(have) > 0 OR r.status <> 'issued'))
     OR (NEW.status = 'cleared' AND ('cleared' = ANY (have) OR r.status <> 'issued'))
     OR (NEW.status = 'returned' AND NOT ('deposited' = ANY (have) OR 'cleared' = ANY (have)))
  THEN
    RAISE EXCEPTION 'a cheque goes deposited, cleared, returned in that order, once each'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cheque_status_events_order';
  END IF;
  IF NEW.source = 'statement' AND NOT EXISTS (
    SELECT 1 FROM fin.upi_confirmations c
    WHERE c.tenant_id = NEW.tenant_id AND c.receipt_id = NEW.receipt_id
      AND c.statement_line_id = NEW.statement_line_id
  ) THEN
    RAISE EXCEPTION 'a cheque is cleared from the statement only with its bank confirmation'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cheque_status_events_unconfirmed';
  END IF;
  IF NEW.status = 'returned' AND NOT EXISTS (
    SELECT 1 FROM fin.receipt_cancellations x
    WHERE x.tenant_id = NEW.tenant_id AND x.id = NEW.cancellation_id
      AND x.receipt_id = NEW.receipt_id AND x.reason_code = 'cheque_dishonoured'
  ) THEN
    RAISE EXCEPTION 'a returned cheque names its cheque_dishonoured cancellation request'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'cheque_status_events_cancellation';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER cheque_status_events_check BEFORE INSERT ON fin.cheque_status_events
  FOR EACH ROW EXECUTE FUNCTION fin.tg_cheque_status_events_check();
"""

# FR-FEE-016 + FR-FEE-128: a confirmation states a fact about an issued UPI, cheque or bank
# transfer receipt; it never corrects one.
CHECK_FUNCTION_NEW = r"""
CREATE OR REPLACE FUNCTION fin.tg_upi_confirmations_check() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  r record;
  l record;
BEGIN
  SELECT mode, status, amount, utr, mode_details INTO r FROM fin.receipts
    WHERE tenant_id = NEW.tenant_id AND id = NEW.receipt_id FOR SHARE;
  SELECT amount, ref_kind, utr_or_ref, match_status, cheque_no INTO l
    FROM fin.bank_statement_lines
    WHERE tenant_id = NEW.tenant_id AND id = NEW.statement_line_id FOR UPDATE;
  IF r.mode IS NULL OR r.mode NOT IN ('upi_qr','cheque','bank_transfer')
     OR r.status IS DISTINCT FROM 'issued' THEN
    RAISE EXCEPTION 'only an issued UPI, cheque or bank transfer receipt is confirmed'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_receipt_state';
  END IF;
  IF l.amount IS DISTINCT FROM r.amount THEN
    RAISE EXCEPTION 'the bank amount differs from the receipt'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_amount_differs';
  END IF;
  IF r.mode = 'upi_qr' AND l.ref_kind = 'utr' AND l.utr_or_ref IS DISTINCT FROM r.utr THEN
    RAISE EXCEPTION 'the bank UTR differs from the receipt'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_utr_differs';
  END IF;
  IF r.mode = 'cheque' AND l.cheque_no IS NOT NULL
     AND l.cheque_no IS DISTINCT FROM (r.mode_details ->> 'cheque_number') THEN
    RAISE EXCEPTION 'the bank cheque number differs from the receipt'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_cheque_differs';
  END IF;
  IF r.mode = 'cheque' AND EXISTS (
    SELECT 1 FROM fin.cheque_status_events e
    WHERE e.tenant_id = NEW.tenant_id AND e.receipt_id = NEW.receipt_id AND e.status = 'returned'
  ) THEN
    RAISE EXCEPTION 'a returned cheque is not confirmed'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'upi_confirmations_cheque_returned';
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
"""

# The 0060 function, restored by the downgrade.
CHECK_FUNCTION_OLD = r"""
CREATE OR REPLACE FUNCTION fin.tg_upi_confirmations_check() RETURNS trigger
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
"""

DOWNGRADE_GUARD = r"""
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM fin.upi_confirmations c JOIN fin.receipts r
      ON r.tenant_id = c.tenant_id AND r.id = c.receipt_id
    WHERE r.mode <> 'upi_qr'
       OR c.match_kind IN ('cheque_exact','transfer_exact')
  ) OR EXISTS (
    SELECT 1 FROM fin.bank_statement_lines
    WHERE match_kind IN ('cheque_exact','transfer_exact')
  ) THEN
    RAISE EXCEPTION 'irreversible: cheque or bank transfer confirmations exist'
      USING ERRCODE = 'check_violation';
  END IF;
END;
$$;
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS fin.cheque_status_events;
DROP FUNCTION IF EXISTS fin.tg_cheque_status_events_check();
ALTER TABLE fin.upi_confirmations DROP CONSTRAINT upi_confirmations_match_kind_check;
ALTER TABLE fin.upi_confirmations ADD CONSTRAINT upi_confirmations_match_kind_check
  CHECK (match_kind IN ('utr_exact','amount_date','manual'));
ALTER TABLE fin.bank_statement_lines DROP CONSTRAINT bank_statement_lines_match_kind_check;
ALTER TABLE fin.bank_statement_lines ADD CONSTRAINT bank_statement_lines_match_kind_check
  CHECK (match_kind IS NULL OR match_kind IN ('utr_exact','amount_date','manual'));
DROP INDEX IF EXISTS fin.bank_statement_lines_cheque_idx;
ALTER TABLE fin.bank_statement_lines DROP COLUMN IF EXISTS transfer_ref;
ALTER TABLE fin.bank_statement_lines DROP COLUMN IF EXISTS cheque_no;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    op.execute(CHECK_FUNCTION_NEW)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = core.current_tenant()) "
        "WITH CHECK (tenant_id = core.current_tenant())"
    )
    # ADR-0029: the offboarding purge deletes these rows as sos_purger (restrictive policy).
    op.execute(f"GRANT SELECT, DELETE ON {TABLE} TO sos_purger")
    op.execute(
        f"CREATE POLICY offboarding_purge ON {TABLE} AS RESTRICTIVE FOR ALL TO sos_purger "
        "USING (core.tenant_purge_allowed())"
    )
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {TABLE} FROM sos_app")
    op.execute(f"REVOKE ALL ON {TABLE} FROM sos_readonly")
    op.execute(f"GRANT SELECT ON {TABLE} TO sos_readonly")


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARD)
    op.execute(DOWNGRADE_SQL)
    op.execute(CHECK_FUNCTION_OLD)

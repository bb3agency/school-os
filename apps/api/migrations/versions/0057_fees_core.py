"""R2-1B fees core: fee heads, plans, accounts, ledger, concessions, adjustments, receipts,
cancellations, refunds and day close (ADR-0042; FR-FEE-001..014, FR-FEE-018..030; docs/05 §15.1).

Every table lives in the tenant schema ``fin`` (bootstrap.sql, ADR-0042 P10) and follows the
tenant-table rules: ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge`` policy with ``SELECT,
DELETE`` for ``sos_purger`` (ADR-0029), no ``definer_access``, nothing for ``sos_platform``.
Money is ``numeric(14,2)`` INR (FR-FEE-028).

- ``fin.fee_settings`` (one row per school): receipt paper size (A5 default, D17), GST note,
  reminder defaults for the later sending wave.
- ``fin.fee_heads``: unique code per school, kind, ``gst_exempt`` (taxable heads are never
  collected, FR-FEE-023), active. Never deleted (deactivate).
- ``fin.fee_plans`` + ``fin.fee_plan_lines`` + ``fin.fee_plan_instalments``: one plan per
  academic year, class and optional category, versioned; ``draft -> active -> superseded``.
  Trigger ``fee_plans_frozen``: a non-draft plan never changes (only ``active -> superseded``);
  lines and instalments change only while their plan is a draft (FR-FEE-004).
- ``fin.student_fee_accounts``: one per student and academic year.
- ``fin.ledger_entries``: **append-only** (FR-FEE-006). ``sos_app`` has SELECT and INSERT only;
  trigger ``ledger_entries_append_only`` refuses UPDATE and DELETE except in the offboarding purge.
  Sign by kind and exactly the source columns of each kind by CHECK; a charge is unique per
  (account, instalment, head) and a late fee per (account, key), so applying a plan or posting
  late fees twice adds nothing (FR-FEE-005, FR-FEE-009).
- ``fin.concessions``, ``fin.adjustments``: maker-checker requests (ADR-0010):
  ``CHECK (decided_by IS NULL OR decided_by <> requested_by)``; a decided request never changes.
- ``fin.receipt_counters``: last number per (school, series ``RCT``/``RFD``, financial year);
  allocated with ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` in the issuing transaction,
  which locks the row (gap-free, never reused; numbers never go down).
- ``fin.receipts`` + ``fin.receipt_allocations``: the frozen receipt (content JSON + SHA-256,
  FR-FEE-011) and where its amount went. Trigger ``receipts_frozen`` allows only ``issued ->
  cancelled``, the PDF state and the document link; allocations are append-only and a deferred
  constraint trigger checks at commit that allocations and the receipt's payment entries both sum
  to its amount (FR-FEE-006, FR-FEE-010). A UPI reference (UTR) is on at most one issued receipt
  (partial unique index, FR-FEE-014).
- ``fin.receipt_cancellations``: maker-checker; one pending or approved per receipt.
- ``fin.refunds``: maker-checker vouchers in their own series (FR-FEE-013).
- ``fin.day_closes``: one per school and IST date; append-only (FR-FEE-019).

Also: ``kb.documents.purpose`` accepts ``fee_receipt`` (generated receipt PDFs, never uploaded,
never indexed) and the eight ``fee.*`` permissions are upserted into ``core.permissions``. Role
grants of existing schools come from ``python -m app.identity.sync_system_roles`` (ADR-0022).

Expand-only (invariant 12). Downgrade drops every ``fin`` table of this revision (lossy: the fee
ledger and receipt book are removed; receipt PDFs stay as documents), re-adds the old documents
purpose CHECK as ``NOT VALID`` and deletes the permission keys unless a role still holds them.

Revision ID: 0057_fees_core
Revises: 0056_certificate_verifications
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057_fees_core"
down_revision = "0056_certificate_verifications"
branch_labels = None
depends_on = None

# Children before parents (downgrade drops in this order; the purge registers the same order).
TENANT_TABLES = (
    "fin.day_closes",
    "fin.ledger_entries",
    "fin.refunds",
    "fin.receipt_cancellations",
    "fin.receipt_allocations",
    "fin.receipts",
    "fin.receipt_counters",
    "fin.adjustments",
    "fin.concessions",
    "fin.student_fee_accounts",
    "fin.fee_plan_instalments",
    "fin.fee_plan_lines",
    "fin.fee_plans",
    "fin.fee_heads",
    "fin.fee_settings",
)

FUNCTIONS = (
    "fin.tg_append_only()",
    "fin.tg_counters_never_down()",
    "fin.tg_fee_plans_frozen()",
    "fin.tg_plan_children_draft_only()",
    "fin.tg_requests_frozen()",
    "fin.tg_receipts_frozen()",
    "fin.tg_receipt_sums()",
    "fin.tg_refunds_frozen()",
)

PURPOSES_BEFORE = (
    "('evidence','register_scan','circular','policy','other','import_file','certificate')"
)

UPGRADE_SQL = r"""
-- Shared trigger functions ---------------------------------------------------------------------

-- Append-only rows: UPDATE always refused; DELETE/TRUNCATE only by the offboarding purge.
CREATE FUNCTION fin.tg_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME
    USING ERRCODE = 'check_violation', CONSTRAINT = TG_TABLE_NAME || '_append_only';
END;
$$;

CREATE FUNCTION fin.tg_counters_never_down() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.last_no < OLD.last_no OR NEW.tenant_id <> OLD.tenant_id OR NEW.series <> OLD.series
     OR NEW.financial_year <> OLD.financial_year THEN
    RAISE EXCEPTION 'receipt and voucher numbers are never reused'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'receipt_counters_never_down';
  END IF;
  RETURN NEW;
END;
$$;

-- Settings -------------------------------------------------------------------------------------

CREATE TABLE fin.fee_settings (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  paper_size           text NOT NULL DEFAULT 'A5',
  gst_note             text NOT NULL DEFAULT 'Education services: exempt from GST',
  reminder_offsets     int[] NOT NULL DEFAULT '{-3,0,7}',
  min_reminder_amount  numeric(14,2) NOT NULL DEFAULT 0,
  updated_by           uuid,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1,
  CONSTRAINT fee_settings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT fee_settings_one_per_school UNIQUE (tenant_id),
  CONSTRAINT fee_settings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT fee_settings_updated_by_fk FOREIGN KEY (tenant_id, updated_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT fee_settings_paper_size_check CHECK (paper_size IN ('A5','A4')),
  CONSTRAINT fee_settings_gst_note_length CHECK (char_length(gst_note) BETWEEN 1 AND 200),
  CONSTRAINT fee_settings_offsets_shape CHECK (cardinality(reminder_offsets) <= 5
    AND -60 <= ALL (reminder_offsets) AND 60 >= ALL (reminder_offsets)),
  CONSTRAINT fee_settings_min_reminder CHECK (min_reminder_amount >= 0),
  CONSTRAINT fee_settings_version_positive CHECK (version >= 1)
);
CREATE TRIGGER fee_settings_set_updated_at BEFORE UPDATE ON fin.fee_settings
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- Heads ----------------------------------------------------------------------------------------

CREATE TABLE fin.fee_heads (
  id          uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  code        text NOT NULL,
  name        text NOT NULL,
  kind        text NOT NULL,
  gst_exempt  boolean NOT NULL DEFAULT true,
  active      boolean NOT NULL DEFAULT true,
  sort_order  int NOT NULL DEFAULT 100,
  created_by  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  version     int NOT NULL DEFAULT 1,
  CONSTRAINT fee_heads_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT fee_heads_code_unique UNIQUE (tenant_id, code),
  CONSTRAINT fee_heads_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT fee_heads_created_by_fk FOREIGN KEY (tenant_id, created_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT fee_heads_code_shape CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,19}$'),
  CONSTRAINT fee_heads_name_length CHECK (char_length(name) BETWEEN 1 AND 80),
  CONSTRAINT fee_heads_kind_check
    CHECK (kind IN ('tuition','admission','exam','books','transport','other')),
  CONSTRAINT fee_heads_sort_order_range CHECK (sort_order BETWEEN 0 AND 10000),
  CONSTRAINT fee_heads_version_positive CHECK (version >= 1)
);
CREATE TRIGGER fee_heads_set_updated_at BEFORE UPDATE ON fin.fee_heads
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- Plans ----------------------------------------------------------------------------------------

CREATE TABLE fin.fee_plans (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  academic_year_id  uuid NOT NULL,
  class_id          uuid NOT NULL,
  category_code     text,
  plan_version      int NOT NULL DEFAULT 1,
  status            text NOT NULL DEFAULT 'draft',
  name              text NOT NULL,
  late_fee_rule     jsonb NOT NULL DEFAULT '{"kind": "none"}'::jsonb,
  previous_plan_id  uuid,
  created_by        uuid NOT NULL,
  activated_by      uuid,
  activated_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1,
  CONSTRAINT fee_plans_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT fee_plans_version_unique UNIQUE NULLS NOT DISTINCT
    (tenant_id, academic_year_id, class_id, category_code, plan_version),
  CONSTRAINT fee_plans_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT fee_plans_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT fee_plans_class_fk FOREIGN KEY (tenant_id, class_id)
    REFERENCES core.classes (tenant_id, id),
  CONSTRAINT fee_plans_previous_fk FOREIGN KEY (tenant_id, previous_plan_id)
    REFERENCES fin.fee_plans (tenant_id, id),
  CONSTRAINT fee_plans_created_by_fk FOREIGN KEY (tenant_id, created_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT fee_plans_activated_by_fk FOREIGN KEY (tenant_id, activated_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT fee_plans_status_check CHECK (status IN ('draft','active','superseded')),
  CONSTRAINT fee_plans_category_shape
    CHECK (category_code IS NULL OR category_code ~ '^[a-z][a-z0-9_]{0,31}$'),
  CONSTRAINT fee_plans_name_length CHECK (char_length(name) BETWEEN 1 AND 80),
  CONSTRAINT fee_plans_plan_version_range CHECK (plan_version BETWEEN 1 AND 999),
  CONSTRAINT fee_plans_late_fee_rule_object CHECK (jsonb_typeof(late_fee_rule) = 'object'
    AND late_fee_rule->>'kind' IN ('none','flat_per_instalment','per_day')),
  CONSTRAINT fee_plans_activation CHECK ((status = 'draft') = (activated_at IS NULL)
    AND (activated_at IS NULL) = (activated_by IS NULL)),
  CONSTRAINT fee_plans_version_positive CHECK (version >= 1)
);
-- One active plan per (year, class, category); NULL category = everyone.
CREATE UNIQUE INDEX fee_plans_one_active ON fin.fee_plans
  (tenant_id, academic_year_id, class_id, coalesce(category_code, '')) WHERE status = 'active';
CREATE INDEX fee_plans_year_class_idx ON fin.fee_plans (tenant_id, academic_year_id, class_id);
CREATE TRIGGER fee_plans_set_updated_at BEFORE UPDATE ON fin.fee_plans
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- FR-FEE-004: a plan is edited only as a draft; an active plan only becomes superseded.
CREATE FUNCTION fin.tg_fee_plans_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (NEW.tenant_id, NEW.academic_year_id, NEW.class_id, NEW.category_code, NEW.plan_version,
      NEW.previous_plan_id, NEW.created_by)
     IS DISTINCT FROM
     (OLD.tenant_id, OLD.academic_year_id, OLD.class_id, OLD.category_code, OLD.plan_version,
      OLD.previous_plan_id, OLD.created_by) THEN
    RAISE EXCEPTION 'a fee plan keeps its year, class, category and version'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_plans_frozen';
  END IF;
  IF OLD.status = 'draft' THEN
    IF NEW.status NOT IN ('draft', 'active') THEN
      RAISE EXCEPTION 'a draft plan is activated before it is superseded'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_plans_frozen';
    END IF;
    RETURN NEW;
  END IF;
  IF (NEW.name, NEW.late_fee_rule, NEW.activated_by, NEW.activated_at)
     IS DISTINCT FROM (OLD.name, OLD.late_fee_rule, OLD.activated_by, OLD.activated_at)
     OR NOT (NEW.status = OLD.status OR (OLD.status = 'active' AND NEW.status = 'superseded'))
  THEN
    RAISE EXCEPTION 'an active fee plan is in use and never changes; make a new version'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_plans_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER fee_plans_frozen BEFORE UPDATE ON fin.fee_plans
  FOR EACH ROW EXECUTE FUNCTION fin.tg_fee_plans_frozen();

CREATE TABLE fin.fee_plan_lines (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL,
  plan_id        uuid NOT NULL,
  head_id        uuid NOT NULL,
  annual_amount  numeric(14,2) NOT NULL,
  sort_order     int NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fee_plan_lines_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT fee_plan_lines_head_unique UNIQUE (tenant_id, plan_id, head_id),
  CONSTRAINT fee_plan_lines_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT fee_plan_lines_plan_fk FOREIGN KEY (tenant_id, plan_id)
    REFERENCES fin.fee_plans (tenant_id, id),
  CONSTRAINT fee_plan_lines_head_fk FOREIGN KEY (tenant_id, head_id)
    REFERENCES fin.fee_heads (tenant_id, id),
  CONSTRAINT fee_plan_lines_amount_positive CHECK (annual_amount > 0),
  CONSTRAINT fee_plan_lines_sort_order_range CHECK (sort_order BETWEEN 0 AND 1000)
);

CREATE TABLE fin.fee_plan_instalments (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL,
  plan_id        uuid NOT NULL,
  instalment_no  int NOT NULL,
  label          text NOT NULL,
  due_on         date NOT NULL,
  amounts        jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fee_plan_instalments_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT fee_plan_instalments_no_unique UNIQUE (tenant_id, plan_id, instalment_no),
  CONSTRAINT fee_plan_instalments_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT fee_plan_instalments_plan_fk FOREIGN KEY (tenant_id, plan_id)
    REFERENCES fin.fee_plans (tenant_id, id),
  CONSTRAINT fee_plan_instalments_no_range CHECK (instalment_no BETWEEN 1 AND 24),
  CONSTRAINT fee_plan_instalments_label_length CHECK (char_length(label) BETWEEN 1 AND 40),
  CONSTRAINT fee_plan_instalments_amounts_object CHECK (jsonb_typeof(amounts) = 'object')
);

-- Lines and instalments change only while their plan is a draft (FR-FEE-004).
CREATE FUNCTION fin.tg_plan_children_draft_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  row_tenant uuid;
  row_plan uuid;
  plan_status text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF core.tenant_purge_allowed() THEN
      RETURN OLD;
    END IF;
    row_tenant := OLD.tenant_id;
    row_plan := OLD.plan_id;
  ELSE
    row_tenant := NEW.tenant_id;
    row_plan := NEW.plan_id;
    IF TG_OP = 'UPDATE' AND (NEW.plan_id <> OLD.plan_id OR NEW.tenant_id <> OLD.tenant_id) THEN
      RAISE EXCEPTION 'plan lines stay with their plan'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_plans_frozen';
    END IF;
  END IF;
  SELECT p.status INTO plan_status FROM fin.fee_plans p
   WHERE p.tenant_id = row_tenant AND p.id = row_plan;
  IF plan_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'only a draft fee plan can be edited; make a new version'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_plans_frozen';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER fee_plan_lines_draft_only BEFORE INSERT OR UPDATE OR DELETE ON fin.fee_plan_lines
  FOR EACH ROW EXECUTE FUNCTION fin.tg_plan_children_draft_only();
CREATE TRIGGER fee_plan_instalments_draft_only
  BEFORE INSERT OR UPDATE OR DELETE ON fin.fee_plan_instalments
  FOR EACH ROW EXECUTE FUNCTION fin.tg_plan_children_draft_only();

-- Accounts -------------------------------------------------------------------------------------

CREATE TABLE fin.student_fee_accounts (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  student_id        uuid NOT NULL,
  academic_year_id  uuid NOT NULL,
  plan_id           uuid NOT NULL,
  category_code     text,
  status            text NOT NULL DEFAULT 'open',
  opened_by         uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1,
  CONSTRAINT student_fee_accounts_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT student_fee_accounts_one_per_year UNIQUE (tenant_id, student_id, academic_year_id),
  CONSTRAINT student_fee_accounts_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT student_fee_accounts_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT student_fee_accounts_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT student_fee_accounts_plan_fk FOREIGN KEY (tenant_id, plan_id)
    REFERENCES fin.fee_plans (tenant_id, id),
  CONSTRAINT student_fee_accounts_opened_by_fk FOREIGN KEY (tenant_id, opened_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT student_fee_accounts_status_check CHECK (status IN ('open','closed')),
  CONSTRAINT student_fee_accounts_category_shape
    CHECK (category_code IS NULL OR category_code ~ '^[a-z][a-z0-9_]{0,31}$'),
  CONSTRAINT student_fee_accounts_version_positive CHECK (version >= 1)
);
CREATE INDEX student_fee_accounts_student_idx ON fin.student_fee_accounts (tenant_id, student_id);
CREATE INDEX student_fee_accounts_plan_idx ON fin.student_fee_accounts (tenant_id, plan_id);
CREATE TRIGGER student_fee_accounts_set_updated_at BEFORE UPDATE ON fin.student_fee_accounts
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
CREATE TRIGGER student_fee_accounts_no_delete BEFORE DELETE ON fin.student_fee_accounts
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- Maker-checker requests: concessions and adjustments (ADR-0010) ----------------------------

CREATE TABLE fin.concessions (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  account_id            uuid NOT NULL,
  concession_type       text NOT NULL,
  basis                 text NOT NULL,
  value                 numeric(14,2) NOT NULL,
  targets               jsonb NOT NULL,
  reason                text NOT NULL,
  evidence_document_id  uuid,
  status                text NOT NULL DEFAULT 'pending',
  total_amount          numeric(14,2),
  requested_by          uuid NOT NULL,
  requested_at          timestamptz NOT NULL DEFAULT now(),
  decided_by            uuid,
  decided_at            timestamptz,
  decision_note         text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1,
  CONSTRAINT concessions_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT concessions_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT concessions_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES fin.student_fee_accounts (tenant_id, id),
  CONSTRAINT concessions_evidence_fk FOREIGN KEY (tenant_id, evidence_document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT concessions_requested_by_fk FOREIGN KEY (tenant_id, requested_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT concessions_decided_by_fk FOREIGN KEY (tenant_id, decided_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT concessions_type_check
    CHECK (concession_type IN ('sibling','staff_ward','merit','hardship','rte','other')),
  CONSTRAINT concessions_basis_check CHECK (basis IN ('amount','percent')),
  CONSTRAINT concessions_value_range
    CHECK (value > 0 AND (basis <> 'percent' OR value <= 100)),
  CONSTRAINT concessions_targets_array
    CHECK (jsonb_typeof(targets) = 'array' AND jsonb_array_length(targets) BETWEEN 1 AND 300),
  CONSTRAINT concessions_reason_length CHECK (char_length(reason) BETWEEN 10 AND 1000),
  CONSTRAINT concessions_status_check
    CHECK (status IN ('pending','approved','rejected','withdrawn')),
  -- ADR-0010 / 07 §6.3: whoever decides a request never made it.
  CONSTRAINT concessions_maker_checker CHECK (decided_by IS NULL OR decided_by <> requested_by),
  CONSTRAINT concessions_decision CHECK (
    (status IN ('approved','rejected')) = (decided_by IS NOT NULL)
    AND (decided_at IS NULL) = (status IN ('pending','withdrawn'))
    AND (status <> 'rejected' OR decision_note IS NOT NULL)
    AND (decision_note IS NULL OR char_length(decision_note) BETWEEN 10 AND 1000)),
  CONSTRAINT concessions_total CHECK ((status = 'approved') = (total_amount IS NOT NULL)
    AND (total_amount IS NULL OR total_amount > 0)),
  CONSTRAINT concessions_version_positive CHECK (version >= 1)
);
CREATE INDEX concessions_account_idx ON fin.concessions (tenant_id, account_id);
CREATE INDEX concessions_pending_idx ON fin.concessions (tenant_id, requested_at)
  WHERE status = 'pending';

CREATE TABLE fin.adjustments (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  account_id        uuid NOT NULL,
  instalment_no     int NOT NULL,
  head_id           uuid,
  amount            numeric(14,2) NOT NULL,
  reason_code       text NOT NULL,
  reason            text NOT NULL,
  source_plan_id    uuid,
  status            text NOT NULL DEFAULT 'pending',
  requested_by      uuid NOT NULL,
  requested_at      timestamptz NOT NULL DEFAULT now(),
  decided_by        uuid,
  decided_at        timestamptz,
  decision_note     text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1,
  CONSTRAINT adjustments_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT adjustments_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT adjustments_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES fin.student_fee_accounts (tenant_id, id),
  CONSTRAINT adjustments_head_fk FOREIGN KEY (tenant_id, head_id)
    REFERENCES fin.fee_heads (tenant_id, id),
  CONSTRAINT adjustments_source_plan_fk FOREIGN KEY (tenant_id, source_plan_id)
    REFERENCES fin.fee_plans (tenant_id, id),
  CONSTRAINT adjustments_requested_by_fk FOREIGN KEY (tenant_id, requested_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT adjustments_decided_by_fk FOREIGN KEY (tenant_id, decided_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT adjustments_instalment_range CHECK (instalment_no BETWEEN 1 AND 24),
  CONSTRAINT adjustments_amount_nonzero CHECK (amount <> 0),
  CONSTRAINT adjustments_reason_code_check CHECK (reason_code IN
    ('late_fee_waiver','plan_change','concession_reversal','correction','other')),
  -- A late-fee waiver reduces the late-fee line (no head); other adjustments name a head.
  CONSTRAINT adjustments_waiver_shape CHECK (
    (reason_code = 'late_fee_waiver') = (head_id IS NULL)
    AND (reason_code <> 'late_fee_waiver' OR amount < 0)),
  CONSTRAINT adjustments_reason_length CHECK (char_length(reason) BETWEEN 10 AND 1000),
  CONSTRAINT adjustments_status_check
    CHECK (status IN ('pending','approved','rejected','withdrawn')),
  CONSTRAINT adjustments_maker_checker CHECK (decided_by IS NULL OR decided_by <> requested_by),
  CONSTRAINT adjustments_decision CHECK (
    (status IN ('approved','rejected')) = (decided_by IS NOT NULL)
    AND (decided_at IS NULL) = (status IN ('pending','withdrawn'))
    AND (status <> 'rejected' OR decision_note IS NOT NULL)
    AND (decision_note IS NULL OR char_length(decision_note) BETWEEN 10 AND 1000)),
  CONSTRAINT adjustments_version_positive CHECK (version >= 1)
);
CREATE INDEX adjustments_account_idx ON fin.adjustments (tenant_id, account_id);
CREATE INDEX adjustments_pending_idx ON fin.adjustments (tenant_id, requested_at)
  WHERE status = 'pending';
-- One pending plan-change proposal per account, instalment and head (FR-FEE-004).
CREATE UNIQUE INDEX adjustments_one_plan_change ON fin.adjustments
  (tenant_id, account_id, source_plan_id, instalment_no, head_id)
  WHERE source_plan_id IS NOT NULL AND status IN ('pending','approved');

-- What was requested never changes; a decided request never changes again.
CREATE FUNCTION fin.tg_requests_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (to_jsonb(NEW) - ARRAY['status','decided_by','decided_at','decision_note','total_amount',
                            'cancelled_on','updated_at','version'])
     IS DISTINCT FROM
     (to_jsonb(OLD) - ARRAY['status','decided_by','decided_at','decision_note','total_amount',
                            'cancelled_on','updated_at','version']) THEN
    RAISE EXCEPTION 'a fee request never changes after it is made'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_requests_frozen';
  END IF;
  IF OLD.status <> 'pending' THEN
    RAISE EXCEPTION 'this request was already decided'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_requests_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER concessions_frozen BEFORE UPDATE ON fin.concessions
  FOR EACH ROW EXECUTE FUNCTION fin.tg_requests_frozen();
CREATE TRIGGER adjustments_frozen BEFORE UPDATE ON fin.adjustments
  FOR EACH ROW EXECUTE FUNCTION fin.tg_requests_frozen();
CREATE TRIGGER concessions_set_updated_at BEFORE UPDATE ON fin.concessions
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
CREATE TRIGGER adjustments_set_updated_at BEFORE UPDATE ON fin.adjustments
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
CREATE TRIGGER concessions_no_delete BEFORE DELETE ON fin.concessions
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();
CREATE TRIGGER adjustments_no_delete BEFORE DELETE ON fin.adjustments
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- Receipts -------------------------------------------------------------------------------------

CREATE TABLE fin.receipt_counters (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  series          text NOT NULL,
  financial_year  text NOT NULL,
  last_no         int NOT NULL DEFAULT 0,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT receipt_counters_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT receipt_counters_one_per_series_year UNIQUE (tenant_id, series, financial_year),
  CONSTRAINT receipt_counters_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT receipt_counters_series_check CHECK (series IN ('RCT','RFD')),
  CONSTRAINT receipt_counters_year_shape CHECK (financial_year ~ '^[0-9]{4}-[0-9]{2}$'),
  CONSTRAINT receipt_counters_last_no_range CHECK (last_no BETWEEN 0 AND 999999)
);
CREATE TRIGGER receipt_counters_never_down BEFORE UPDATE ON fin.receipt_counters
  FOR EACH ROW EXECUTE FUNCTION fin.tg_counters_never_down();
CREATE TRIGGER receipt_counters_append_only BEFORE DELETE ON fin.receipt_counters
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

CREATE TABLE fin.receipts (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  account_id           uuid NOT NULL,
  financial_year       text NOT NULL,
  receipt_no           int NOT NULL,
  receipt_number       text NOT NULL,
  receipt_date         date NOT NULL,
  amount               numeric(14,2) NOT NULL,
  mode                 text NOT NULL,
  mode_details         jsonb NOT NULL DEFAULT '{}'::jsonb,
  utr                  text,
  provider_payment_id  text,
  payment_intent_id    uuid,
  collected_by         uuid,
  issued_at            timestamptz NOT NULL DEFAULT now(),
  content              jsonb NOT NULL,
  content_sha256       bytea NOT NULL,
  status               text NOT NULL DEFAULT 'issued',
  document_id          uuid,
  pdf_status           text NOT NULL DEFAULT 'queued',
  pdf_error            text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1,
  CONSTRAINT receipts_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT receipts_number_unique UNIQUE (tenant_id, financial_year, receipt_no),
  CONSTRAINT receipts_number_text_unique UNIQUE (tenant_id, receipt_number),
  CONSTRAINT receipts_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT receipts_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES fin.student_fee_accounts (tenant_id, id),
  CONSTRAINT receipts_collected_by_fk FOREIGN KEY (tenant_id, collected_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT receipts_document_fk FOREIGN KEY (tenant_id, document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT receipts_year_shape CHECK (financial_year ~ '^[0-9]{4}-[0-9]{2}$'),
  CONSTRAINT receipts_no_range CHECK (receipt_no BETWEEN 1 AND 999999),
  CONSTRAINT receipts_number_shape CHECK (receipt_number ~ '^[A-Za-z0-9][A-Za-z0-9/_.-]{0,39}$'),
  CONSTRAINT receipts_amount_positive CHECK (amount > 0),
  CONSTRAINT receipts_mode_check
    CHECK (mode IN ('cash','cheque','bank_transfer','upi_qr','razorpay')),
  CONSTRAINT receipts_mode_details_object CHECK (jsonb_typeof(mode_details) = 'object'),
  -- FR-FEE-014: a UPI receipt carries its 12-digit UTR; no other mode does.
  CONSTRAINT receipts_utr_shape CHECK ((mode = 'upi_qr') = (utr IS NOT NULL)
    AND (utr IS NULL OR utr ~ '^[0-9]{12}$')),
  CONSTRAINT receipts_provider_payment
    CHECK ((mode = 'razorpay') = (provider_payment_id IS NOT NULL)
    AND (provider_payment_id IS NULL OR provider_payment_id ~ '^[A-Za-z0-9_]{1,40}$')),
  -- Counter receipts name who took the money; online ones are issued by the system.
  CONSTRAINT receipts_collector CHECK (mode = 'razorpay' OR collected_by IS NOT NULL),
  CONSTRAINT receipts_content_object CHECK (jsonb_typeof(content) = 'object'),
  CONSTRAINT receipts_sha256_length CHECK (octet_length(content_sha256) = 32),
  CONSTRAINT receipts_status_check CHECK (status IN ('issued','cancelled')),
  CONSTRAINT receipts_pdf_status_check CHECK (pdf_status IN ('queued','ready','failed')),
  CONSTRAINT receipts_pdf_ready CHECK (pdf_status <> 'ready' OR document_id IS NOT NULL),
  CONSTRAINT receipts_pdf_error_shape
    CHECK ((pdf_error IS NULL OR pdf_error ~ '^[a-z][a-z0-9_]{0,63}$')
           AND (pdf_status = 'failed') = (pdf_error IS NOT NULL)),
  CONSTRAINT receipts_version_positive CHECK (version >= 1)
);
CREATE INDEX receipts_date_idx ON fin.receipts (tenant_id, receipt_date, receipt_no);
CREATE INDEX receipts_account_idx ON fin.receipts (tenant_id, account_id);
CREATE INDEX receipts_list_idx ON fin.receipts (tenant_id, issued_at DESC, id DESC);
CREATE UNIQUE INDEX receipts_provider_payment_unique ON fin.receipts
  (tenant_id, provider_payment_id) WHERE provider_payment_id IS NOT NULL;
CREATE UNIQUE INDEX receipts_utr_unique ON fin.receipts (tenant_id, utr)
  WHERE mode = 'upi_qr' AND status = 'issued';
CREATE INDEX receipts_document_idx ON fin.receipts (tenant_id, document_id)
  WHERE document_id IS NOT NULL;
CREATE TRIGGER receipts_set_updated_at BEFORE UPDATE ON fin.receipts
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- FR-FEE-011: a receipt is never edited. Only issued -> cancelled, the PDF state and the
-- document link (once) change.
CREATE FUNCTION fin.tg_receipts_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (to_jsonb(NEW) - ARRAY['status','document_id','pdf_status','pdf_error','updated_at','version'])
     IS DISTINCT FROM
     (to_jsonb(OLD) - ARRAY['status','document_id','pdf_status','pdf_error','updated_at','version'])
     OR NOT (NEW.status = OLD.status OR (OLD.status = 'issued' AND NEW.status = 'cancelled'))
     OR (OLD.document_id IS NOT NULL AND NEW.document_id IS DISTINCT FROM OLD.document_id)
  THEN
    RAISE EXCEPTION 'a receipt is never edited; cancel it instead'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'receipts_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER receipts_frozen BEFORE UPDATE ON fin.receipts
  FOR EACH ROW EXECUTE FUNCTION fin.tg_receipts_frozen();
CREATE TRIGGER receipts_append_only BEFORE DELETE ON fin.receipts
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

CREATE TABLE fin.receipt_allocations (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL,
  receipt_id     uuid NOT NULL,
  instalment_no  int NOT NULL,
  head_id        uuid,
  amount         numeric(14,2) NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT receipt_allocations_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT receipt_allocations_line_unique UNIQUE NULLS NOT DISTINCT
    (tenant_id, receipt_id, instalment_no, head_id),
  CONSTRAINT receipt_allocations_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT receipt_allocations_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT receipt_allocations_head_fk FOREIGN KEY (tenant_id, head_id)
    REFERENCES fin.fee_heads (tenant_id, id),
  CONSTRAINT receipt_allocations_instalment_range CHECK (instalment_no BETWEEN 1 AND 24),
  CONSTRAINT receipt_allocations_amount_positive CHECK (amount > 0)
);
CREATE INDEX receipt_allocations_receipt_idx ON fin.receipt_allocations (tenant_id, receipt_id);
CREATE TRIGGER receipt_allocations_append_only
  BEFORE UPDATE OR DELETE ON fin.receipt_allocations
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- Cancellations and refunds -----------------------------------------------------------------

CREATE TABLE fin.receipt_cancellations (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL,
  receipt_id     uuid NOT NULL,
  reason_code    text NOT NULL,
  reason         text NOT NULL,
  status         text NOT NULL DEFAULT 'pending',
  requested_by   uuid NOT NULL,
  requested_at   timestamptz NOT NULL DEFAULT now(),
  decided_by     uuid,
  decided_at     timestamptz,
  decision_note  text,
  cancelled_on   date,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  version        int NOT NULL DEFAULT 1,
  CONSTRAINT receipt_cancellations_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT receipt_cancellations_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT receipt_cancellations_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT receipt_cancellations_requested_by_fk FOREIGN KEY (tenant_id, requested_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT receipt_cancellations_decided_by_fk FOREIGN KEY (tenant_id, decided_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT receipt_cancellations_reason_code_check CHECK (reason_code IN
    ('wrong_student','wrong_amount','cheque_dishonoured','duplicate_entry','other')),
  CONSTRAINT receipt_cancellations_reason_length CHECK (char_length(reason) BETWEEN 10 AND 1000),
  CONSTRAINT receipt_cancellations_status_check
    CHECK (status IN ('pending','approved','rejected','withdrawn')),
  -- ADR-0042 decision 5, D17: never self-cancel.
  CONSTRAINT receipt_cancellations_maker_checker
    CHECK (decided_by IS NULL OR decided_by <> requested_by),
  CONSTRAINT receipt_cancellations_decision CHECK (
    (status IN ('approved','rejected')) = (decided_by IS NOT NULL)
    AND (decided_at IS NULL) = (status IN ('pending','withdrawn'))
    AND (status <> 'rejected' OR decision_note IS NOT NULL)
    AND (decision_note IS NULL OR char_length(decision_note) BETWEEN 10 AND 1000)
    AND (status = 'approved') = (cancelled_on IS NOT NULL)),
  CONSTRAINT receipt_cancellations_version_positive CHECK (version >= 1)
);
CREATE UNIQUE INDEX receipt_cancellations_one_live ON fin.receipt_cancellations
  (tenant_id, receipt_id) WHERE status IN ('pending','approved');
CREATE INDEX receipt_cancellations_pending_idx ON fin.receipt_cancellations
  (tenant_id, requested_at) WHERE status = 'pending';
CREATE INDEX receipt_cancellations_day_idx ON fin.receipt_cancellations
  (tenant_id, cancelled_on) WHERE cancelled_on IS NOT NULL;
CREATE TRIGGER receipt_cancellations_frozen BEFORE UPDATE ON fin.receipt_cancellations
  FOR EACH ROW EXECUTE FUNCTION fin.tg_requests_frozen();
CREATE TRIGGER receipt_cancellations_set_updated_at BEFORE UPDATE ON fin.receipt_cancellations
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
CREATE TRIGGER receipt_cancellations_no_delete BEFORE DELETE ON fin.receipt_cancellations
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

CREATE TABLE fin.refunds (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  receipt_id          uuid NOT NULL,
  account_id          uuid NOT NULL,
  amount              numeric(14,2) NOT NULL,
  mode                text NOT NULL,
  mode_details        jsonb NOT NULL DEFAULT '{}'::jsonb,
  reason              text NOT NULL,
  status              text NOT NULL DEFAULT 'pending',
  requested_by        uuid NOT NULL,
  requested_at        timestamptz NOT NULL DEFAULT now(),
  decided_by          uuid,
  decided_at          timestamptz,
  decision_note       text,
  financial_year      text,
  voucher_no          int,
  voucher_number      text,
  voucher_date        date,
  content             jsonb,
  content_sha256      bytea,
  provider_refund_id  text,
  processed_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  version             int NOT NULL DEFAULT 1,
  CONSTRAINT refunds_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT refunds_voucher_unique UNIQUE (tenant_id, financial_year, voucher_no),
  CONSTRAINT refunds_voucher_text_unique UNIQUE (tenant_id, voucher_number),
  CONSTRAINT refunds_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT refunds_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT refunds_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES fin.student_fee_accounts (tenant_id, id),
  CONSTRAINT refunds_requested_by_fk FOREIGN KEY (tenant_id, requested_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT refunds_decided_by_fk FOREIGN KEY (tenant_id, decided_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT refunds_amount_positive CHECK (amount > 0),
  CONSTRAINT refunds_mode_check
    CHECK (mode IN ('original_method','cash','cheque','bank_transfer')),
  CONSTRAINT refunds_mode_details_object CHECK (jsonb_typeof(mode_details) = 'object'),
  CONSTRAINT refunds_reason_length CHECK (char_length(reason) BETWEEN 10 AND 1000),
  CONSTRAINT refunds_status_check CHECK (status IN
    ('pending','approved','processing','processed','failed','rejected','withdrawn')),
  -- D17: the approver is never the requester.
  CONSTRAINT refunds_maker_checker CHECK (decided_by IS NULL OR decided_by <> requested_by),
  CONSTRAINT refunds_decision CHECK (
    (status IN ('pending','withdrawn')) = (decided_by IS NULL)
    AND (status <> 'rejected' OR decision_note IS NOT NULL)
    AND (decision_note IS NULL OR char_length(decision_note) BETWEEN 10 AND 1000)),
  CONSTRAINT refunds_voucher_shape CHECK (
    (status = 'processed') = (processed_at IS NOT NULL)
    AND (status = 'processed') = (voucher_no IS NOT NULL)
    AND (voucher_no IS NULL) = (voucher_number IS NULL)
    AND (voucher_no IS NULL) = (voucher_date IS NULL)
    AND (voucher_no IS NULL) = (financial_year IS NULL)
    AND (voucher_no IS NULL) = (content IS NULL)
    AND (content IS NULL) = (content_sha256 IS NULL)
    AND (voucher_no IS NULL OR voucher_no BETWEEN 1 AND 999999)
    AND (financial_year IS NULL OR financial_year ~ '^[0-9]{4}-[0-9]{2}$')
    AND (voucher_number IS NULL OR voucher_number ~ '^[A-Za-z0-9][A-Za-z0-9/_.-]{0,39}$')
    AND (content_sha256 IS NULL OR octet_length(content_sha256) = 32)),
  CONSTRAINT refunds_online_only CHECK (mode = 'original_method'
    OR (status NOT IN ('approved','processing','failed') AND provider_refund_id IS NULL)),
  CONSTRAINT refunds_version_positive CHECK (version >= 1)
);
CREATE INDEX refunds_receipt_idx ON fin.refunds (tenant_id, receipt_id);
CREATE INDEX refunds_pending_idx ON fin.refunds (tenant_id, requested_at) WHERE status = 'pending';
CREATE INDEX refunds_voucher_day_idx ON fin.refunds (tenant_id, voucher_date)
  WHERE voucher_date IS NOT NULL;
CREATE UNIQUE INDEX refunds_provider_unique ON fin.refunds (tenant_id, provider_refund_id)
  WHERE provider_refund_id IS NOT NULL;

-- A refund request never changes; its workflow moves forward only and a processed voucher is
-- frozen.
CREATE FUNCTION fin.tg_refunds_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  workflow text[] := ARRAY['status','decided_by','decided_at','decision_note','financial_year',
    'voucher_no','voucher_number','voucher_date','content','content_sha256',
    'provider_refund_id','processed_at','updated_at','version'];
BEGIN
  IF (to_jsonb(NEW) - workflow) IS DISTINCT FROM (to_jsonb(OLD) - workflow) THEN
    RAISE EXCEPTION 'a refund request never changes after it is made'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_requests_frozen';
  END IF;
  IF OLD.status IN ('processed','rejected','withdrawn')
     OR (OLD.status = 'pending' AND NEW.status NOT IN
         ('pending','approved','processed','rejected','withdrawn'))
     OR (OLD.status IN ('approved','processing','failed') AND NEW.status NOT IN
         ('approved','processing','processed','failed'))
     OR (OLD.decided_by IS NOT NULL AND (NEW.decided_by, NEW.decided_at)
         IS DISTINCT FROM (OLD.decided_by, OLD.decided_at)) THEN
    RAISE EXCEPTION 'this refund was already decided'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_requests_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER refunds_frozen BEFORE UPDATE ON fin.refunds
  FOR EACH ROW EXECUTE FUNCTION fin.tg_refunds_frozen();
CREATE TRIGGER refunds_set_updated_at BEFORE UPDATE ON fin.refunds
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
CREATE TRIGGER refunds_no_delete BEFORE DELETE ON fin.refunds
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- Ledger ---------------------------------------------------------------------------------------

CREATE TABLE fin.ledger_entries (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  account_id       uuid NOT NULL,
  entry_kind       text NOT NULL,
  amount           numeric(14,2) NOT NULL,
  instalment_no    int NOT NULL,
  head_id          uuid,
  entry_date       date NOT NULL,
  receipt_id       uuid,
  cancellation_id  uuid,
  concession_id    uuid,
  adjustment_id    uuid,
  refund_id        uuid,
  late_fee_key     text,
  posted_by        uuid,
  posted_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ledger_entries_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT ledger_entries_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT ledger_entries_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES fin.student_fee_accounts (tenant_id, id),
  CONSTRAINT ledger_entries_head_fk FOREIGN KEY (tenant_id, head_id)
    REFERENCES fin.fee_heads (tenant_id, id),
  CONSTRAINT ledger_entries_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT ledger_entries_cancellation_fk FOREIGN KEY (tenant_id, cancellation_id)
    REFERENCES fin.receipt_cancellations (tenant_id, id),
  CONSTRAINT ledger_entries_concession_fk FOREIGN KEY (tenant_id, concession_id)
    REFERENCES fin.concessions (tenant_id, id),
  CONSTRAINT ledger_entries_adjustment_fk FOREIGN KEY (tenant_id, adjustment_id)
    REFERENCES fin.adjustments (tenant_id, id),
  CONSTRAINT ledger_entries_refund_fk FOREIGN KEY (tenant_id, refund_id)
    REFERENCES fin.refunds (tenant_id, id),
  CONSTRAINT ledger_entries_posted_by_fk FOREIGN KEY (tenant_id, posted_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT ledger_entries_kind_check CHECK (entry_kind IN
    ('charge','concession','late_fee','adjustment','payment','payment_reversal','refund')),
  CONSTRAINT ledger_entries_instalment_range CHECK (instalment_no BETWEEN 1 AND 24),
  -- Charges and amounts owed are positive, credits negative (FR-FEE-006).
  CONSTRAINT ledger_entries_sign CHECK (CASE entry_kind
    WHEN 'concession' THEN amount < 0
    WHEN 'payment' THEN amount < 0
    WHEN 'adjustment' THEN amount <> 0
    ELSE amount > 0 END),
  -- Exactly the source of each kind.
  CONSTRAINT ledger_entries_source CHECK (CASE entry_kind
    WHEN 'charge' THEN head_id IS NOT NULL
      AND num_nonnulls(receipt_id, cancellation_id, concession_id, adjustment_id, refund_id,
                       late_fee_key) = 0
    WHEN 'late_fee' THEN head_id IS NULL AND late_fee_key IS NOT NULL
      AND num_nonnulls(receipt_id, cancellation_id, concession_id, adjustment_id, refund_id) = 0
    WHEN 'concession' THEN head_id IS NOT NULL AND concession_id IS NOT NULL
      AND num_nonnulls(receipt_id, cancellation_id, adjustment_id, refund_id, late_fee_key) = 0
    WHEN 'adjustment' THEN adjustment_id IS NOT NULL
      AND num_nonnulls(receipt_id, cancellation_id, concession_id, refund_id, late_fee_key) = 0
    WHEN 'payment' THEN receipt_id IS NOT NULL
      AND num_nonnulls(cancellation_id, concession_id, adjustment_id, refund_id, late_fee_key) = 0
    WHEN 'payment_reversal' THEN receipt_id IS NOT NULL AND cancellation_id IS NOT NULL
      AND num_nonnulls(concession_id, adjustment_id, refund_id, late_fee_key) = 0
    WHEN 'refund' THEN receipt_id IS NOT NULL AND refund_id IS NOT NULL
      AND num_nonnulls(cancellation_id, concession_id, adjustment_id, late_fee_key) = 0
    END),
  CONSTRAINT ledger_entries_late_fee_key_shape
    CHECK (late_fee_key IS NULL OR late_fee_key ~ '^[a-z0-9:_-]{1,100}$')
);
CREATE INDEX ledger_entries_account_idx ON fin.ledger_entries (tenant_id, account_id, entry_date);
CREATE INDEX ledger_entries_receipt_idx ON fin.ledger_entries (tenant_id, receipt_id)
  WHERE receipt_id IS NOT NULL;
-- FR-FEE-005: applying a plan twice charges nothing more.
CREATE UNIQUE INDEX ledger_entries_one_charge ON fin.ledger_entries
  (tenant_id, account_id, instalment_no, head_id) WHERE entry_kind = 'charge';
-- FR-FEE-009: each late fee is posted once.
CREATE UNIQUE INDEX ledger_entries_one_late_fee ON fin.ledger_entries
  (tenant_id, account_id, late_fee_key) WHERE late_fee_key IS NOT NULL;
CREATE TRIGGER ledger_entries_append_only BEFORE UPDATE OR DELETE ON fin.ledger_entries
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- FR-FEE-006/010: at commit, a receipt's allocations and its payment entries both sum to its
-- amount (the allocation rows and the ledger rows are written in the same transaction).
CREATE FUNCTION fin.tg_receipt_sums() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  rid uuid;
  tid uuid := NEW.tenant_id;
  receipt_amount numeric(14,2);
  allocated numeric(14,2);
  paid numeric(14,2);
BEGIN
  IF TG_TABLE_NAME = 'receipts' THEN
    rid := NEW.id;
  ELSE
    rid := NEW.receipt_id;
  END IF;
  IF rid IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT r.amount INTO receipt_amount FROM fin.receipts r WHERE r.tenant_id = tid AND r.id = rid;
  SELECT coalesce(sum(a.amount), 0) INTO allocated FROM fin.receipt_allocations a
   WHERE a.tenant_id = tid AND a.receipt_id = rid;
  SELECT coalesce(-sum(e.amount), 0) INTO paid FROM fin.ledger_entries e
   WHERE e.tenant_id = tid AND e.receipt_id = rid AND e.entry_kind = 'payment';
  IF receipt_amount IS NULL OR allocated <> receipt_amount OR paid <> receipt_amount THEN
    RAISE EXCEPTION 'receipt allocations and payments must add up to the receipt amount'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'receipts_allocations_sum';
  END IF;
  RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER receipts_allocations_sum AFTER INSERT ON fin.receipts
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fin.tg_receipt_sums();
CREATE CONSTRAINT TRIGGER receipt_allocations_sum AFTER INSERT ON fin.receipt_allocations
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fin.tg_receipt_sums();
CREATE CONSTRAINT TRIGGER ledger_entries_payment_sum AFTER INSERT ON fin.ledger_entries
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  WHEN (NEW.entry_kind = 'payment') EXECUTE FUNCTION fin.tg_receipt_sums();

-- Day close ------------------------------------------------------------------------------------

CREATE TABLE fin.day_closes (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  close_date      date NOT NULL,
  receipt_count   int NOT NULL,
  receipt_total   numeric(14,2) NOT NULL,
  totals          jsonb NOT NULL,
  closed_by       uuid NOT NULL,
  closed_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT day_closes_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT day_closes_one_per_day UNIQUE (tenant_id, close_date),
  CONSTRAINT day_closes_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT day_closes_closed_by_fk FOREIGN KEY (tenant_id, closed_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT day_closes_counts CHECK (receipt_count >= 0 AND receipt_total >= 0),
  CONSTRAINT day_closes_totals_object CHECK (jsonb_typeof(totals) = 'object')
);
CREATE TRIGGER day_closes_append_only BEFORE UPDATE OR DELETE ON fin.day_closes
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- Generated receipt PDFs are documents (FR-FEE-011).
ALTER TABLE kb.documents DROP CONSTRAINT documents_purpose_check;
ALTER TABLE kb.documents ADD CONSTRAINT documents_purpose_check CHECK (purpose IN
  ('evidence','register_scan','circular','policy','other','import_file','certificate',
   'fee_receipt'));
"""

# Default privileges gave sos_app full DML on every fin table. Narrow it (FR-FEE-006, SEC-002):
# append-only tables keep SELECT and INSERT; workflow tables add UPDATE on their workflow columns
# only; nothing gets DELETE or TRUNCATE (the offboarding purge deletes as sos_purger).
APPEND_ONLY = (
    "fin.ledger_entries",
    "fin.receipt_allocations",
    "fin.day_closes",
)
UPDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "fin.fee_settings": (
        "paper_size",
        "gst_note",
        "reminder_offsets",
        "min_reminder_amount",
        "updated_by",
        "updated_at",
        "version",
    ),
    "fin.fee_heads": (
        "name",
        "kind",
        "gst_exempt",
        "active",
        "sort_order",
        "updated_at",
        "version",
    ),
    "fin.fee_plans": (
        "name",
        "late_fee_rule",
        "status",
        "activated_by",
        "activated_at",
        "updated_at",
        "version",
    ),
    "fin.fee_plan_lines": ("annual_amount", "sort_order"),
    "fin.fee_plan_instalments": ("label", "due_on", "amounts"),
    "fin.student_fee_accounts": ("plan_id", "status", "updated_at", "version"),
    "fin.concessions": (
        "status",
        "decided_by",
        "decided_at",
        "decision_note",
        "total_amount",
        "updated_at",
        "version",
    ),
    "fin.adjustments": (
        "status",
        "decided_by",
        "decided_at",
        "decision_note",
        "updated_at",
        "version",
    ),
    "fin.receipt_counters": ("last_no", "updated_at"),
    "fin.receipts": ("status", "document_id", "pdf_status", "pdf_error", "updated_at", "version"),
    "fin.receipt_cancellations": (
        "status",
        "decided_by",
        "decided_at",
        "decision_note",
        "cancelled_on",
        "updated_at",
        "version",
    ),
    "fin.refunds": (
        "status",
        "decided_by",
        "decided_at",
        "decision_note",
        "financial_year",
        "voucher_no",
        "voucher_number",
        "voucher_date",
        "content",
        "content_sha256",
        "provider_refund_id",
        "processed_at",
        "updated_at",
        "version",
    ),
}
# Draft plans lose their lines and instalments when edited (the trigger allows it for drafts).
DELETABLE = ("fin.fee_plan_lines", "fin.fee_plan_instalments")

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "fee.collect",
        "description": (
            "Take fee payments at the counter, issue receipts and request receipt cancellations"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "fee.manage",
        "description": (
            "Set fee heads and plans, apply plans, request adjustments, change fee settings and "
            "close the day"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "fee.concession.request",
        "description": "Request a fee concession for a student",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "fee.concession.approve",
        "description": (
            "Approve or reject fee concessions and adjustments requested by someone else "
            "(recent MFA sign-in)"
        ),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
    {
        "key": "fee.receipt.cancel",
        "description": (
            "Approve or reject the cancellation of a receipt requested by someone else "
            "(recent MFA sign-in)"
        ),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
    {
        "key": "fee.refund.request",
        "description": "Request a refund against a receipt",
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "fee.refund.approve",
        "description": ("Approve or reject refunds requested by someone else (recent MFA sign-in)"),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
    {
        "key": "fee.report",
        "description": (
            "Download the day book and dues lists (bulk personal financial data; recent MFA "
            "sign-in)"
        ),
        "sensitivity": "sensitive",
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
        if table in DELETABLE:
            lines.append(f"GRANT DELETE ON {table} TO sos_app;")
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
    op.execute("ALTER TABLE kb.documents DROP CONSTRAINT documents_purpose_check")
    op.execute(
        "ALTER TABLE kb.documents ADD CONSTRAINT documents_purpose_check CHECK (purpose IN "
        f"{PURPOSES_BEFORE}) NOT VALID"
    )

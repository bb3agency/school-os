"""Billing catalogue: one-time fee, AI answer bundles, overage (FR-PLT-010..017, FR-PLT-020).

Owner-approved commercial model of 2026-10-01 (ADR-0037; docs/16 §5.6, §5.7, §10.2, §11).
Prices are ex-GST starting prices in INR; GST goes on the invoice as before.

Schema ``platform`` only (control plane, no RLS, no student data):

- ``platform.plans``: ``one_time_fee_inr`` (``numeric(14,2) NOT NULL DEFAULT 0``, the
  "Implementation and data verification" fee charged once, on the subscription's first invoice)
  and ``description`` (plain wording shown to operators). ``created_by`` becomes nullable: NULL
  means "seeded by a catalogue migration" (every row an operator creates still names them).
  The existing freeze trigger covers the new columns (it compares whole rows).
- ``platform.ai_bundles`` (new): versioned AI answer bundles (code, version, name, included
  answers per calendar month, monthly price, price per extra answer), ``published`` or
  ``retired``; prices never change (trigger), rows are never deleted.
- ``platform.subscriptions``: ``ai_bundle_id`` (FK) and ``ai_bundle_from`` (first day of the
  first calendar month whose answers count against the bundle), both set or both NULL.
- ``platform.invoice_lines``: kind ``one_time_fee``; ``usage_month`` (first day of the calendar
  month an ``usage_overage`` line bills, NULL on every other line).
- ``platform.usage_daily``: ``ai_answers`` (billable AI answers that IST day; counts only).

Data: the published Shared (₹4,999 a month, one-time ₹15,000) and Dedicated (₹9,900 a month,
one-time ₹49,000) plans, and the AI bundles Lite (300 answers, ₹699), Standard (1,000, ₹1,499)
and High (3,000, ₹3,499), each ₹1.50 per extra answer. A plan code that already exists gets
the next version. Pinned by ``tests/platform/test_catalogue.py``.

Grants: default privileges give ``sos_platform`` DML on ``platform.ai_bundles``; ``sos_app``,
``sos_definer`` and ``sos_readonly`` get nothing new.

Downgrade refuses while a subscription uses a seeded plan or any AI bundle, or while an
overage line records its month (the month is what stops a second charge). Otherwise it deletes
the seeded plans and bundles, drops the new columns and table (lossy for ``ai_answers``), and
restores ``plans.created_by NOT NULL`` and the old ``invoice_lines`` kind check as ``NOT VALID``
(one-time fee lines on issued invoices stay; new ones are refused).

Revision ID: 0041_billing_catalogue
Revises: 0040_contextual_retrieval
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_billing_catalogue"
down_revision = "0040_contextual_retrieval"
branch_labels = None
depends_on = None

# UUIDv7 inside SQL (same construction as 0003_core_schema and 0005_platform).
_UUID7 = """pg_catalog.encode(pg_catalog.set_bit(pg_catalog.set_bit(
            overlay(pg_catalog.uuid_send(pg_catalog.gen_random_uuid())
                    PLACING substring(pg_catalog.int8send(
                      (extract(epoch FROM pg_catalog.clock_timestamp()) * 1000)::bigint) FROM 3)
                    FROM 1 FOR 6),
            52, 1), 53, 1), 'hex')::uuid"""

OLD_KINDS = "'subscription','per_student','addon','usage_overage','discount','adjustment'"
NEW_KINDS = OLD_KINDS + ",'one_time_fee'"

DEDICATED_WORDING = (
    "A managed, isolated SchoolOS environment with your own domain, a dedicated database "
    "and a documented data export."
)
SHARED_WORDING = (
    "Your school runs as a separate, isolated school on the managed SchoolOS platform "
    "in AWS Mumbai."
)

# (code, name, tier, monthly price, one-time fee, description): the owner's catalogue.
PLANS = (
    ("shared", "Shared", "shared", "4999.00", "15000.00", SHARED_WORDING),
    ("dedicated", "Dedicated", "dedicated", "9900.00", "49000.00", DEDICATED_WORDING),
)
# (code, name, included answers per month, monthly price, price per extra answer)
BUNDLES = (
    ("ai-lite", "Lite", 300, "699.00", "1.50"),
    ("ai-standard", "Standard", 1000, "1499.00", "1.50"),
    ("ai-high", "High", 3000, "3499.00", "1.50"),
)

SCHEMA_SQL = r"""
ALTER TABLE platform.plans
  ADD COLUMN one_time_fee_inr numeric(14,2) NOT NULL DEFAULT 0
    CONSTRAINT plans_one_time_fee CHECK (one_time_fee_inr >= 0),
  ADD COLUMN description text
    CONSTRAINT plans_description CHECK (char_length(description) BETWEEN 1 AND 300),
  ALTER COLUMN created_by DROP NOT NULL;
COMMENT ON COLUMN platform.plans.created_by IS
  'Operator who created the plan; NULL = seeded by a catalogue migration (0041).';
COMMENT ON COLUMN platform.plans.one_time_fee_inr IS
  'Implementation and data verification fee (ex-GST), on the subscription''s first invoice.';

CREATE TABLE platform.ai_bundles (
  id                uuid PRIMARY KEY,
  code              text NOT NULL CHECK (code ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  version           int  NOT NULL CHECK (version >= 1),
  name              text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
  included_answers  int  NOT NULL CHECK (included_answers > 0),
  price_inr         numeric(14,2) NOT NULL CHECK (price_inr >= 0),
  overage_rate_inr  numeric(14,2) NOT NULL CHECK (overage_rate_inr > 0),
  status            text NOT NULL CHECK (status IN ('published','retired')),
  published_at      timestamptz NOT NULL DEFAULT now(),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (code, version)
);
COMMENT ON TABLE platform.ai_bundles IS
  'AI answer bundles: monthly add-on with an included answer quota and a price per extra '
  'answer (docs/16 5.6). Prices never change; a new price is a new version. No student data.';

-- Only published -> retired may change; rows are never deleted (FR-PLT-010, like plans).
CREATE FUNCTION platform.tg_ai_bundles_freeze() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'AI bundles cannot be deleted; retire them'
      USING ERRCODE = 'object_not_in_prerequisite_state', CONSTRAINT = 'ai_bundles_frozen';
  END IF;
  IF (pg_catalog.to_jsonb(NEW) - 'status' - 'updated_at')
       IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'status' - 'updated_at')
     OR NOT (NEW.status = OLD.status OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
    RAISE EXCEPTION 'AI bundles are immutable; create a new version'
      USING ERRCODE = 'object_not_in_prerequisite_state', CONSTRAINT = 'ai_bundles_frozen';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION platform.tg_ai_bundles_freeze() FROM PUBLIC;
CREATE TRIGGER ai_bundles_freeze BEFORE UPDATE OR DELETE ON platform.ai_bundles
  FOR EACH ROW EXECUTE FUNCTION platform.tg_ai_bundles_freeze();
CREATE TRIGGER ai_bundles_set_updated_at BEFORE UPDATE ON platform.ai_bundles
  FOR EACH ROW EXECUTE FUNCTION platform.tg_set_updated_at();

ALTER TABLE platform.subscriptions
  ADD COLUMN ai_bundle_id uuid REFERENCES platform.ai_bundles (id),
  ADD COLUMN ai_bundle_from date,
  ADD CONSTRAINT subscriptions_ai_bundle_from
    CHECK ((ai_bundle_id IS NULL) = (ai_bundle_from IS NULL)
           AND (ai_bundle_from IS NULL OR extract(day FROM ai_bundle_from) = 1));

ALTER TABLE platform.invoice_lines
  DROP CONSTRAINT invoice_lines_kind_check,
  ADD CONSTRAINT invoice_lines_kind_check CHECK (kind IN (__KINDS__)),
  ADD COLUMN usage_month date,
  ADD CONSTRAINT invoice_lines_usage_month
    CHECK (usage_month IS NULL
           OR (kind = 'usage_overage' AND extract(day FROM usage_month) = 1));
CREATE INDEX invoice_lines_usage_month ON platform.invoice_lines (invoice_id, usage_month)
  WHERE usage_month IS NOT NULL;

ALTER TABLE platform.usage_daily
  ADD COLUMN ai_answers int NOT NULL DEFAULT 0 CONSTRAINT usage_daily_ai_answers
    CHECK (ai_answers >= 0);
""".replace("__KINDS__", NEW_KINDS)


# Bound parameters for every value (the UUIDv7 expression is a constant, as in 0005_platform).
SEED_PLAN_SQL = """
INSERT INTO platform.plans (id, code, version, name, tier, billing_period, pricing_model,
  base_price_inr, gst_rate, sac_code, trial_days, limits, features, status, published_at,
  created_by, one_time_fee_inr, description)
SELECT __UUID7__, :code, COALESCE(max(p.version), 0) + 1, :name, :tier, 'monthly', 'flat',
  CAST(:price AS numeric), 18.00, '998314', 30, '{}'::jsonb, '{}'::jsonb, 'published', now(),
  NULL, CAST(:fee AS numeric), :description
FROM platform.plans AS p WHERE p.code = :code
""".replace("__UUID7__", _UUID7)

SEED_BUNDLE_SQL = """
INSERT INTO platform.ai_bundles (id, code, version, name, included_answers, price_inr,
  overage_rate_inr, status)
VALUES (__UUID7__, :code, 1, :name, :answers, CAST(:price AS numeric), CAST(:rate AS numeric),
  'published')
""".replace("__UUID7__", _UUID7)


def _seed() -> None:
    for code, name, tier, price, fee, desc in PLANS:
        op.execute(
            sa.text(SEED_PLAN_SQL).bindparams(
                code=code, name=name, tier=tier, price=price, fee=fee, description=desc
            )
        )
    for code, name, answers, price, rate in BUNDLES:
        op.execute(
            sa.text(SEED_BUNDLE_SQL).bindparams(
                code=code, name=name, answers=answers, price=price, rate=rate
            )
        )


DOWNGRADE_SQL = r"""
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM platform.subscriptions AS s
             JOIN platform.plans AS p ON p.id IN (s.plan_id, s.pending_plan_id)
             WHERE p.created_by IS NULL) THEN
    RAISE EXCEPTION 'irreversible: subscriptions use catalogue plans'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.subscriptions WHERE ai_bundle_id IS NOT NULL) THEN
    RAISE EXCEPTION 'irreversible: subscriptions have AI bundles'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.invoice_lines WHERE usage_month IS NOT NULL) THEN
    RAISE EXCEPTION 'irreversible: AI overage has been invoiced'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END
$do$;

ALTER TABLE platform.plans DISABLE TRIGGER plans_freeze;
DELETE FROM platform.plans WHERE created_by IS NULL;
ALTER TABLE platform.plans ENABLE TRIGGER plans_freeze;
ALTER TABLE platform.plans
  DROP COLUMN one_time_fee_inr,
  DROP COLUMN description,
  ALTER COLUMN created_by SET NOT NULL;
COMMENT ON COLUMN platform.plans.created_by IS NULL;

ALTER TABLE platform.usage_daily DROP COLUMN ai_answers;

DROP INDEX IF EXISTS platform.invoice_lines_usage_month;
ALTER TABLE platform.invoice_lines
  DROP CONSTRAINT invoice_lines_usage_month,
  DROP COLUMN usage_month,
  DROP CONSTRAINT invoice_lines_kind_check,
  ADD CONSTRAINT invoice_lines_kind_check CHECK (kind IN (__KINDS__)) NOT VALID;

ALTER TABLE platform.subscriptions
  DROP CONSTRAINT subscriptions_ai_bundle_from,
  DROP COLUMN ai_bundle_from,
  DROP COLUMN ai_bundle_id;

DROP TABLE platform.ai_bundles;
DROP FUNCTION platform.tg_ai_bundles_freeze();
""".replace("__KINDS__", OLD_KINDS)


def upgrade() -> None:
    op.execute(SCHEMA_SQL)
    _seed()


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)

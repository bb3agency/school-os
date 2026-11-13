"""Online payment links for SchoolOS's own invoices (FA-13; FR-PAY-017..030; ADR-0016).

Schema ``platform`` only (control plane, no RLS, no student data; written by ``sos_platform``
through default privileges, nothing for ``sos_app``, ``sos_definer`` or ``sos_readonly``).
SchoolOS is the merchant for its own invoices: the money goes to SchoolOS's Razorpay account,
never to a school's.

- ``platform.invoice_payment_links`` (new): one Razorpay payment link per issued invoice at a
  time (partial unique index on ``status = 'active'``). It carries the provider's link id and
  short URL, the amount asked (the balance due when the link was made), its expiry, who made it
  and, once settled, the payment it produced. ``needs_review`` marks a link that was paid but
  could not be applied (the invoice was voided, settled by hand or its balance shrank since);
  ``review_reason`` says why. An operator refunds such a payment in Razorpay's dashboard.
- ``platform.payments``: ``provider`` also allows ``razorpay`` and ``fake`` (local and CI only;
  the settings refuse the fake provider in staging and production), ``method`` also allows
  ``online``; ``recorded_by`` may be NULL only for a provider payment (the signed webhook
  records it, no operator); ``(provider, provider_payment_id)`` is unique, the idempotency key
  of the webhook and the status check.

No UPI Autopay or e-mandate (lead default D87, owner question of FA-13 open).

Expand-only (invariant 12). Downgrade is **irreversible** (refused) while any provider payment
exists (the 0005 checks cannot hold it); otherwise it drops the table and restores the 0005
checks and ``recorded_by NOT NULL``.

Revision ID: 0105_invoice_payment_links
Revises: 0104_board_candidate
Create Date: 2026-11-13
"""

from __future__ import annotations

from alembic import op

revision = "0105_invoice_payment_links"
down_revision = "0104_board_candidate"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE platform.payments
  DROP CONSTRAINT payments_provider_check,
  ADD CONSTRAINT payments_provider_check CHECK (provider IN ('manual','razorpay','fake')),
  DROP CONSTRAINT payments_method_check,
  ADD CONSTRAINT payments_method_check
    CHECK (method IN ('bank_transfer','upi','cheque','other','online')),
  ALTER COLUMN recorded_by DROP NOT NULL,
  ADD CONSTRAINT payments_recorded_by_operator
    CHECK (provider <> 'manual' OR recorded_by IS NOT NULL),
  ADD CONSTRAINT payments_provider_payment_id
    CHECK ((provider = 'manual') = (provider_payment_id IS NULL)
           AND (provider_payment_id IS NULL OR provider_payment_id ~ '^[A-Za-z0-9_]{1,64}$'));
CREATE UNIQUE INDEX payments_provider_payment_unique
  ON platform.payments (provider, provider_payment_id) WHERE provider_payment_id IS NOT NULL;
COMMENT ON COLUMN platform.payments.recorded_by IS
  'Operator who recorded a manual payment; NULL for a provider payment applied by the signed '
  'webhook or a status check (0106, FA-13).';

CREATE TABLE platform.invoice_payment_links (
  id                uuid PRIMARY KEY,
  invoice_id        uuid NOT NULL REFERENCES platform.invoices (id),
  tenant_id         uuid NOT NULL,
  provider          text NOT NULL CHECK (provider IN ('razorpay','fake')),
  provider_link_id  text NOT NULL CHECK (provider_link_id ~ '^[A-Za-z0-9_]{1,64}$'),
  short_url         text NOT NULL
                      CHECK (short_url ~ '^https://[^\s]+$' AND char_length(short_url) <= 300),
  amount_inr        numeric(14,2) NOT NULL CHECK (amount_inr > 0),
  status            text NOT NULL
                      CHECK (status IN ('active','paid','cancelled','expired','needs_review')),
  expire_by         timestamptz NOT NULL,
  created_by        uuid NOT NULL REFERENCES platform.operators (id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  payment_id        uuid REFERENCES platform.payments (id),
  provider_payment_id text CHECK (provider_payment_id ~ '^[A-Za-z0-9_]{1,64}$'),
  settled_at        timestamptz,
  review_reason     text CHECK (review_reason IN
                      ('invoice_not_open','amount_exceeds_balance','amount_mismatch')),
  cancelled_by      uuid REFERENCES platform.operators (id),
  cancelled_at      timestamptz,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1 CHECK (version >= 1),
  UNIQUE (provider, provider_link_id),
  CONSTRAINT invoice_payment_links_paid
    CHECK ((status = 'paid') = (payment_id IS NOT NULL)),
  CONSTRAINT invoice_payment_links_review
    CHECK ((status = 'needs_review') = (review_reason IS NOT NULL)),
  CONSTRAINT invoice_payment_links_settled
    CHECK ((status IN ('paid','needs_review'))
           = (settled_at IS NOT NULL AND provider_payment_id IS NOT NULL)),
  CONSTRAINT invoice_payment_links_cancelled
    CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL))
);
CREATE UNIQUE INDEX invoice_payment_links_one_active
  ON platform.invoice_payment_links (invoice_id) WHERE status = 'active';
CREATE INDEX invoice_payment_links_invoice
  ON platform.invoice_payment_links (invoice_id, created_at DESC);
CREATE INDEX invoice_payment_links_tenant
  ON platform.invoice_payment_links (tenant_id, invoice_id);
CREATE TRIGGER invoice_payment_links_set_updated_at BEFORE UPDATE
  ON platform.invoice_payment_links
  FOR EACH ROW EXECUTE FUNCTION platform.tg_set_updated_at();
REVOKE ALL ON platform.invoice_payment_links FROM PUBLIC;
COMMENT ON TABLE platform.invoice_payment_links IS
  'Razorpay payment links of SchoolOS invoices (SchoolOS is the merchant; FA-13). One active '
  'link per invoice. No student data, no payer details.';
"""

DOWNGRADE_GUARD = r"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM platform.payments WHERE provider <> 'manual') THEN
    RAISE EXCEPTION 'irreversible: online payments of SchoolOS invoices exist'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END
$$;
"""

DOWNGRADE_SQL = r"""
DROP TABLE platform.invoice_payment_links;
DROP INDEX platform.payments_provider_payment_unique;
ALTER TABLE platform.payments
  DROP CONSTRAINT payments_provider_payment_id,
  DROP CONSTRAINT payments_recorded_by_operator,
  ALTER COLUMN recorded_by SET NOT NULL,
  DROP CONSTRAINT payments_method_check,
  ADD CONSTRAINT payments_method_check
    CHECK (method IN ('bank_transfer','upi','cheque','other')),
  DROP CONSTRAINT payments_provider_check,
  ADD CONSTRAINT payments_provider_check CHECK (provider IN ('manual'));
COMMENT ON COLUMN platform.payments.recorded_by IS NULL;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARD)
    op.execute(DOWNGRADE_SQL)

"""R2-2D online payments: the school's Razorpay connection, payment intents (links), the webhook
inbox, provider payments and online reconciliation (ADR-0042 decisions 2, 3, 6-10; FR-PAY-001..016;
docs/05 §15.2).

Every table lives in the tenant schema ``fin`` and follows the tenant-table rules: ``tenant_id``
first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``, composite FKs,
the restrictive ``offboarding_purge`` policy with ``SELECT, DELETE`` for ``sos_purger``
(ADR-0029), no ``definer_access``, nothing for ``sos_platform``. Money is ``numeric(14,2)`` INR;
paise exist only inside ``app/fees/providers/razorpay`` (FR-FEE-028).

- ``fin.payment_connections``: the school's own Razorpay account (the school is the merchant,
  ADR-0042 decision 2). ``mode`` ``oauth`` (Technology Partner, decision 6) or ``manual_keys``
  (pilots, D13). Secrets (key secret, OAuth tokens, webhook secret and, for 7 days after a
  rotation, the previous one; the webhook route value) are wrapped by the school's key wrapper
  bound to the row (``core.crypto.wrap_bound``, ``resource: payment_connection/<id>``, SEC-035)
  and erased on disconnect (``CHECK``). One connection that is not disconnected per school.
- ``fin.payment_intents``: one payment link for an account's chosen instalments (FR-PAY-005);
  amount and instalments never change (trigger), statuses move ``created`` -> ``paid`` /
  ``expired`` / ``cancelled`` / ``failed`` and an expired, cancelled or failed intent may still
  become ``paid`` (the provider is the truth after a re-fetch); ``paid`` is final.
  ``fin.receipts.payment_intent_id`` gains its composite FK here (``NOT VALID``: receipts kept
  through a downgrade may name removed intents) and one issued receipt per intent.
- ``fin.provider_events``: the webhook inbox (FR-PAY-007), unique ``(tenant_id, provider,
  event_id)`` (Razorpay's ``x-razorpay-event-id``), minimised payload encrypted with the school's
  DEK (AAD ``fin.provider_events|payload_ciphertext|<id>``); purged 90 days after processing.
- ``fin.provider_payments``: what a re-fetch from the provider said about a payment (amount,
  method, status, fee, tax, settlement). No payer identifier (contact, email, VPA, card) is kept.
- ``fin.reconciliation_runs`` / ``fin.reconciliation_items``: the daily online reconciliation and
  every exception found by it or by webhook processing (FR-PAY-009, FR-PAY-010); an item is
  explained by a person with a note (audited) or resolved by a later receipt, never deleted.

Also the permission ``payments.configure`` (critical, step-up; owner). Role grants of existing
schools come from ``python -m app.identity.sync_system_roles`` (ADR-0022).

Expand-only (invariant 12). Downgrade drops the FK on ``fin.receipts.payment_intent_id`` and every
table of this revision (lossy: connections, intents, the inbox and reconciliation items are
removed; receipts and ledger entries stay) and deletes the permission key unless a role still
holds it.

Revision ID: 0059_payments_online
Revises: 0058_fees_core
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_payments_online"
down_revision = "0058_fees_core"
branch_labels = None
depends_on = None

CODE = r"'^[a-z][a-z0-9_]{0,63}$'"

# Children before parents (downgrade drops in this order; the fees purge deletes the same order).
TENANT_TABLES = (
    "fin.reconciliation_items",
    "fin.reconciliation_runs",
    "fin.provider_payments",
    "fin.provider_events",
    "fin.payment_intents",
    "fin.payment_connections",
)

FUNCTIONS = (
    "fin.tg_payment_intents_guard()",
    "fin.tg_reconciliation_items_guard()",
)

UPGRADE_SQL = (
    r"""
-- Connections ----------------------------------------------------------------------------------

CREATE TABLE fin.payment_connections (
  id                                  uuid PRIMARY KEY,
  tenant_id                           uuid NOT NULL,
  provider                            text NOT NULL DEFAULT 'razorpay',
  mode                                text NOT NULL,
  environment                         text NOT NULL,
  status                              text NOT NULL,
  provider_account_id                 text,
  key_id                              text,
  key_secret_ciphertext               bytea,
  access_token_ciphertext             bytea,
  refresh_token_ciphertext            bytea,
  access_expires_at                   timestamptz,
  refresh_expires_at                  timestamptz,
  webhook_id                          text,
  webhook_secret_ciphertext           bytea,
  previous_webhook_secret_ciphertext  bytea,
  previous_until                      timestamptz,
  route_ciphertext                    bytea,
  route_handle_id                     uuid,
  connected_by                        uuid,
  connected_at                        timestamptz NOT NULL DEFAULT now(),
  secret_rotated_at                   timestamptz,
  last_checked_at                     timestamptz,
  last_error_code                     text,
  revoked_at                          timestamptz,
  disconnected_by                     uuid,
  disconnected_at                     timestamptz,
  created_at                          timestamptz NOT NULL DEFAULT now(),
  updated_at                          timestamptz NOT NULL DEFAULT now(),
  version                             int NOT NULL DEFAULT 1,
  CONSTRAINT payment_connections_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT payment_connections_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT payment_connections_route_handle_fk FOREIGN KEY (tenant_id, route_handle_id)
    REFERENCES core.public_handles (tenant_id, id),
  CONSTRAINT payment_connections_connected_by_fk FOREIGN KEY (tenant_id, connected_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT payment_connections_disconnected_by_fk FOREIGN KEY (tenant_id, disconnected_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT payment_connections_provider_check CHECK (provider IN ('razorpay')),
  CONSTRAINT payment_connections_mode_check CHECK (mode IN ('oauth','manual_keys')),
  CONSTRAINT payment_connections_environment_check CHECK (environment IN ('test','live')),
  CONSTRAINT payment_connections_status_check
    CHECK (status IN ('active','revoked','disconnected')),
  CONSTRAINT payment_connections_account_shape
    CHECK (provider_account_id IS NULL OR provider_account_id ~ '^acc_[A-Za-z0-9]{1,30}$'),
  CONSTRAINT payment_connections_key_id_shape
    CHECK (key_id IS NULL OR key_id ~ '^rzp_(test|live)_[A-Za-z0-9]{6,30}$'),
  CONSTRAINT payment_connections_webhook_id_shape
    CHECK (webhook_id IS NULL OR webhook_id ~ '^[A-Za-z0-9_]{1,40}$'),
  CONSTRAINT payment_connections_error_code_format
    CHECK (last_error_code IS NULL OR last_error_code ~ {CODE}),
  -- The previous webhook secret is kept for a while after a rotation (FR-PAY-014).
  CONSTRAINT payment_connections_previous_secret
    CHECK ((previous_webhook_secret_ciphertext IS NULL) = (previous_until IS NULL)),
  CONSTRAINT payment_connections_disconnected
    CHECK ((status = 'disconnected') = (disconnected_at IS NOT NULL)),
  CONSTRAINT payment_connections_revoked CHECK (status <> 'revoked' OR revoked_at IS NOT NULL),
  -- A disconnected connection keeps no secret; a live one has what its mode needs (SEC-035).
  CONSTRAINT payment_connections_secrets CHECK (CASE
    WHEN status = 'disconnected' THEN key_secret_ciphertext IS NULL
      AND access_token_ciphertext IS NULL AND refresh_token_ciphertext IS NULL
      AND webhook_secret_ciphertext IS NULL AND previous_webhook_secret_ciphertext IS NULL
      AND route_ciphertext IS NULL
    WHEN mode = 'manual_keys' THEN key_id IS NOT NULL AND key_secret_ciphertext IS NOT NULL
      AND access_token_ciphertext IS NULL AND refresh_token_ciphertext IS NULL
      AND webhook_secret_ciphertext IS NOT NULL AND route_ciphertext IS NOT NULL
      AND route_handle_id IS NOT NULL
    WHEN status = 'active' THEN key_id IS NULL AND key_secret_ciphertext IS NULL
      AND access_token_ciphertext IS NOT NULL AND refresh_token_ciphertext IS NOT NULL
      AND access_expires_at IS NOT NULL AND refresh_expires_at IS NOT NULL
      AND provider_account_id IS NOT NULL AND webhook_secret_ciphertext IS NOT NULL
      AND route_ciphertext IS NOT NULL AND route_handle_id IS NOT NULL
    ELSE key_id IS NULL AND key_secret_ciphertext IS NULL END),
  CONSTRAINT payment_connections_version_positive CHECK (version >= 1)
);
-- One connection that is not disconnected per school and provider (docs/05 §15.2).
CREATE UNIQUE INDEX payment_connections_one_live ON fin.payment_connections (tenant_id, provider)
  WHERE status <> 'disconnected';
CREATE TRIGGER payment_connections_set_updated_at BEFORE UPDATE ON fin.payment_connections
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- Payment intents ------------------------------------------------------------------------------

CREATE TABLE fin.payment_intents (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  account_id           uuid NOT NULL,
  connection_id        uuid NOT NULL,
  intent_code          text NOT NULL,
  amount               numeric(14,2) NOT NULL,
  instalments          int[] NOT NULL,
  status               text NOT NULL DEFAULT 'created',
  provider_link_id     text NOT NULL,
  short_url            text NOT NULL,
  expires_at           timestamptz NOT NULL,
  created_via          text NOT NULL,
  created_by           uuid,
  public_handle_id     uuid,
  provider_payment_id  text,
  paid_at              timestamptz,
  cancelled_by         uuid,
  cancelled_at         timestamptz,
  closed_at            timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1,
  CONSTRAINT payment_intents_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT payment_intents_link_unique UNIQUE (tenant_id, provider_link_id),
  CONSTRAINT payment_intents_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT payment_intents_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES fin.student_fee_accounts (tenant_id, id),
  CONSTRAINT payment_intents_connection_fk FOREIGN KEY (tenant_id, connection_id)
    REFERENCES fin.payment_connections (tenant_id, id),
  CONSTRAINT payment_intents_created_by_fk FOREIGN KEY (tenant_id, created_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT payment_intents_cancelled_by_fk FOREIGN KEY (tenant_id, cancelled_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT payment_intents_public_handle_fk FOREIGN KEY (tenant_id, public_handle_id)
    REFERENCES core.public_handles (tenant_id, id),
  CONSTRAINT payment_intents_code_shape CHECK (intent_code ~ '^[A-Z0-9]{8}$'),
  CONSTRAINT payment_intents_amount_range CHECK (amount >= 1 AND amount <= 10000000),
  CONSTRAINT payment_intents_instalments_shape CHECK (cardinality(instalments) BETWEEN 1 AND 24
    AND 1 <= ALL (instalments) AND 24 >= ALL (instalments)),
  CONSTRAINT payment_intents_status_check
    CHECK (status IN ('created','paid','expired','cancelled','failed')),
  CONSTRAINT payment_intents_link_shape CHECK (provider_link_id ~ '^[A-Za-z0-9_]{1,40}$'),
  CONSTRAINT payment_intents_short_url_shape
    CHECK (short_url ~ '^https://[A-Za-z0-9.-]+(:[0-9]+)?/[!-~]{0,180}$'),
  CONSTRAINT payment_intents_created_via_check
    CHECK (created_via IN ('staff','parent_page','reminder')),
  CONSTRAINT payment_intents_staff_creator CHECK (created_via <> 'staff' OR created_by IS NOT NULL),
  CONSTRAINT payment_intents_payment_shape
    CHECK (provider_payment_id IS NULL OR provider_payment_id ~ '^[A-Za-z0-9_]{1,40}$'),
  CONSTRAINT payment_intents_paid CHECK ((status = 'paid') = (paid_at IS NOT NULL)
    AND (status = 'paid') = (provider_payment_id IS NOT NULL)),
  CONSTRAINT payment_intents_cancelled
    CHECK ((cancelled_at IS NULL) OR status IN ('cancelled','paid')),
  CONSTRAINT payment_intents_closed CHECK ((status = 'created') = (closed_at IS NULL)),
  CONSTRAINT payment_intents_expiry_after_creation CHECK (expires_at > created_at),
  CONSTRAINT payment_intents_version_positive CHECK (version >= 1)
);
CREATE INDEX payment_intents_account_idx ON fin.payment_intents
  (tenant_id, account_id, created_at DESC);
CREATE INDEX payment_intents_open_idx ON fin.payment_intents (tenant_id, expires_at)
  WHERE status = 'created';
CREATE UNIQUE INDEX payment_intents_payment_unique ON fin.payment_intents
  (tenant_id, provider_payment_id) WHERE provider_payment_id IS NOT NULL;
CREATE TRIGGER payment_intents_set_updated_at BEFORE UPDATE ON fin.payment_intents
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- What the parent is asked to pay never changes; statuses move forward only, and the provider's
-- word after a re-fetch may still turn an expired, cancelled or failed intent into a paid one.
CREATE FUNCTION fin.tg_payment_intents_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  workflow text[] := ARRAY['status','provider_payment_id','paid_at','cancelled_by',
    'cancelled_at','closed_at','updated_at','version'];
BEGIN
  IF (to_jsonb(NEW) - workflow) IS DISTINCT FROM (to_jsonb(OLD) - workflow) THEN
    RAISE EXCEPTION 'a payment link never changes what it asks for'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'payment_intents_frozen';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       OLD.status = 'created'
       OR (OLD.status IN ('expired','cancelled','failed') AND NEW.status = 'paid')) THEN
    RAISE EXCEPTION 'payment link status % cannot become %', OLD.status, NEW.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'payment_intents_frozen';
  END IF;
  IF OLD.status = 'paid' AND (NEW.provider_payment_id, NEW.paid_at)
     IS DISTINCT FROM (OLD.provider_payment_id, OLD.paid_at) THEN
    RAISE EXCEPTION 'a paid link stays paid'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'payment_intents_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER payment_intents_guard BEFORE UPDATE ON fin.payment_intents
  FOR EACH ROW EXECUTE FUNCTION fin.tg_payment_intents_guard();
CREATE TRIGGER payment_intents_no_delete BEFORE DELETE ON fin.payment_intents
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();

-- Receipts name the intent they were issued for (no FK until now, docs/05 §15.1 as built).
-- NOT VALID: new and changed rows are checked; receipts kept through a downgrade of this
-- revision may still name intents that the downgrade removed (receipts are never edited).
ALTER TABLE fin.receipts ADD CONSTRAINT receipts_payment_intent_fk
  FOREIGN KEY (tenant_id, payment_intent_id) REFERENCES fin.payment_intents (tenant_id, id)
  NOT VALID;
CREATE UNIQUE INDEX receipts_payment_intent_unique ON fin.receipts (tenant_id, payment_intent_id)
  WHERE payment_intent_id IS NOT NULL AND status = 'issued';

-- Webhook inbox --------------------------------------------------------------------------------

CREATE TABLE fin.provider_events (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  provider            text NOT NULL DEFAULT 'razorpay',
  connection_id       uuid NOT NULL,
  event_id            text NOT NULL,
  event_type          text NOT NULL,
  event_at            timestamptz,
  received_at         timestamptz NOT NULL DEFAULT now(),
  payload_ciphertext  bytea NOT NULL,
  status              text NOT NULL DEFAULT 'received',
  outcome             text,
  attempts            int NOT NULL DEFAULT 0,
  next_attempt_at     timestamptz NOT NULL DEFAULT now(),
  error_code          text,
  processed_at        timestamptz,
  CONSTRAINT provider_events_tenant_id_id_key UNIQUE (tenant_id, id),
  -- FR-PAY-007: one row per provider event id (replays and retries are no-ops).
  CONSTRAINT provider_events_event_unique UNIQUE (tenant_id, provider, event_id),
  CONSTRAINT provider_events_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT provider_events_connection_fk FOREIGN KEY (tenant_id, connection_id)
    REFERENCES fin.payment_connections (tenant_id, id),
  CONSTRAINT provider_events_provider_check CHECK (provider IN ('razorpay')),
  CONSTRAINT provider_events_event_id_shape CHECK (event_id ~ '^[A-Za-z0-9_:.-]{1,100}$'),
  CONSTRAINT provider_events_type_shape CHECK (event_type ~ '^[a-z][a-z0-9_.]{0,59}$'),
  CONSTRAINT provider_events_status_check
    CHECK (status IN ('received','processed','ignored','failed')),
  CONSTRAINT provider_events_outcome_format CHECK (outcome IS NULL OR outcome ~ {CODE}),
  CONSTRAINT provider_events_error_code_format CHECK (error_code IS NULL OR error_code ~ {CODE}),
  CONSTRAINT provider_events_attempts_range CHECK (attempts BETWEEN 0 AND 100),
  CONSTRAINT provider_events_processed CHECK ((status = 'received') = (processed_at IS NULL))
);
CREATE INDEX provider_events_pending_idx ON fin.provider_events
  (tenant_id, next_attempt_at, received_at) WHERE status = 'received';
CREATE INDEX provider_events_processed_idx ON fin.provider_events (tenant_id, processed_at)
  WHERE processed_at IS NOT NULL;

-- Provider payments ----------------------------------------------------------------------------

CREATE TABLE fin.provider_payments (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  provider             text NOT NULL DEFAULT 'razorpay',
  connection_id        uuid NOT NULL,
  provider_payment_id  text NOT NULL,
  provider_order_id    text,
  intent_id            uuid,
  amount               numeric(14,2) NOT NULL,
  currency             text NOT NULL,
  method               text NOT NULL,
  status               text NOT NULL,
  amount_refunded      numeric(14,2) NOT NULL DEFAULT 0,
  provider_fee         numeric(14,2),
  provider_tax         numeric(14,2),
  paid_at              timestamptz,
  settlement_id        text,
  settled_at           timestamptz,
  receipt_id           uuid,
  first_seen_at        timestamptz NOT NULL DEFAULT now(),
  last_fetched_at      timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT provider_payments_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT provider_payments_payment_unique UNIQUE (tenant_id, provider, provider_payment_id),
  CONSTRAINT provider_payments_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT provider_payments_connection_fk FOREIGN KEY (tenant_id, connection_id)
    REFERENCES fin.payment_connections (tenant_id, id),
  CONSTRAINT provider_payments_intent_fk FOREIGN KEY (tenant_id, intent_id)
    REFERENCES fin.payment_intents (tenant_id, id),
  CONSTRAINT provider_payments_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT provider_payments_provider_check CHECK (provider IN ('razorpay')),
  CONSTRAINT provider_payments_payment_shape
    CHECK (provider_payment_id ~ '^[A-Za-z0-9_]{1,40}$'),
  CONSTRAINT provider_payments_order_shape
    CHECK (provider_order_id IS NULL OR provider_order_id ~ '^[A-Za-z0-9_]{1,40}$'),
  CONSTRAINT provider_payments_currency_shape CHECK (currency ~ '^[A-Z]{3}$'),
  CONSTRAINT provider_payments_method_check
    CHECK (method IN ('upi','card','netbanking','wallet','emi','other')),
  CONSTRAINT provider_payments_status_check
    CHECK (status IN ('created','authorized','captured','refunded','failed')),
  CONSTRAINT provider_payments_amounts CHECK (amount >= 0 AND amount_refunded >= 0
    AND (provider_fee IS NULL OR provider_fee >= 0) AND (provider_tax IS NULL OR provider_tax >= 0)),
  CONSTRAINT provider_payments_settlement_shape
    CHECK (settlement_id IS NULL OR settlement_id ~ '^[A-Za-z0-9_]{1,40}$')
);
CREATE INDEX provider_payments_paid_idx ON fin.provider_payments (tenant_id, paid_at);
CREATE INDEX provider_payments_receipt_idx ON fin.provider_payments (tenant_id, receipt_id)
  WHERE receipt_id IS NOT NULL;
CREATE TRIGGER provider_payments_set_updated_at BEFORE UPDATE ON fin.provider_payments
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- Reconciliation -------------------------------------------------------------------------------

CREATE TABLE fin.reconciliation_runs (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  provider           text NOT NULL DEFAULT 'razorpay',
  run_date           date NOT NULL,
  status             text NOT NULL DEFAULT 'running',
  payments_seen      int NOT NULL DEFAULT 0,
  exceptions_opened  int NOT NULL DEFAULT 0,
  error_code         text,
  started_at         timestamptz NOT NULL DEFAULT now(),
  finished_at        timestamptz,
  CONSTRAINT reconciliation_runs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT reconciliation_runs_one_per_day UNIQUE (tenant_id, provider, run_date),
  CONSTRAINT reconciliation_runs_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT reconciliation_runs_provider_check CHECK (provider IN ('razorpay')),
  CONSTRAINT reconciliation_runs_status_check CHECK (status IN ('running','done','failed')),
  CONSTRAINT reconciliation_runs_counts CHECK (payments_seen >= 0 AND exceptions_opened >= 0),
  CONSTRAINT reconciliation_runs_error_code_format
    CHECK (error_code IS NULL OR error_code ~ {CODE}),
  CONSTRAINT reconciliation_runs_finished CHECK ((status = 'running') = (finished_at IS NULL))
);

CREATE TABLE fin.reconciliation_items (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  run_id               uuid,
  kind                 text NOT NULL,
  reason_code          text NOT NULL,
  dedupe_key           text NOT NULL,
  provider_payment_id  text,
  provider_refund_id   text,
  intent_id            uuid,
  receipt_id           uuid,
  refund_id            uuid,
  amount               numeric(14,2),
  expected_amount      numeric(14,2),
  status               text NOT NULL DEFAULT 'open',
  note                 text,
  explained_by         uuid,
  explained_at         timestamptz,
  resolved_at          timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1,
  CONSTRAINT reconciliation_items_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT reconciliation_items_dedupe UNIQUE (tenant_id, dedupe_key),
  CONSTRAINT reconciliation_items_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT reconciliation_items_run_fk FOREIGN KEY (tenant_id, run_id)
    REFERENCES fin.reconciliation_runs (tenant_id, id),
  CONSTRAINT reconciliation_items_intent_fk FOREIGN KEY (tenant_id, intent_id)
    REFERENCES fin.payment_intents (tenant_id, id),
  CONSTRAINT reconciliation_items_receipt_fk FOREIGN KEY (tenant_id, receipt_id)
    REFERENCES fin.receipts (tenant_id, id),
  CONSTRAINT reconciliation_items_refund_fk FOREIGN KEY (tenant_id, refund_id)
    REFERENCES fin.refunds (tenant_id, id),
  CONSTRAINT reconciliation_items_explained_by_fk FOREIGN KEY (tenant_id, explained_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT reconciliation_items_kind_check CHECK (kind IN ('paid_not_receipted',
    'receipted_not_captured','amount_mismatch','refunded','refund_failed','settlement_missing',
    'unknown_intent')),
  CONSTRAINT reconciliation_items_reason_format CHECK (reason_code ~ {CODE}),
  CONSTRAINT reconciliation_items_dedupe_shape CHECK (dedupe_key ~ '^[A-Za-z0-9_:.-]{1,120}$'),
  CONSTRAINT reconciliation_items_payment_shape
    CHECK (provider_payment_id IS NULL OR provider_payment_id ~ '^[A-Za-z0-9_]{1,40}$'),
  CONSTRAINT reconciliation_items_refund_shape
    CHECK (provider_refund_id IS NULL OR provider_refund_id ~ '^[A-Za-z0-9_]{1,40}$'),
  CONSTRAINT reconciliation_items_status_check
    CHECK (status IN ('open','explained','resolved')),
  CONSTRAINT reconciliation_items_note_length
    CHECK (note IS NULL OR char_length(note) BETWEEN 10 AND 500),
  CONSTRAINT reconciliation_items_explained CHECK ((status = 'explained') =
    (explained_by IS NOT NULL AND explained_at IS NOT NULL AND note IS NOT NULL)),
  CONSTRAINT reconciliation_items_resolved CHECK ((status = 'resolved') = (resolved_at IS NOT NULL)),
  CONSTRAINT reconciliation_items_version_positive CHECK (version >= 1)
);
CREATE INDEX reconciliation_items_open_idx ON fin.reconciliation_items (tenant_id, created_at)
  WHERE status = 'open';
CREATE INDEX reconciliation_items_list_idx ON fin.reconciliation_items
  (tenant_id, created_at DESC, id DESC);
CREATE TRIGGER reconciliation_items_set_updated_at BEFORE UPDATE ON fin.reconciliation_items
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- An item is never deleted and what was found never changes; it is explained or resolved once.
CREATE FUNCTION fin.tg_reconciliation_items_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  workflow text[] := ARRAY['status','note','explained_by','explained_at','resolved_at',
    'updated_at','version'];
BEGIN
  IF (to_jsonb(NEW) - workflow) IS DISTINCT FROM (to_jsonb(OLD) - workflow)
     OR (OLD.status <> 'open' AND NEW IS DISTINCT FROM OLD) THEN
    RAISE EXCEPTION 'a reconciliation item is explained or resolved once and never edited'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'reconciliation_items_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER reconciliation_items_guard BEFORE UPDATE ON fin.reconciliation_items
  FOR EACH ROW EXECUTE FUNCTION fin.tg_reconciliation_items_guard();
CREATE TRIGGER reconciliation_items_no_delete BEFORE DELETE ON fin.reconciliation_items
  FOR EACH ROW EXECUTE FUNCTION fin.tg_append_only();
"""
).replace("{CODE}", CODE)

# Default privileges gave sos_app full DML on every fin table. Narrow it (SEC-002): column UPDATE
# on workflow columns only; DELETE only where a retention job deletes (the inbox); the offboarding
# purge deletes as sos_purger.
UPDATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "fin.payment_connections": (
        "status",
        "provider_account_id",
        "access_token_ciphertext",
        "refresh_token_ciphertext",
        "access_expires_at",
        "refresh_expires_at",
        "key_secret_ciphertext",
        "webhook_id",
        "webhook_secret_ciphertext",
        "previous_webhook_secret_ciphertext",
        "previous_until",
        "route_ciphertext",
        "secret_rotated_at",
        "last_checked_at",
        "last_error_code",
        "revoked_at",
        "disconnected_by",
        "disconnected_at",
        "updated_at",
        "version",
    ),
    "fin.payment_intents": (
        "status",
        "provider_payment_id",
        "paid_at",
        "cancelled_by",
        "cancelled_at",
        "closed_at",
        "updated_at",
        "version",
    ),
    "fin.provider_events": (
        "status",
        "outcome",
        "attempts",
        "next_attempt_at",
        "error_code",
        "processed_at",
        "payload_ciphertext",
    ),
    "fin.provider_payments": (
        "provider_order_id",
        "intent_id",
        "amount",
        "currency",
        "method",
        "status",
        "amount_refunded",
        "provider_fee",
        "provider_tax",
        "paid_at",
        "settlement_id",
        "settled_at",
        "receipt_id",
        "last_fetched_at",
        "updated_at",
    ),
    "fin.reconciliation_runs": (
        "status",
        "payments_seen",
        "exceptions_opened",
        "error_code",
        "finished_at",
    ),
    "fin.reconciliation_items": (
        "status",
        "note",
        "explained_by",
        "explained_at",
        "resolved_at",
        "updated_at",
        "version",
    ),
}
DELETABLE = ("fin.provider_events",)
# Secrets and raw provider payloads never reach the read-only role (docs/05 §15).
NO_READONLY = ("fin.payment_connections", "fin.provider_events")

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "payments.configure",
        "description": (
            "Connect, test, rotate the webhook secret of, or disconnect the school's own Razorpay "
            "account (recent MFA sign-in)"
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
        lines.append(
            f"REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON {table} FROM sos_app;"
        )
        columns = UPDATE_COLUMNS.get(table)
        if columns:
            lines.append(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO sos_app;")
        if table in DELETABLE:
            lines.append(f"GRANT DELETE ON {table} TO sos_app;")
        lines.append(f"REVOKE ALL ON {table} FROM sos_readonly;")
        if table not in NO_READONLY:
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
    op.execute("DROP INDEX IF EXISTS fin.receipts_payment_intent_unique")
    op.execute("ALTER TABLE fin.receipts DROP CONSTRAINT IF EXISTS receipts_payment_intent_fk")
    for table in TENANT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")

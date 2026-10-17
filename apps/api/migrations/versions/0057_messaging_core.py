"""R2-1C WhatsApp messaging core (ADR-0043, ADR-0044; FR-MSG-001..010, FR-MSG-016, FR-MSG-018..024;
docs/05 §15.3).

Tenant tables in schema ``msg`` (created by ``infra/db/bootstrap.sql``, owner decision D14):
``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with ``tenant_isolation``,
composite FKs, the restrictive ``offboarding_purge`` policy and ``SELECT, DELETE`` for
``sos_purger`` (ADR-0029). The app role's default table-wide grants are narrowed per table:

- ``msg.accounts``: the school's WhatsApp Business account and number. One row that is not
  ``disconnected`` per school (partial unique index). The business token and the registration
  PIN are wrapped with the school's key wrapper bound to the row (``core.crypto.wrap_bound``,
  ``resource: "messaging_account/<id>"``) and erased on disconnect (CHECK). ``waba_handle_id``
  names the ``meta_waba`` public handle that routes webhooks (ADR-0047). The app may update the
  status, sync and token columns only; ``sos_readonly`` gets nothing (secrets).
- ``msg.templates``: one row per catalogue template, version and language created in the
  school's WABA (``sos_<key>_v<version>_<lang>``), with Meta's status and category and the
  ``paused_by_sos`` flag (FR-MSG-006, FR-MSG-007).
- ``msg.settings``: one row per school: switches per purpose (all off by default; absence alerts
  off, owner decision D25), absence-alert time (11:00), quiet hours (21:00-07:00), daily cap
  (NULL = Meta's limit minus 10%), the office phone for automatic replies.
- ``msg.optins``: opt-in and opt-out decisions, **append-only** (SELECT and INSERT for the app;
  ``seq`` per guardian and category, ``UNIQUE`` so a concurrent write fails instead of forking
  the history; only the phone blind index is refreshed when the school's key rotates). CHECKs
  per method: ``signed_form`` needs an evidence document, ``in_person`` and
  ``staff_recorded_stop`` name the recorder, ``whatsapp_join`` / ``whatsapp_stop`` have none;
  ``opted_in`` rows keep the guardian's phone blind index and key version at the time (a new
  number needs a new opt-in, docs/05 §15.3). Deleted with the guardian (``ON DELETE CASCADE``;
  the audit event keeps the fact).
- ``msg.join_codes``: printed slip codes as SHA-256 only, single use (``used_at``), 30 days.
- ``msg.messages``: one row per message to a guardian: purpose, template, language, the phone's
  blind index and last four digits, encrypted variables, provider message id, status. A trigger
  keeps statuses monotonic (``queued`` -> ``sent`` -> ``delivered`` -> ``read``; ``failed`` only
  from ``queued``/``sent``; ``skipped`` only from ``queued``; final states stay final;
  FR-MSG-016). Idempotency ``UNIQUE (tenant_id, purpose, source_id, guardian_id, dedupe_key)``.
  No message text is stored.
- ``msg.inbound_events``: kind (``join``, ``stop``, ``other``) and time of inbound messages; the
  text is never stored (FR-MSG-018).
- ``msg.provider_events``: the webhook inbox, ``UNIQUE (tenant_id, provider, event_key)``,
  payload encrypted with the school's key (``sos_readonly`` gets nothing).

``core.resolve_public_handle`` (ADR-0047, amended): ``meta_waba`` handles also resolve while the
school is ``suspended``, so a parent's STOP is still honoured (the service stores and applies
only opt-outs then, and sends nothing). The downgrade restores the 0055 body.

The permission catalog gets ``messaging.configure`` (step-up), ``messaging.optin.record`` and
``message.read`` (rows written out here, like 0051). Role grants of existing schools need the
post-migration system-role sync (ADR-0022).

Expand-only (invariant 12). Downgrade drops the tables (lossy: messaging accounts, opt-in
records, message history) and removes the new catalog keys no role holds.

Revision ID: 0057_messaging_core
Revises: 0056_certificate_verifications
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057_messaging_core"
down_revision = "0056_certificate_verifications"
branch_labels = None
depends_on = None

CODE = r"'^[a-z][a-z0-9_]{1,39}$'"

TABLES_SQL = rf"""
CREATE TABLE msg.accounts (
  id                     uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL,
  provider               text NOT NULL DEFAULT 'meta_cloud',
  mode                   text NOT NULL,
  business_id            text,
  waba_id                text NOT NULL,
  phone_number_id        text NOT NULL,
  display_phone          text,
  display_name           text,
  status                 text NOT NULL,
  pause_reason           text,
  quality_rating         text,
  messaging_limit        text,
  number_status          text,
  currency               text,
  currency_confirmed_at  timestamptz,
  token_ciphertext       bytea,
  pin_ciphertext         bytea,
  waba_handle_id         uuid,
  connected_by           uuid,
  connected_at           timestamptz,
  disconnected_by        uuid,
  disconnected_at        timestamptz,
  paused_at              timestamptz,
  last_synced_at         timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  version                int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT accounts_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT accounts_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT accounts_handle_fk FOREIGN KEY (tenant_id, waba_handle_id)
    REFERENCES core.public_handles (tenant_id, id),
  CONSTRAINT accounts_connected_by_fk FOREIGN KEY (connected_by) REFERENCES core.users (id),
  CONSTRAINT accounts_disconnected_by_fk FOREIGN KEY (disconnected_by)
    REFERENCES core.users (id),
  CONSTRAINT accounts_provider_check CHECK (provider IN ('meta_cloud')),
  CONSTRAINT accounts_mode_check CHECK (mode IN ('embedded_signup','manual_token')),
  CONSTRAINT accounts_status_check
    CHECK (status IN ('connecting','active','paused','disconnected')),
  CONSTRAINT accounts_waba_id_format CHECK (waba_id ~ '^[0-9]{{1,32}}$'),
  CONSTRAINT accounts_phone_number_id_format CHECK (phone_number_id ~ '^[0-9]{{1,32}}$'),
  CONSTRAINT accounts_business_id_format
    CHECK (business_id IS NULL OR business_id ~ '^[0-9]{{1,32}}$'),
  CONSTRAINT accounts_pause_reason_format
    CHECK (pause_reason IS NULL OR pause_reason ~ {CODE}),
  CONSTRAINT accounts_paused_has_reason CHECK (status <> 'paused' OR pause_reason IS NOT NULL),
  CONSTRAINT accounts_display_name_length
    CHECK (display_name IS NULL OR char_length(display_name) <= 200),
  CONSTRAINT accounts_display_phone_length
    CHECK (display_phone IS NULL OR char_length(display_phone) <= 32),
  CONSTRAINT accounts_currency_format CHECK (currency IS NULL OR currency ~ '^[A-Z]{{3}}$'),
  -- A disconnected account keeps no secret; a live one always has its token (ADR-0043 dec. 5).
  CONSTRAINT accounts_disconnected CHECK ((status = 'disconnected') =
    (disconnected_at IS NOT NULL)),
  CONSTRAINT accounts_secrets CHECK (CASE WHEN status = 'disconnected'
    THEN token_ciphertext IS NULL AND pin_ciphertext IS NULL
    ELSE token_ciphertext IS NOT NULL END)
);
-- One account that is not disconnected per school.
CREATE UNIQUE INDEX accounts_one_live ON msg.accounts (tenant_id)
  WHERE status <> 'disconnected';
CREATE INDEX accounts_waba_idx ON msg.accounts (tenant_id, waba_id);
CREATE TRIGGER accounts_set_updated_at BEFORE UPDATE ON msg.accounts
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE msg.templates (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  account_id            uuid NOT NULL,
  template_key          text NOT NULL,
  template_version      int NOT NULL CHECK (template_version BETWEEN 1 AND 99),
  language              text NOT NULL,
  provider_name         text NOT NULL,
  provider_template_id  text,
  status                text NOT NULL,
  provider_category     text,
  paused_by_sos         boolean NOT NULL DEFAULT false,
  rejected_reason       text,
  error_code            text,
  last_synced_at        timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT templates_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT templates_key UNIQUE (tenant_id, account_id, template_key, template_version, language),
  CONSTRAINT templates_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT templates_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES msg.accounts (tenant_id, id),
  CONSTRAINT templates_key_format CHECK (template_key ~ '^[a-z][a-z_]{{2,30}}$'),
  CONSTRAINT templates_language_check CHECK (language IN ('en','te')),
  CONSTRAINT templates_provider_name_format CHECK (provider_name ~ '^sos_[a-z_]+_v[0-9]+_(en|te)$'),
  CONSTRAINT templates_status_check CHECK (status IN ('not_created','pending','approved',
    'rejected','paused','disabled')),
  CONSTRAINT templates_category_format
    CHECK (provider_category IS NULL OR provider_category ~ '^[A-Z_]{{1,40}}$'),
  CONSTRAINT templates_rejected_reason_length
    CHECK (rejected_reason IS NULL OR char_length(rejected_reason) <= 200),
  CONSTRAINT templates_error_code_format CHECK (error_code IS NULL OR error_code ~ {CODE})
);
CREATE TRIGGER templates_set_updated_at BEFORE UPDATE ON msg.templates
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE msg.settings (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  notices_enabled         boolean NOT NULL DEFAULT false,
  absence_alerts_enabled  boolean NOT NULL DEFAULT false,
  fee_reminders_enabled   boolean NOT NULL DEFAULT false,
  fee_receipts_enabled    boolean NOT NULL DEFAULT false,
  absence_alert_time      time NOT NULL DEFAULT '11:00',
  quiet_start             time NOT NULL DEFAULT '21:00',
  quiet_end               time NOT NULL DEFAULT '07:00',
  daily_cap               int CHECK (daily_cap IS NULL OR daily_cap BETWEEN 1 AND 1000000),
  office_phone            text,
  updated_by              uuid,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  version                 int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT settings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT settings_one_per_school UNIQUE (tenant_id),
  CONSTRAINT settings_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT settings_updated_by_fk FOREIGN KEY (updated_by) REFERENCES core.users (id),
  CONSTRAINT settings_office_phone_format
    CHECK (office_phone IS NULL OR office_phone ~ '^[0-9]{{10,11}}$')
);
CREATE TRIGGER settings_set_updated_at BEFORE UPDATE ON msg.settings
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE msg.optins (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  guardian_id             uuid NOT NULL,
  category                text NOT NULL,
  seq                     int NOT NULL CHECK (seq >= 1),
  status                  text NOT NULL,
  method                  text NOT NULL,
  wording_version         text NOT NULL,
  decided_on              date NOT NULL,
  evidence_document_id    uuid,
  phone_bidx              bytea,
  phone_key_version       int,
  recorded_by             uuid,
  recorded_by_membership  uuid,
  recorded_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT optins_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT optins_seq_key UNIQUE (tenant_id, guardian_id, category, seq),
  CONSTRAINT optins_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  -- The record goes with the guardian (docs/05 §13); the audit event keeps the fact.
  CONSTRAINT optins_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT optins_evidence_fk FOREIGN KEY (tenant_id, evidence_document_id)
    REFERENCES kb.documents (tenant_id, id),
  CONSTRAINT optins_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT optins_membership_fk FOREIGN KEY (tenant_id, recorded_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT optins_category_check CHECK (category IN ('notices','attendance','fees')),
  CONSTRAINT optins_status_check CHECK (status IN ('opted_in','opted_out')),
  CONSTRAINT optins_method_check CHECK (method IN ('whatsapp_join','signed_form','in_person',
    'whatsapp_stop','staff_recorded_stop')),
  CONSTRAINT optins_wording_format CHECK (wording_version ~ '^[a-z0-9][a-z0-9-]{{2,39}}$'),
  -- Methods per status (ADR-0043 decision 7, owner decision D26).
  CONSTRAINT optins_method_by_status CHECK (CASE status
    WHEN 'opted_in' THEN method IN ('whatsapp_join','signed_form','in_person')
    ELSE method IN ('whatsapp_stop','staff_recorded_stop') END),
  CONSTRAINT optins_signed_form_has_evidence
    CHECK (method <> 'signed_form' OR evidence_document_id IS NOT NULL),
  -- Staff decisions name the recorder; the parent's own messages have none.
  CONSTRAINT optins_recorder CHECK (CASE WHEN method IN ('whatsapp_join','whatsapp_stop')
    THEN recorded_by IS NULL AND recorded_by_membership IS NULL
    ELSE recorded_by IS NOT NULL AND recorded_by_membership IS NOT NULL END),
  -- An opt-in is for the number the guardian had at the time (a new number needs a new one).
  CONSTRAINT optins_phone CHECK (status <> 'opted_in' OR
    (phone_bidx IS NOT NULL AND octet_length(phone_bidx) = 32 AND phone_key_version >= 1))
);
CREATE INDEX optins_guardian_idx ON msg.optins (tenant_id, guardian_id, category, seq DESC);
CREATE INDEX optins_evidence_idx ON msg.optins (tenant_id, evidence_document_id)
  WHERE evidence_document_id IS NOT NULL;

CREATE TABLE msg.join_codes (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  code_hash    bytea NOT NULL CHECK (octet_length(code_hash) = 32),
  guardian_id  uuid NOT NULL,
  categories   text[] NOT NULL,
  expires_at   timestamptz NOT NULL,
  used_at      timestamptz,
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT join_codes_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT join_codes_code_key UNIQUE (tenant_id, code_hash),
  CONSTRAINT join_codes_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT join_codes_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT join_codes_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT join_codes_categories_check CHECK (cardinality(categories) BETWEEN 1 AND 3
    AND categories <@ ARRAY['notices','attendance','fees']::text[]),
  CONSTRAINT join_codes_expiry CHECK (expires_at > created_at)
);
CREATE INDEX join_codes_expires_idx ON msg.join_codes (tenant_id, expires_at);

CREATE TABLE msg.messages (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  purpose               text NOT NULL,
  kind                  text NOT NULL,
  template_key          text,
  template_version      int,
  language              text NOT NULL,
  language_fallback     boolean NOT NULL DEFAULT false,
  guardian_id           uuid,
  student_id            uuid,
  source_type           text NOT NULL,
  source_id             uuid NOT NULL,
  dedupe_key            text NOT NULL,
  phone_bidx            bytea,
  phone_key_version     int,
  phone_last4           text,
  vars_ciphertext       bytea,
  public_handle_id      uuid,
  provider_message_id   text,
  status                text NOT NULL DEFAULT 'queued',
  skip_reason           text,
  error_code            text,
  attempts              int NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 20),
  billable              boolean,
  pricing_category      text,
  queued_at             timestamptz NOT NULL DEFAULT now(),
  sent_at               timestamptz,
  delivered_at          timestamptz,
  read_at               timestamptz,
  failed_at             timestamptz,
  status_at             timestamptz NOT NULL DEFAULT now(),
  created_by            uuid,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT messages_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT messages_once UNIQUE (tenant_id, purpose, source_id, guardian_id, dedupe_key),
  CONSTRAINT messages_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT messages_guardian_fk FOREIGN KEY (tenant_id, guardian_id)
    REFERENCES sis.guardians (tenant_id, id) ON DELETE SET NULL (guardian_id),
  CONSTRAINT messages_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT messages_handle_fk FOREIGN KEY (tenant_id, public_handle_id)
    REFERENCES core.public_handles (tenant_id, id) ON DELETE SET NULL (public_handle_id),
  CONSTRAINT messages_created_by_fk FOREIGN KEY (created_by) REFERENCES core.users (id),
  CONSTRAINT messages_purpose_check CHECK (purpose IN ('optin_confirm','school_notice',
    'absence_alert','fee_reminder','fee_receipt','optin_stopped')),
  CONSTRAINT messages_kind_check CHECK (kind IN ('template','text')),
  CONSTRAINT messages_template CHECK ((kind = 'template') =
    (template_key IS NOT NULL AND template_version IS NOT NULL)),
  CONSTRAINT messages_language_check CHECK (language IN ('en','te')),
  CONSTRAINT messages_source_type_format CHECK (source_type ~ {CODE}),
  CONSTRAINT messages_dedupe_key_format CHECK (dedupe_key ~ '^[A-Za-z0-9:._-]{{1,80}}$'),
  CONSTRAINT messages_status_check CHECK (status IN ('queued','skipped','sent','delivered',
    'read','failed')),
  CONSTRAINT messages_skip_reason CHECK ((status = 'skipped') = (skip_reason IS NOT NULL)),
  CONSTRAINT messages_skip_reason_format CHECK (skip_reason IS NULL OR skip_reason ~ {CODE}),
  CONSTRAINT messages_error_code_format
    CHECK (error_code IS NULL OR error_code ~ '^[a-z0-9_:]{{1,40}}$'),
  CONSTRAINT messages_phone_last4 CHECK (phone_last4 IS NULL OR phone_last4 ~ '^[0-9]{{4}}$'),
  CONSTRAINT messages_phone_bidx CHECK (phone_bidx IS NULL OR octet_length(phone_bidx) = 32),
  CONSTRAINT messages_provider_id_format
    CHECK (provider_message_id IS NULL OR char_length(provider_message_id) <= 200),
  CONSTRAINT messages_pricing_format
    CHECK (pricing_category IS NULL OR pricing_category ~ '^[a-z_]{{1,40}}$')
);
CREATE UNIQUE INDEX messages_provider_id_key ON msg.messages (tenant_id, provider_message_id)
  WHERE provider_message_id IS NOT NULL;
CREATE INDEX messages_guardian_idx ON msg.messages (tenant_id, guardian_id, queued_at DESC);
CREATE INDEX messages_student_idx ON msg.messages (tenant_id, student_id, queued_at DESC)
  WHERE student_id IS NOT NULL;
CREATE INDEX messages_queued_idx ON msg.messages (tenant_id, queued_at DESC);
CREATE INDEX messages_handle_idx ON msg.messages (tenant_id, public_handle_id)
  WHERE public_handle_id IS NOT NULL;
CREATE TRIGGER messages_set_updated_at BEFORE UPDATE ON msg.messages
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- FR-MSG-016: statuses never move backwards, final states stay final (defence in depth next to
-- the service, which ignores late or out-of-order webhooks).
CREATE FUNCTION msg.tg_messages_status_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  rank_old int := CASE OLD.status WHEN 'queued' THEN 0 WHEN 'sent' THEN 1
    WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 9 END;
  rank_new int := CASE NEW.status WHEN 'queued' THEN 0 WHEN 'sent' THEN 1
    WHEN 'delivered' THEN 2 WHEN 'read' THEN 3 ELSE 9 END;
BEGIN
  IF NEW.purpose <> OLD.purpose OR NEW.kind <> OLD.kind OR NEW.source_id <> OLD.source_id
     OR NEW.source_type <> OLD.source_type OR NEW.dedupe_key <> OLD.dedupe_key
     OR NEW.tenant_id <> OLD.tenant_id OR NEW.id <> OLD.id
     OR (NEW.guardian_id IS DISTINCT FROM OLD.guardian_id AND NEW.guardian_id IS NOT NULL) THEN
    RAISE EXCEPTION 'message identity columns are fixed' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = OLD.status THEN
    RETURN NEW;
  END IF;
  IF OLD.status IN ('read','failed','skipped') THEN
    RAISE EXCEPTION 'a final message status stays final' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'skipped' AND OLD.status <> 'queued' THEN
    RAISE EXCEPTION 'only a queued message can be skipped' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'failed' AND OLD.status NOT IN ('queued','sent') THEN
    RAISE EXCEPTION 'a delivered message cannot fail' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IN ('queued','sent','delivered','read') AND rank_new < rank_old THEN
    RAISE EXCEPTION 'message statuses only move forward' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION msg.tg_messages_status_guard() FROM PUBLIC;
CREATE TRIGGER messages_status_guard BEFORE UPDATE ON msg.messages
  FOR EACH ROW EXECUTE FUNCTION msg.tg_messages_status_guard();

CREATE TABLE msg.inbound_events (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  phone_bidx         bytea NOT NULL CHECK (octet_length(phone_bidx) = 32),
  phone_key_version  int NOT NULL CHECK (phone_key_version >= 1),
  kind               text NOT NULL,
  outcome            text,
  received_at        timestamptz NOT NULL,
  replied            boolean NOT NULL DEFAULT false,
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT inbound_events_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT inbound_events_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT inbound_events_kind_check CHECK (kind IN ('join','stop','other')),
  CONSTRAINT inbound_events_outcome_format CHECK (outcome IS NULL OR outcome ~ {CODE})
);
CREATE INDEX inbound_events_phone_idx
  ON msg.inbound_events (tenant_id, phone_bidx, received_at DESC);
CREATE INDEX inbound_events_received_idx ON msg.inbound_events (tenant_id, received_at);

CREATE TABLE msg.provider_events (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  provider            text NOT NULL DEFAULT 'meta_cloud',
  account_id          uuid NOT NULL,
  event_key           text NOT NULL,
  event_type          text NOT NULL,
  event_at            timestamptz,
  received_at         timestamptz NOT NULL DEFAULT now(),
  payload_ciphertext  bytea,
  status              text NOT NULL DEFAULT 'received',
  attempts            int NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 100),
  error_code          text,
  processed_at        timestamptz,
  CONSTRAINT provider_events_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT provider_events_key UNIQUE (tenant_id, provider, event_key),
  CONSTRAINT provider_events_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT provider_events_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES msg.accounts (tenant_id, id),
  CONSTRAINT provider_events_provider_check CHECK (provider IN ('meta_cloud')),
  CONSTRAINT provider_events_key_length CHECK (char_length(event_key) BETWEEN 1 AND 300),
  CONSTRAINT provider_events_type_check CHECK (event_type IN ('message_status',
    'inbound_message','template_status','template_category','phone_quality',
    'account_update','other')),
  CONSTRAINT provider_events_status_check
    CHECK (status IN ('received','processed','ignored','failed')),
  CONSTRAINT provider_events_error_code_format
    CHECK (error_code IS NULL OR error_code ~ {CODE}),
  CONSTRAINT provider_events_processed
    CHECK ((status = 'received') = (processed_at IS NULL))
);
CREATE INDEX provider_events_pending_idx ON msg.provider_events (tenant_id, received_at)
  WHERE status = 'received';
CREATE INDEX provider_events_processed_idx ON msg.provider_events (tenant_id, processed_at)
  WHERE processed_at IS NOT NULL;
"""  # noqa: S608 - DDL; only the constant CODE pattern is interpolated

# Children before parents (the order the module's offboarding purge deletes them in).
TENANT_TABLES = (
    "msg.provider_events",
    "msg.inbound_events",
    "msg.messages",
    "msg.join_codes",
    "msg.optins",
    "msg.settings",
    "msg.templates",
    "msg.accounts",
)

GRANTS_SQL = r"""
-- Narrow the default table-wide grants (bootstrap.sql) to what the app writes.
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON
  msg.accounts, msg.templates, msg.settings, msg.optins, msg.join_codes, msg.messages,
  msg.inbound_events, msg.provider_events FROM sos_app;
GRANT UPDATE (status, pause_reason, quality_rating, messaging_limit, number_status, display_phone,
  display_name, business_id, currency, currency_confirmed_at, token_ciphertext, pin_ciphertext,
  waba_handle_id, disconnected_by, disconnected_at, paused_at, last_synced_at, updated_at,
  version) ON msg.accounts TO sos_app;
GRANT UPDATE (provider_template_id, status, provider_category, paused_by_sos, rejected_reason,
  error_code, last_synced_at, updated_at) ON msg.templates TO sos_app;
GRANT UPDATE (notices_enabled, absence_alerts_enabled, fee_reminders_enabled,
  fee_receipts_enabled, absence_alert_time, quiet_start, quiet_end, daily_cap, office_phone,
  updated_by, updated_at, version) ON msg.settings TO sos_app;
-- msg.optins is append-only for the app (SELECT, INSERT), except the phone blind index the DEK
-- rotation recomputes under the new key version (SEC-012; the decision itself never changes).
GRANT UPDATE (phone_bidx, phone_key_version) ON msg.optins TO sos_app;
GRANT UPDATE (used_at) ON msg.join_codes TO sos_app;
GRANT DELETE ON msg.join_codes TO sos_app;
GRANT UPDATE (status, skip_reason, error_code, attempts, billable, pricing_category,
  provider_message_id, public_handle_id, vars_ciphertext, sent_at, delivered_at, read_at,
  failed_at, status_at, updated_at) ON msg.messages TO sos_app;
GRANT DELETE ON msg.messages TO sos_app;
GRANT UPDATE (replied, outcome) ON msg.inbound_events TO sos_app;
GRANT DELETE ON msg.inbound_events TO sos_app;
GRANT UPDATE (status, attempts, error_code, processed_at, payload_ciphertext)
  ON msg.provider_events TO sos_app;
GRANT DELETE ON msg.provider_events TO sos_app;
-- Secrets, code hashes and raw provider payloads never reach the read-only role (docs/05 §15).
REVOKE ALL ON msg.accounts, msg.provider_events, msg.join_codes FROM sos_readonly;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "messaging.configure",
        "description": (
            "Connect or disconnect the school's WhatsApp number, sync templates, change "
            "messaging switches and times, and resume sending after a pause"
        ),
        "sensitivity": "critical",
        "step_up": True,
        "is_platform": False,
    },
    {
        "key": "messaging.optin.record",
        "description": (
            "Record a guardian's WhatsApp opt-in or opt-out with its evidence, and print join "
            "slips (class teachers: their sections)"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
    {
        "key": "message.read",
        "description": (
            "See WhatsApp delivery reports, message history and opt-in states (class teachers: "
            "their sections)"
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


RESOLVER_SIGNATURE = "core.resolve_public_handle(text,bytea)"


def _resolver(suspended_kinds: str) -> str:
    return rf"""
CREATE OR REPLACE FUNCTION core.resolve_public_handle(p_kind text, p_key_hash bytea)
  RETURNS TABLE (tenant_id uuid, handle_id uuid)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT h.tenant_id, h.id
  FROM core.public_handles AS h
  JOIN core.tenants AS t ON t.id = h.tenant_id
  WHERE h.kind = p_kind
    AND h.key_hash = p_key_hash
    AND h.revoked_at IS NULL
    AND (h.expires_at IS NULL OR h.expires_at > pg_catalog.now())
    AND (t.status = 'active'
         OR (t.status = 'suspended' AND h.kind IN ({suspended_kinds})))
$$;
"""  # noqa: S608 - DDL; only constant kind lists are interpolated


# ADR-0047 amendment (R2-1C, 2026-10-18): a parent's STOP must be honoured while the school is
# suspended (consent withdrawal, PRV-025), so Meta's webhook also reaches suspended schools; the
# messaging service then stores and applies opt-outs only and sends nothing.
RESOLVER_WITH_META = _resolver("'parent_receipt', 'cert_verify', 'meta_waba'")
RESOLVER_0055 = _resolver("'parent_receipt', 'cert_verify'")


def _replace_resolver(sql: str) -> None:
    # The function is owned by sos_definer; CREATE on core exists only inside this transaction
    # (as in 0055). Grants and ownership survive CREATE OR REPLACE.
    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(sql)
    op.execute(f"REVOKE ALL ON FUNCTION {RESOLVER_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {RESOLVER_SIGNATURE} TO sos_app")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def upgrade() -> None:
    _replace_resolver(RESOLVER_WITH_META)
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
    _replace_resolver(RESOLVER_0055)
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
    op.execute("DROP FUNCTION IF EXISTS msg.tg_messages_status_guard()")

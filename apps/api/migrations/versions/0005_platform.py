"""Control-plane schema ``platform`` (docs/16 §7, ADR-0013, ADR-0017).

- Tables: operators, operator_roles, plans, billing_accounts, deployments, subscriptions,
  invoice_sequences, invoices, invoice_lines, payments, usage_daily, usage_threshold_events,
  feature_flags, announcements, support_tickets, support_messages, breakglass_requests, job_runs.
  (``platform.audit_events`` / ``platform.audit_chain_head`` already exist from 0002_audit.)
- No RLS (no student data, not tenant-owned). Isolation is by grants: default privileges give
  ``sos_platform`` DML; ``sos_app`` gets SELECT on ``platform.feature_flags`` only.
- Immutability triggers: published plans (FR-PLT-010) and issued invoices (FR-PLT-016).
- Two-person rules (SEC-029) as DB CHECKs: offboarding approver <> requester, emergency
  break-glass confirmers distinct from each other.
- Definer functions (owned by sos_definer, search_path pinned; ADR-0013 §2):
  ``core.current_subscription()`` for sos_app (own tenant's plan/usage/invoices, FR-PLT-030) and
  ``core.create_owner_invite(...)`` for sos_platform (first owner of a provisioning school,
  FR-PLT-002). ``definer_access`` is added to core.roles, core.membership_roles and
  core.membership_scopes (allowlisted) for the invite.

Requirements: FR-PLT-001..030, SEC-026, SEC-027, SEC-029.

Revision ID: 0005_platform
Revises: 0004_authz_seed
Create Date: 2026-09-26
"""

from __future__ import annotations

from alembic import op

revision = "0005_platform"
down_revision = "0004_authz_seed"
branch_labels = None
depends_on = None

# UUIDv7 inside SQL (same construction as 0003_core_schema).
_UUID7 = """pg_catalog.encode(pg_catalog.set_bit(pg_catalog.set_bit(
            overlay(pg_catalog.uuid_send(pg_catalog.gen_random_uuid())
                    PLACING substring(pg_catalog.int8send(
                      (extract(epoch FROM pg_catalog.clock_timestamp()) * 1000)::bigint) FROM 3)
                    FROM 1 FOR 6),
            52, 1), 53, 1), 'hex')::uuid"""

TABLES_SQL = r"""
CREATE TABLE platform.operators (
  id              uuid PRIMARY KEY,
  idp_subject     text UNIQUE CHECK (char_length(idp_subject) BETWEEN 1 AND 255),
  email           public.citext NOT NULL UNIQUE,
  display_name    text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 200),
  status          text NOT NULL CHECK (status IN ('invited','active','deactivated')),
  mfa_enrolled    boolean NOT NULL DEFAULT false,
  invited_by      uuid REFERENCES platform.operators(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_login_at   timestamptz,
  deactivated_at  timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT operators_deactivated_at CHECK ((status = 'deactivated') = (deactivated_at IS NOT NULL)),
  CONSTRAINT operators_active_needs_mfa
    CHECK (status <> 'active' OR (idp_subject IS NOT NULL AND mfa_enrolled))
);

CREATE TABLE platform.operator_roles (
  operator_id  uuid NOT NULL REFERENCES platform.operators(id),
  role_key     text NOT NULL CHECK (role_key IN
                 ('platform_owner','platform_engineer','support_agent','billing_admin',
                  'platform_viewer')),
  granted_by   uuid REFERENCES platform.operators(id),
  granted_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (operator_id, role_key),
  CONSTRAINT operator_roles_no_self_grant CHECK (granted_by IS NULL OR granted_by <> operator_id)
);

CREATE TABLE platform.plans (
  id                     uuid PRIMARY KEY,
  code                   text NOT NULL CHECK (code ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  version                int  NOT NULL CHECK (version >= 1),
  name                   text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
  tier                   text NOT NULL CHECK (tier IN ('shared','dedicated')),
  billing_period         text NOT NULL CHECK (billing_period IN ('monthly','annual')),
  pricing_model          text NOT NULL CHECK (pricing_model IN ('flat','per_student')),
  base_price_inr         numeric(14,2) NOT NULL CHECK (base_price_inr >= 0),
  per_student_price_inr  numeric(14,2) CHECK (per_student_price_inr >= 0),
  included_students      int CHECK (included_students >= 0),
  gst_rate               numeric(5,2) NOT NULL DEFAULT 18.00 CHECK (gst_rate IN (0, 5, 12, 18, 28)),
  sac_code               text NOT NULL CHECK (sac_code ~ '^[0-9]{6}$'),
  trial_days             int NOT NULL DEFAULT 30 CHECK (trial_days BETWEEN 0 AND 365),
  limits                 jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(limits) = 'object'),
  features               jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(features) = 'object'),
  status                 text NOT NULL CHECK (status IN ('draft','published','retired')),
  published_at           timestamptz,
  created_by             uuid NOT NULL REFERENCES platform.operators(id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (code, version),
  CONSTRAINT plans_per_student_price
    CHECK ((pricing_model = 'per_student') = (per_student_price_inr IS NOT NULL)),
  CONSTRAINT plans_published_at CHECK ((status = 'draft') = (published_at IS NULL))
);

CREATE TABLE platform.billing_accounts (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL UNIQUE,
  legal_name            text NOT NULL CHECK (char_length(legal_name) BETWEEN 1 AND 200),
  gstin                 text CHECK (gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'),
  pan                   text CHECK (pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'),
  billing_email         public.citext NOT NULL,
  billing_contact_name  text,
  billing_phone         text CHECK (billing_phone ~ '^\+?[0-9]{10,13}$'),
  address_line1         text NOT NULL,
  address_line2         text,
  city                  text NOT NULL,
  district              text,
  postal_code           text NOT NULL CHECK (postal_code ~ '^[1-9][0-9]{5}$'),
  state_code            text NOT NULL CHECK (state_code ~ '^[0-9]{2}$'),
  po_reference          text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT billing_accounts_gstin_state CHECK (gstin IS NULL OR substr(gstin, 1, 2) = state_code),
  CONSTRAINT billing_accounts_gstin_pan
    CHECK (gstin IS NULL OR pan IS NULL OR substr(gstin, 3, 10) = pan)
);

CREATE TABLE platform.deployments (
  id                        uuid PRIMARY KEY,
  tenant_id                 uuid NOT NULL UNIQUE,
  tenant_code               text NOT NULL UNIQUE CHECK (tenant_code ~ '^[a-z][a-z0-9-]{1,31}$'),
  school_name               text NOT NULL CHECK (char_length(school_name) BETWEEN 1 AND 200),
  boards                    text[] NOT NULL DEFAULT '{}',
  mode                      text NOT NULL CHECK (mode IN ('shared','dedicated')),
  region                    text NOT NULL DEFAULT 'ap-south-1' CHECK (region = 'ap-south-1'),
  backup_region             text NOT NULL DEFAULT 'ap-south-2' CHECK (backup_region = 'ap-south-2'),
  host_ref                  text CHECK (host_ref ~ '^i-[0-9a-f]{8,17}$'),
  hostname                  text CHECK (hostname ~ '^[a-z0-9.-]{1,253}$'),
  custom_domain             public.citext UNIQUE CHECK (custom_domain ~ '^[a-z0-9.-]{1,253}$'),
  tenant_status             text NOT NULL CHECK (tenant_status IN
                              ('provisioning','active','suspended','offboarding','deleted')),
  tenant_status_reason      text,
  status                    text NOT NULL CHECK (status IN
                              ('provisioning','healthy','degraded','unreachable','decommissioned')),
  app_version               text,
  target_version            text,
  last_heartbeat_at         timestamptz,
  last_heartbeat            jsonb,
  heartbeat_key_id          text,
  heartbeat_key_ciphertext  bytea,
  heartbeat_next_key_id     text,
  heartbeat_next_key_ciphertext bytea,
  heartbeat_rotation_started_at timestamptz,
  offboard_requested_by     uuid REFERENCES platform.operators(id),
  offboard_requested_at     timestamptz,
  offboard_reason           text,
  offboard_approved_by      uuid REFERENCES platform.operators(id),
  offboard_approved_at      timestamptz,
  deletion_certificate_ref  text,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  version                   int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT deployments_shared_has_no_host
    CHECK (mode = 'dedicated' OR (host_ref IS NULL AND custom_domain IS NULL
                                  AND heartbeat_key_id IS NULL)),
  CONSTRAINT deployments_dedicated_has_key
    CHECK (mode = 'shared' OR status = 'decommissioned' OR heartbeat_key_ciphertext IS NOT NULL),
  CONSTRAINT deployments_key_pair CHECK ((heartbeat_key_id IS NULL) = (heartbeat_key_ciphertext IS NULL)),
  CONSTRAINT deployments_next_key_pair
    CHECK ((heartbeat_next_key_id IS NULL) = (heartbeat_next_key_ciphertext IS NULL)),
  CONSTRAINT deployments_rotation_started
    CHECK ((heartbeat_next_key_id IS NULL) = (heartbeat_rotation_started_at IS NULL)),
  CONSTRAINT deployments_offboard_request
    CHECK ((offboard_requested_by IS NULL) = (offboard_requested_at IS NULL)),
  CONSTRAINT deployments_offboard_approved_at
    CHECK ((offboard_approved_by IS NULL) = (offboard_approved_at IS NULL)),
  -- SEC-029: two different operators request and approve offboarding.
  CONSTRAINT deployments_offboard_two_person
    CHECK (offboard_approved_by IS NULL
           OR (offboard_requested_by IS NOT NULL AND offboard_approved_by <> offboard_requested_by)),
  CONSTRAINT deployments_suspension_reason
    CHECK (tenant_status <> 'suspended' OR tenant_status_reason IS NOT NULL)
);

CREATE TABLE platform.subscriptions (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL REFERENCES platform.deployments(tenant_id),
  billing_account_id    uuid NOT NULL REFERENCES platform.billing_accounts(id),
  plan_id               uuid NOT NULL REFERENCES platform.plans(id),
  pending_plan_id       uuid REFERENCES platform.plans(id),
  status                text NOT NULL CHECK (status IN ('trial','active','past_due','suspended','cancelled')),
  trial_ends_at         timestamptz,
  current_period_start  date NOT NULL,
  current_period_end    date NOT NULL,
  price_override_inr    numeric(14,2) CHECK (price_override_inr >= 0),
  override_reason       text,
  past_due_since        date,
  grace_ends_on         date,
  suspended_at          timestamptz,
  suspended_by          uuid REFERENCES platform.operators(id),
  suspension_reason     text,
  exam_window_override_by uuid REFERENCES platform.operators(id),
  cancel_at_period_end  boolean NOT NULL DEFAULT false,
  cancelled_at          timestamptz,
  cancel_reason         text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT subscriptions_period CHECK (current_period_end > current_period_start),
  CONSTRAINT subscriptions_trial_end CHECK (status <> 'trial' OR trial_ends_at IS NOT NULL),
  CONSTRAINT subscriptions_override_reason
    CHECK ((price_override_inr IS NULL) = (override_reason IS NULL)),
  CONSTRAINT subscriptions_past_due_fields
    CHECK (status NOT IN ('past_due','suspended')
           OR (past_due_since IS NOT NULL AND grace_ends_on IS NOT NULL)),
  CONSTRAINT subscriptions_grace_15_days
    CHECK (grace_ends_on IS NULL OR grace_ends_on >= past_due_since + 15),
  CONSTRAINT subscriptions_suspension_fields
    CHECK (status <> 'suspended'
           OR (suspended_at IS NOT NULL AND suspended_by IS NOT NULL AND suspension_reason IS NOT NULL)),
  CONSTRAINT subscriptions_cancelled_at CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
  CONSTRAINT subscriptions_cancel_reason CHECK (NOT cancel_at_period_end OR cancel_reason IS NOT NULL)
);
CREATE UNIQUE INDEX one_live_subscription ON platform.subscriptions (tenant_id)
  WHERE status <> 'cancelled';

CREATE TABLE platform.invoice_sequences (
  financial_year  text PRIMARY KEY CHECK (financial_year ~ '^[0-9]{4}-[0-9]{2}$'),
  prefix          text NOT NULL DEFAULT 'SOS' CHECK (prefix ~ '^[A-Z]{2,5}$'),
  last_number     int  NOT NULL DEFAULT 0 CHECK (last_number >= 0),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.invoices (
  id                          uuid PRIMARY KEY,
  tenant_id                   uuid NOT NULL,
  subscription_id             uuid NOT NULL REFERENCES platform.subscriptions(id),
  billing_account_id          uuid NOT NULL REFERENCES platform.billing_accounts(id),
  status                      text NOT NULL CHECK (status IN ('draft','issued','paid','void')),
  financial_year              text REFERENCES platform.invoice_sequences(financial_year),
  sequence_no                 int CHECK (sequence_no > 0),
  -- CGST Rule 46: at most 16 characters, e.g. 'SOS/26-27/000123'.
  invoice_number              text UNIQUE CHECK (invoice_number ~ '^[A-Z0-9/-]{1,16}$'),
  period_start                date NOT NULL,
  period_end                  date NOT NULL,
  issue_date                  date,
  due_date                    date,
  currency                    char(3) NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
  supplier_legal_name         text NOT NULL,
  supplier_gstin              text NOT NULL,
  supplier_state_code         text NOT NULL CHECK (supplier_state_code ~ '^[0-9]{2}$'),
  recipient_legal_name        text NOT NULL,
  recipient_gstin             text,
  recipient_address           jsonb NOT NULL,
  place_of_supply_state_code  text NOT NULL CHECK (place_of_supply_state_code ~ '^[0-9]{2}$'),
  tax_type                    text NOT NULL CHECK (tax_type IN ('cgst_sgst','igst')),
  taxable_value_inr           numeric(14,2) NOT NULL DEFAULT 0 CHECK (taxable_value_inr >= 0),
  cgst_inr                    numeric(14,2) NOT NULL DEFAULT 0 CHECK (cgst_inr >= 0),
  sgst_inr                    numeric(14,2) NOT NULL DEFAULT 0 CHECK (sgst_inr >= 0),
  igst_inr                    numeric(14,2) NOT NULL DEFAULT 0 CHECK (igst_inr >= 0),
  total_inr                   numeric(14,2) NOT NULL DEFAULT 0,
  amount_paid_inr             numeric(14,2) NOT NULL DEFAULT 0 CHECK (amount_paid_inr >= 0),
  tds_inr                     numeric(14,2) NOT NULL DEFAULT 0 CHECK (tds_inr >= 0),
  notes                       text CHECK (char_length(notes) <= 1000),
  issued_by                   uuid REFERENCES platform.operators(id),
  issued_at                   timestamptz,
  voided_by                   uuid REFERENCES platform.operators(id),
  voided_at                   timestamptz,
  void_reason                 text,
  created_at                  timestamptz NOT NULL DEFAULT now(),
  updated_at                  timestamptz NOT NULL DEFAULT now(),
  version                     int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT invoices_period CHECK (period_end > period_start),
  CONSTRAINT invoices_total CHECK (total_inr = taxable_value_inr + cgst_inr + sgst_inr + igst_inr),
  CONSTRAINT invoices_tax_type CHECK (tax_type = CASE WHEN place_of_supply_state_code = supplier_state_code
                         THEN 'cgst_sgst' ELSE 'igst' END),
  CONSTRAINT invoices_tax_split CHECK ((tax_type = 'igst' AND cgst_inr = 0 AND sgst_inr = 0)
      OR (tax_type = 'cgst_sgst' AND igst_inr = 0 AND cgst_inr = sgst_inr)),
  CONSTRAINT invoices_number_iff_not_draft CHECK ((status = 'draft') = (invoice_number IS NULL)),
  CONSTRAINT invoices_number_fields CHECK ((invoice_number IS NULL) = (financial_year IS NULL AND sequence_no IS NULL
                                     AND issue_date IS NULL AND due_date IS NULL AND issued_at IS NULL)),
  CONSTRAINT invoices_due_after_issue CHECK (due_date IS NULL OR due_date >= issue_date),
  CONSTRAINT invoices_void_fields CHECK ((status = 'void') = (voided_at IS NOT NULL AND void_reason IS NOT NULL)),
  CONSTRAINT invoices_paid_covered CHECK (status <> 'paid' OR amount_paid_inr + tds_inr >= total_inr),
  UNIQUE (financial_year, sequence_no)
);
CREATE UNIQUE INDEX one_invoice_per_period ON platform.invoices (subscription_id, period_start)
  WHERE status <> 'void';
CREATE INDEX invoices_tenant ON platform.invoices (tenant_id, period_start DESC);
CREATE INDEX invoices_status_due ON platform.invoices (status, due_date);

CREATE TABLE platform.invoice_lines (
  id              uuid PRIMARY KEY,
  invoice_id      uuid NOT NULL REFERENCES platform.invoices(id) ON DELETE CASCADE,
  line_no         int  NOT NULL CHECK (line_no >= 1),
  kind            text NOT NULL CHECK (kind IN
                    ('subscription','per_student','addon','usage_overage','discount','adjustment')),
  description     text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 200),
  sac_code        text NOT NULL CHECK (sac_code ~ '^[0-9]{6}$'),
  quantity        numeric(12,3) NOT NULL CHECK (quantity > 0),
  unit_price_inr  numeric(14,2) NOT NULL,
  amount_inr      numeric(14,2) NOT NULL,
  gst_rate        numeric(5,2) NOT NULL CHECK (gst_rate IN (0, 5, 12, 18, 28)),
  UNIQUE (invoice_id, line_no),
  CONSTRAINT invoice_lines_sign CHECK (CASE kind WHEN 'discount'   THEN amount_inr <= 0
                   WHEN 'adjustment' THEN true
                   ELSE amount_inr >= 0 END)
);

CREATE TABLE platform.payments (
  id                   uuid PRIMARY KEY,
  invoice_id           uuid NOT NULL REFERENCES platform.invoices(id),
  tenant_id            uuid NOT NULL,
  provider             text NOT NULL CHECK (provider IN ('manual')),
  method               text NOT NULL CHECK (method IN ('bank_transfer','upi','cheque','other')),
  amount_inr           numeric(14,2) NOT NULL CHECK (amount_inr > 0),
  tds_inr              numeric(14,2) NOT NULL DEFAULT 0 CHECK (tds_inr >= 0),
  received_on          date NOT NULL,
  reference            text NOT NULL CHECK (reference ~ '^[A-Za-z0-9/_.-]{1,64}$'),
  provider_payment_id  text,
  status               text NOT NULL CHECK (status IN ('recorded','reversed')),
  notes                text CHECK (char_length(notes) <= 500),
  recorded_by          uuid NOT NULL REFERENCES platform.operators(id),
  recorded_at          timestamptz NOT NULL DEFAULT now(),
  reversed_by          uuid REFERENCES platform.operators(id),
  reversed_at          timestamptz,
  reversal_reason      text,
  UNIQUE (tenant_id, method, reference),
  CONSTRAINT payments_reversal_fields CHECK ((status = 'reversed') = (reversed_by IS NOT NULL AND reversed_at IS NOT NULL
                                  AND reversal_reason IS NOT NULL))
);
CREATE INDEX payments_invoice ON platform.payments (invoice_id);

CREATE TABLE platform.usage_daily (
  tenant_id         uuid NOT NULL,
  usage_date        date NOT NULL,
  source            text NOT NULL CHECK (source IN ('shared_collector','heartbeat')),
  active_users      int NOT NULL CHECK (active_users >= 0),
  staff_users       int NOT NULL CHECK (staff_users >= 0),
  students_active   int NOT NULL CHECK (students_active >= 0),
  storage_bytes     bigint NOT NULL CHECK (storage_bytes >= 0),
  documents         int NOT NULL CHECK (documents >= 0),
  ai_queries        int NOT NULL DEFAULT 0 CHECK (ai_queries >= 0),
  ai_input_tokens   bigint NOT NULL DEFAULT 0 CHECK (ai_input_tokens >= 0),
  ai_output_tokens  bigint NOT NULL DEFAULT 0 CHECK (ai_output_tokens >= 0),
  ai_cost_usd       numeric(14,4) NOT NULL DEFAULT 0 CHECK (ai_cost_usd >= 0),
  ai_cost_inr       numeric(14,2) NOT NULL DEFAULT 0 CHECK (ai_cost_inr >= 0),
  collected_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, usage_date)
);

-- 80% / 100% crossings are reported once per metric per billing period (FR-PLT-021).
CREATE TABLE platform.usage_threshold_events (
  tenant_id     uuid NOT NULL,
  metric        text NOT NULL CHECK (metric ~ '^[a-z_]{1,40}$'),
  threshold     smallint NOT NULL CHECK (threshold IN (80, 100)),
  period_start  date NOT NULL,
  usage_value   numeric(20,2) NOT NULL,
  limit_value   numeric(20,2) NOT NULL,
  crossed_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, metric, threshold, period_start)
);

CREATE TABLE platform.feature_flags (
  id               uuid PRIMARY KEY,
  key              text NOT NULL CHECK (key ~ '^[a-z0-9_]+(\.[a-z0-9_]+)+$'),
  tenant_id        uuid,
  enabled          boolean NOT NULL,
  rollout_percent  smallint CHECK (rollout_percent BETWEEN 0 AND 100),
  description      text CHECK (char_length(description) <= 300),
  updated_by       uuid REFERENCES platform.operators(id),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1 CHECK (version >= 1),
  UNIQUE NULLS NOT DISTINCT (key, tenant_id),
  CONSTRAINT feature_flags_rollout_global_only CHECK (tenant_id IS NULL OR rollout_percent IS NULL)
);

CREATE TABLE platform.announcements (
  id                   uuid PRIMARY KEY,
  title_en             text NOT NULL CHECK (char_length(title_en) BETWEEN 1 AND 120),
  title_te             text NOT NULL CHECK (char_length(title_te) BETWEEN 1 AND 120),
  body_en              text NOT NULL CHECK (char_length(body_en) BETWEEN 1 AND 1000),
  body_te              text NOT NULL CHECK (char_length(body_te) BETWEEN 1 AND 1000),
  severity             text NOT NULL CHECK (severity IN ('info','maintenance','warning','critical')),
  audience             text NOT NULL CHECK (audience IN ('all','tier','tenants')),
  audience_tier        text CHECK (audience_tier IN ('shared','dedicated')),
  audience_tenant_ids  uuid[] NOT NULL DEFAULT '{}',
  starts_at            timestamptz NOT NULL,
  ends_at              timestamptz NOT NULL,
  status               text NOT NULL CHECK (status IN ('draft','scheduled','cancelled')),
  created_by           uuid NOT NULL REFERENCES platform.operators(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT announcements_window CHECK (ends_at > starts_at),
  CONSTRAINT announcements_tier CHECK ((audience = 'tier') = (audience_tier IS NOT NULL)),
  CONSTRAINT announcements_tenants CHECK ((audience = 'tenants') = (cardinality(audience_tenant_ids) > 0))
);

CREATE TABLE platform.support_tickets (
  id                      uuid PRIMARY KEY,
  ticket_no               bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  tenant_id               uuid NOT NULL,
  opened_by_user_id       uuid,
  opened_by_operator_id   uuid REFERENCES platform.operators(id),
  channel                 text NOT NULL CHECK (channel IN ('app','email','phone','whatsapp')),
  category                text NOT NULL CHECK (category IN
                            ('access','import','data_quality','exports','documents','ask','billing','bug','other')),
  priority                text NOT NULL CHECK (priority IN ('p1','p2','p3','p4')),
  subject                 text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 200),
  status                  text NOT NULL CHECK (status IN
                            ('open','in_progress','waiting_on_school','resolved','closed')),
  assigned_to             uuid REFERENCES platform.operators(id),
  first_response_due_at   timestamptz NOT NULL,
  resolution_due_at       timestamptz NOT NULL,
  first_responded_at      timestamptz,
  resolved_at             timestamptz,
  closed_at               timestamptz,
  personal_data_flagged   boolean NOT NULL DEFAULT false,
  purge_after             date,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  version                 int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT support_tickets_opener CHECK ((opened_by_user_id IS NULL) <> (opened_by_operator_id IS NULL)),
  CONSTRAINT support_tickets_app_channel CHECK ((channel = 'app') = (opened_by_user_id IS NOT NULL)),
  CONSTRAINT support_tickets_closed CHECK ((status = 'closed') = (closed_at IS NOT NULL AND purge_after IS NOT NULL)),
  CONSTRAINT support_tickets_purge_after
    CHECK (purge_after IS NULL OR purge_after >= (closed_at AT TIME ZONE 'Asia/Kolkata')::date + 365)
);
CREATE INDEX tickets_queue ON platform.support_tickets (status, priority, resolution_due_at);
CREATE INDEX tickets_tenant ON platform.support_tickets (tenant_id, created_at DESC);

CREATE TABLE platform.support_messages (
  id             uuid PRIMARY KEY,
  ticket_id      uuid NOT NULL REFERENCES platform.support_tickets(id) ON DELETE CASCADE,
  author_type    text NOT NULL CHECK (author_type IN ('school_user','operator','system')),
  author_id      uuid,
  body           text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 5000),
  internal_note  boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT support_messages_internal CHECK (NOT internal_note OR author_type = 'operator'),
  CONSTRAINT support_messages_author CHECK (author_type = 'system' OR author_id IS NOT NULL)
);
CREATE INDEX support_messages_ticket ON platform.support_messages (ticket_id, created_at);

-- Break-glass requests as seen by the control plane (07 §6.4; workflow M1). Emergency access
-- without school approval needs two different operators holding platform.breakglass.emergency.
CREATE TABLE platform.breakglass_requests (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  requested_by          uuid NOT NULL REFERENCES platform.operators(id),
  reason_code           text NOT NULL CHECK (reason_code IN
                          ('support_request','security_incident','legal_obligation')),
  reason                text NOT NULL CHECK (char_length(reason) BETWEEN 10 AND 500),
  scope                 jsonb NOT NULL CHECK (jsonb_typeof(scope) = 'object'),
  duration_minutes      int NOT NULL CHECK (duration_minutes BETWEEN 15 AND 480),
  emergency             boolean NOT NULL DEFAULT false,
  status                text NOT NULL CHECK (status IN
                          ('requested','approved','active','expired','revoked','denied')),
  emergency_confirmed_by_1 uuid REFERENCES platform.operators(id),
  emergency_confirmed_by_2 uuid REFERENCES platform.operators(id),
  emergency_confirmed_at   timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT breakglass_emergency_only
    CHECK (emergency OR (emergency_confirmed_by_1 IS NULL AND emergency_confirmed_by_2 IS NULL)),
  CONSTRAINT breakglass_second_after_first
    CHECK (emergency_confirmed_by_2 IS NULL OR emergency_confirmed_by_1 IS NOT NULL),
  -- SEC-029: the two emergency confirmations come from different operators.
  CONSTRAINT breakglass_two_person
    CHECK (emergency_confirmed_by_2 IS NULL OR emergency_confirmed_by_2 <> emergency_confirmed_by_1),
  CONSTRAINT breakglass_confirmed_at
    CHECK ((emergency_confirmed_by_2 IS NULL) = (emergency_confirmed_at IS NULL))
);
CREATE INDEX breakglass_tenant ON platform.breakglass_requests (tenant_id, created_at DESC);

CREATE TABLE platform.job_runs (
  id               uuid PRIMARY KEY,
  task_name        text NOT NULL CHECK (task_name ~ '^[a-z_]+(\.[a-z_]+)+$'),
  idempotency_key  text NOT NULL UNIQUE CHECK (char_length(idempotency_key) BETWEEN 1 AND 200),
  status           text NOT NULL CHECK (status IN ('pending','running','succeeded','failed','dead')),
  attempts         int NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  progress         jsonb,
  error            text CHECK (char_length(error) <= 500),
  created_by       uuid REFERENCES platform.operators(id),
  started_at       timestamptz,
  finished_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT job_runs_finished_after_start CHECK (finished_at IS NULL OR started_at IS NOT NULL)
);
"""

TRIGGERS_SQL = r"""
CREATE FUNCTION platform.tg_set_updated_at() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  NEW.updated_at := pg_catalog.now();
  RETURN NEW;
END
$$;

-- FR-PLT-010: once published, a plan's prices and limits never change; only
-- published -> retired is allowed. Published or retired plans cannot be deleted.
CREATE FUNCTION platform.tg_plans_freeze() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'published plans cannot be deleted'
        USING ERRCODE = 'object_not_in_prerequisite_state', CONSTRAINT = 'plans_frozen';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status <> 'draft' THEN
    IF (pg_catalog.to_jsonb(NEW) - 'status' - 'updated_at')
         IS DISTINCT FROM (pg_catalog.to_jsonb(OLD) - 'status' - 'updated_at')
       OR NOT (NEW.status = OLD.status OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
      RAISE EXCEPTION 'published plans are immutable; create a new version'
        USING ERRCODE = 'object_not_in_prerequisite_state', CONSTRAINT = 'plans_frozen';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

-- FR-PLT-016: after issue only status, payments, void fields and bookkeeping may change.
CREATE FUNCTION platform.tg_invoices_freeze() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'only draft invoices can be deleted'
        USING ERRCODE = 'object_not_in_prerequisite_state', CONSTRAINT = 'invoices_frozen';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status <> 'draft' THEN
    IF (pg_catalog.to_jsonb(NEW) - ARRAY['status','amount_paid_inr','tds_inr','voided_by',
                                          'voided_at','void_reason','updated_at','version'])
         IS DISTINCT FROM
       (pg_catalog.to_jsonb(OLD) - ARRAY['status','amount_paid_inr','tds_inr','voided_by',
                                          'voided_at','void_reason','updated_at','version'])
       OR NOT (NEW.status = OLD.status
               OR (OLD.status, NEW.status) IN (('issued','paid'), ('paid','issued'),
                                               ('issued','void'))) THEN
      RAISE EXCEPTION 'issued invoices are immutable'
        USING ERRCODE = 'object_not_in_prerequisite_state', CONSTRAINT = 'invoices_frozen';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE FUNCTION platform.tg_invoice_lines_draft_only() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_invoice uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
BEGIN
  IF EXISTS (SELECT 1 FROM platform.invoices AS i WHERE i.id = v_invoice AND i.status <> 'draft') THEN
    RAISE EXCEPTION 'lines of an issued invoice cannot change'
      USING ERRCODE = 'object_not_in_prerequisite_state', CONSTRAINT = 'invoices_frozen';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION platform.tg_set_updated_at(), platform.tg_plans_freeze(),
  platform.tg_invoices_freeze(), platform.tg_invoice_lines_draft_only() FROM PUBLIC;

CREATE TRIGGER plans_freeze BEFORE UPDATE OR DELETE ON platform.plans
  FOR EACH ROW EXECUTE FUNCTION platform.tg_plans_freeze();
CREATE TRIGGER invoices_freeze BEFORE UPDATE OR DELETE ON platform.invoices
  FOR EACH ROW EXECUTE FUNCTION platform.tg_invoices_freeze();
CREATE TRIGGER invoice_lines_draft_only BEFORE INSERT OR UPDATE OR DELETE ON platform.invoice_lines
  FOR EACH ROW EXECUTE FUNCTION platform.tg_invoice_lines_draft_only();
"""

UPDATED_AT_TABLES = (
    "operators",
    "plans",
    "billing_accounts",
    "deployments",
    "subscriptions",
    "invoices",
    "announcements",
    "support_tickets",
    "breakglass_requests",
    "job_runs",
    "feature_flags",
    "invoice_sequences",
)

GRANTS_SQL = """
-- Default privileges already gave sos_platform DML on every new platform table.
-- The school app may read feature flags, and nothing else in this schema (ADR-0013).
GRANT SELECT ON platform.feature_flags TO sos_app;
-- core.current_subscription() (sos_definer) reads exactly these tables.
GRANT SELECT ON platform.plans, platform.subscriptions, platform.invoices, platform.usage_daily
  TO sos_definer;
-- core.create_owner_invite() (sos_definer) writes the first membership of a new school.
GRANT INSERT ON core.memberships, core.membership_scopes, core.membership_roles TO sos_definer;
GRANT SELECT ON core.roles TO sos_definer;
"""

# Tables core.create_owner_invite() must reach across tenants (allowlisted in
# apps/api/tests/security/rls_allowlist.yaml). Created only if absent, because the authz
# migration may add the same allowlisted policy.
_POLICY_MARK = "0005_platform"
INVITE_DEFINER_TABLES = ("core.roles", "core.membership_roles", "core.membership_scopes")

_CREATE_POLICY_DO = """
            DO $do$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = '__SCHEMA__'
                             AND tablename = '__NAME__' AND policyname = 'definer_access') THEN
                CREATE POLICY definer_access ON __TABLE__ AS PERMISSIVE FOR ALL TO PUBLIC
                  USING (current_user = 'sos_definer') WITH CHECK (current_user = 'sos_definer');
                COMMENT ON POLICY definer_access ON __TABLE__ IS '__MARK__';
              END IF;
            END
            $do$
            """

_DROP_POLICY_DO = """
            DO $do$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
                         JOIN pg_namespace n ON n.oid = c.relnamespace
                         WHERE n.nspname = '__SCHEMA__' AND c.relname = '__NAME__'
                           AND p.polname = 'definer_access'
                           AND obj_description(p.oid, 'pg_policy') = '__MARK__') THEN
                DROP POLICY definer_access ON __TABLE__;
              END IF;
            END
            $do$
            """


def _fill(template: str, table: str) -> str:
    """Substitute constant identifiers from INVITE_DEFINER_TABLES (never user input)."""
    schema, name = table.split(".", 1)
    return (
        template.replace("__SCHEMA__", schema)
        .replace("__NAME__", name)
        .replace("__TABLE__", table)
        .replace("__MARK__", _POLICY_MARK)
    )


DEFINER_FUNCTIONS_SQL = r"""
CREATE FUNCTION core.current_subscription() RETURNS jsonb
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT pg_catalog.jsonb_build_object(
      'subscription_id', s.id,
      'plan_code', p.code,
      'plan_version', p.version,
      'plan_name', p.name,
      'tier', p.tier,
      'billing_period', p.billing_period,
      'status', s.status,
      'current_period_start', s.current_period_start,
      'current_period_end', s.current_period_end,
      'trial_ends_at', s.trial_ends_at,
      'past_due_since', s.past_due_since,
      'grace_ends_on', s.grace_ends_on,
      'cancel_at_period_end', s.cancel_at_period_end,
      'limits', p.limits,
      'usage', (SELECT pg_catalog.jsonb_build_object(
                  'usage_date', u.usage_date, 'active_users', u.active_users,
                  'staff_users', u.staff_users, 'students_active', u.students_active,
                  'storage_bytes', u.storage_bytes, 'documents', u.documents,
                  'ai_queries', u.ai_queries,
                  'ai_tokens', u.ai_input_tokens + u.ai_output_tokens,
                  'ai_cost_inr', u.ai_cost_inr)
                FROM platform.usage_daily AS u
                WHERE u.tenant_id = s.tenant_id
                ORDER BY u.usage_date DESC LIMIT 1),
      'invoices', COALESCE((SELECT pg_catalog.jsonb_agg(inv ORDER BY inv->>'period_start' DESC)
                  FROM (SELECT pg_catalog.jsonb_build_object(
                          'invoice_id', i.id, 'invoice_number', i.invoice_number,
                          'period_start', i.period_start, 'period_end', i.period_end,
                          'issue_date', i.issue_date, 'due_date', i.due_date,
                          'total_inr', i.total_inr, 'status', i.status,
                          'amount_due_inr', CASE WHEN i.status = 'issued'
                              THEN GREATEST(i.total_inr - i.amount_paid_inr - i.tds_inr, 0)
                              ELSE 0 END) AS inv
                        FROM platform.invoices AS i
                        WHERE i.tenant_id = s.tenant_id AND i.status <> 'draft'
                        ORDER BY i.period_start DESC LIMIT 24) AS x), '[]'::jsonb))
    FROM platform.subscriptions AS s
    JOIN platform.plans AS p ON p.id = s.plan_id
    WHERE s.tenant_id = core.current_tenant()
    ORDER BY (s.status = 'cancelled'), s.created_at DESC
    LIMIT 1
$$;

CREATE FUNCTION core.create_owner_invite(
    p_tenant uuid, p_subject text, p_display_name text, p_email public.citext, p_language text)
  RETURNS TABLE (user_id uuid, membership_id uuid, owner_role_assigned boolean)
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_status     text;
  v_user       uuid;
  v_user_state text;
  v_membership uuid := __UUID7__;
  v_role       uuid;
BEGIN
  IF p_tenant IS NULL OR p_subject IS NULL OR p_display_name IS NULL THEN
    RAISE EXCEPTION 'tenant, subject and display name are required'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  SELECT t.status INTO v_status FROM core.tenants AS t WHERE t.id = p_tenant FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'tenant not found' USING ERRCODE = 'no_data_found';
  END IF;
  IF v_status <> 'provisioning' THEN
    RAISE EXCEPTION 'owner invites are only created while a school is being provisioned'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  IF EXISTS (SELECT 1 FROM core.memberships AS m WHERE m.tenant_id = p_tenant) THEN
    RAISE EXCEPTION 'the school already has members'
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  INSERT INTO core.users AS u (id, idp_subject, display_name, email, preferred_language, status)
  VALUES (__UUID7__, p_subject, p_display_name, p_email, COALESCE(p_language, 'en'), 'active')
  ON CONFLICT (idp_subject) DO NOTHING
  RETURNING u.id INTO v_user;
  IF v_user IS NULL THEN
    SELECT u.id, u.status INTO v_user, v_user_state FROM core.users AS u
    WHERE u.idp_subject = p_subject;
    IF v_user_state <> 'active' THEN
      RAISE EXCEPTION 'this sign-in is disabled' USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  END IF;
  INSERT INTO core.memberships (id, tenant_id, user_id, status, mfa_required)
  VALUES (v_membership, p_tenant, v_user, 'invited', true);
  INSERT INTO core.membership_scopes (id, tenant_id, membership_id, scope_type, scope_ref)
  VALUES (__UUID7__, p_tenant, v_membership, 'school', NULL);
  SELECT r.id INTO v_role FROM core.roles AS r WHERE r.tenant_id = p_tenant AND r.key = 'owner';
  IF FOUND THEN
    INSERT INTO core.membership_roles (tenant_id, membership_id, role_id)
    VALUES (p_tenant, v_membership, v_role);
  END IF;
  RETURN QUERY SELECT v_user, v_membership, (v_role IS NOT NULL);
END
$$;
""".replace("__UUID7__", _UUID7)

DEFINER_FUNCTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("core.current_subscription()", ("sos_app",)),
    ("core.create_owner_invite(uuid, text, text, public.citext, text)", ("sos_platform",)),
)

DROP_ORDER = (
    "platform.job_runs",
    "platform.breakglass_requests",
    "platform.support_messages",
    "platform.support_tickets",
    "platform.announcements",
    "platform.feature_flags",
    "platform.usage_threshold_events",
    "platform.usage_daily",
    "platform.payments",
    "platform.invoice_lines",
    "platform.invoices",
    "platform.invoice_sequences",
    "platform.subscriptions",
    "platform.deployments",
    "platform.billing_accounts",
    "platform.plans",
    "platform.operator_roles",
    "platform.operators",
)


def upgrade() -> None:
    op.execute(TABLES_SQL)
    op.execute(TRIGGERS_SQL)
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON platform.{table} "
            "FOR EACH ROW EXECUTE FUNCTION platform.tg_set_updated_at()"
        )
    op.execute(GRANTS_SQL)
    for table in INVITE_DEFINER_TABLES:
        op.execute(_fill(_CREATE_POLICY_DO, table))

    op.execute("GRANT CREATE ON SCHEMA core TO sos_definer")
    op.execute("SET ROLE sos_definer")
    op.execute(DEFINER_FUNCTIONS_SQL)
    for signature, roles in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {', '.join(roles)}")
    op.execute("SET ROLE sos_owner")
    op.execute("REVOKE CREATE ON SCHEMA core FROM sos_definer")


def downgrade() -> None:
    for signature, _roles in reversed(DEFINER_FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    # Drop only the policies this revision created (marked by comment); an identical policy
    # created by an earlier revision (e.g. 0004_authz_seed) stays in place.
    for table in INVITE_DEFINER_TABLES:
        op.execute(_fill(_DROP_POLICY_DO, table))
    op.execute("REVOKE SELECT ON core.roles FROM sos_definer")
    op.execute(
        "REVOKE INSERT ON core.memberships, core.membership_scopes, core.membership_roles "
        "FROM sos_definer"
    )
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute(
        "DROP FUNCTION IF EXISTS platform.tg_invoice_lines_draft_only(), "
        "platform.tg_invoices_freeze(), platform.tg_plans_freeze(), platform.tg_set_updated_at()"
    )

"""Data-quality runs and findings (US-501, US-502; FR-DQ-002, FR-DQ-004, FR-DQ-006, FR-DQ-020;
docs/05 §5).

- ``sis.dq_runs``: one row per check run, manual (``POST /dq/runs``) or incremental (outbox
  event). ``scope`` is the effective scope (sections, classes, students or an import batch, already
  limited to the requester's reach); ``stats`` holds counts only.
- ``sis.dq_findings``: one row per (student, rule, attribute, sources[, profile][, pair]) =
  ``fingerprint`` (UNIQUE per school), so re-runs update the same row (idempotent) and reopen it
  when a resolved finding's conflict returns (FR-DQ-004). ``details`` and
  ``explanation_params`` hold masked values, value ids, codes and counts only (FR-DQ-006).
  Workflow: ``open`` -> ``resolved`` (note or change request; ``auto_cleared`` when a re-run no
  longer finds the conflict) | ``waived`` (reason) -> ``reopened``.
- ``change_request_id`` is a plain uuid until ``sis.change_requests`` exists (0014 adds the
  composite FK).
- Tenant-owned: RLS ENABLE + FORCE with ``tenant_isolation``; composite FKs to students, runs and
  memberships (ADR-0013 §7). Findings and runs are records: the app may not delete them.

Requirements: FR-DQ-002, FR-DQ-004, FR-DQ-006, FR-DQ-020, SEC-001.

Revision ID: 0013_dq
Revises: 0011_breakglass
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0013_dq"
down_revision = "0011_breakglass"
branch_labels = None
depends_on = None

SEVERITIES = "'blocker','high','medium','low','info'"
MATCH_CLASSES = "'EXACT','ORDER','SPACING','INITIALS','VARIANT','TYPO','DIFFERENT','MISSING'"

TABLES_SQL = rf"""
CREATE TABLE sis.dq_runs (
  id                       uuid PRIMARY KEY,
  tenant_id                uuid NOT NULL REFERENCES core.tenants (id),
  trigger                  text NOT NULL CHECK (trigger IN ('manual','event')),
  event_type               text CHECK (event_type ~ '^[a-z_]+(\.[a-z_]+)+$'),
  scope                    jsonb NOT NULL DEFAULT '{{}}'::jsonb
                             CHECK (jsonb_typeof(scope) = 'object'),
  profile_key              text CHECK (profile_key ~ '^[a-z0-9][a-z0-9-]{{0,63}}$'),
  status                   text NOT NULL
                             CHECK (status IN ('queued','running','completed','failed')),
  started_by               uuid,
  requested_by_membership  uuid,
  created_at               timestamptz NOT NULL DEFAULT now(),
  started_at               timestamptz,
  finished_at              timestamptz,
  stats                    jsonb CHECK (stats IS NULL OR jsonb_typeof(stats) = 'object'),
  error_code               text CHECK (char_length(error_code) <= 100),
  CONSTRAINT dq_runs_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT dq_runs_requested_by_fk FOREIGN KEY (tenant_id, requested_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT dq_runs_manual_has_requester
    CHECK (trigger <> 'manual' OR (started_by IS NOT NULL AND requested_by_membership IS NOT NULL)),
  CONSTRAINT dq_runs_event_has_type CHECK ((trigger = 'event') = (event_type IS NOT NULL)),
  CONSTRAINT dq_runs_finished_when_done
    CHECK ((status IN ('completed','failed')) = (finished_at IS NOT NULL))
);
CREATE INDEX dq_runs_tenant_created ON sis.dq_runs (tenant_id, created_at DESC, id DESC);

CREATE TABLE sis.dq_findings (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL REFERENCES core.tenants (id),
  fingerprint          text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
  student_id           uuid NOT NULL,
  related_student_id   uuid,
  rule_id              text NOT NULL CHECK (rule_id ~ '^DQ-[0-9]{{3}}$'),
  rule_version         int NOT NULL CHECK (rule_version >= 1),
  profile_key          text CHECK (profile_key ~ '^[a-z0-9][a-z0-9-]{{0,63}}$'),
  attribute_key        text CHECK (attribute_key ~ '^[a-z][a-z0-9_]*$'),
  sources              text[] NOT NULL DEFAULT '{{}}',
  match_class          text CHECK (match_class IN ({MATCH_CLASSES})),
  severity             text NOT NULL CHECK (severity IN ({SEVERITIES})),
  status               text NOT NULL CHECK (status IN ('open','resolved','waived','reopened')),
  explanation_code     text NOT NULL CHECK (explanation_code ~ '^DQ-[0-9]{{3}}(-[A-Z]+)?$'),
  explanation_params   jsonb NOT NULL DEFAULT '{{}}'::jsonb
                         CHECK (jsonb_typeof(explanation_params) = 'object'),
  route_codes          text[] NOT NULL CHECK (cardinality(route_codes) >= 1),
  details              jsonb NOT NULL DEFAULT '{{}}'::jsonb
                         CHECK (jsonb_typeof(details) = 'object'),
  conflict_hash        text NOT NULL CHECK (conflict_hash ~ '^[0-9a-f]{{64}}$'),
  first_seen_run_id    uuid,
  last_seen_run_id     uuid,
  first_seen_at        timestamptz NOT NULL DEFAULT now(),
  last_seen_at         timestamptz NOT NULL DEFAULT now(),
  resolution           text CHECK (resolution IN ('note','change_request','auto_cleared')),
  resolution_note      text CHECK (char_length(resolution_note) BETWEEN 1 AND 1000),
  change_request_id    uuid,
  resolved_by          uuid,
  resolved_at          timestamptz,
  waived_by            uuid,
  waived_at            timestamptz,
  waived_reason        text CHECK (char_length(waived_reason) BETWEEN 1 AND 1000),
  reopened_count       int NOT NULL DEFAULT 0 CHECK (reopened_count >= 0),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  version              int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT dq_findings_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT dq_findings_fingerprint_key UNIQUE (tenant_id, fingerprint),
  CONSTRAINT dq_findings_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT dq_findings_related_student_fk FOREIGN KEY (tenant_id, related_student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT dq_findings_first_run_fk FOREIGN KEY (tenant_id, first_seen_run_id)
    REFERENCES sis.dq_runs (tenant_id, id),
  CONSTRAINT dq_findings_last_run_fk FOREIGN KEY (tenant_id, last_seen_run_id)
    REFERENCES sis.dq_runs (tenant_id, id),
  -- US-502 AC1: resolving needs a note (by a person) or a change request (by a person, or by
  -- the engine when an approved request removed the conflict); auto_cleared is the engine's.
  CONSTRAINT dq_findings_resolved_complete CHECK (
    status <> 'resolved' OR (
      resolved_at IS NOT NULL AND resolution IS NOT NULL
      AND (resolution <> 'note' OR (resolution_note IS NOT NULL AND resolved_by IS NOT NULL))
      AND (resolution <> 'change_request' OR change_request_id IS NOT NULL))),
  -- US-502 AC1: waiving needs a reason and who waived it.
  CONSTRAINT dq_findings_waived_complete CHECK (
    status <> 'waived' OR (waived_by IS NOT NULL AND waived_at IS NOT NULL
                           AND waived_reason IS NOT NULL)),
  CONSTRAINT dq_findings_pair_only_duplicates
    CHECK (related_student_id IS NULL OR rule_id = 'DQ-008'),
  CONSTRAINT dq_findings_not_self_pair CHECK (related_student_id IS DISTINCT FROM student_id)
);
-- Pre-check lists and summary counts (US-501 AC1/AC2).
CREATE INDEX dq_findings_status_severity ON sis.dq_findings (tenant_id, status, severity);
-- Findings of a student (profile, change requests, re-runs).
CREATE INDEX dq_findings_student ON sis.dq_findings (tenant_id, student_id);
CREATE INDEX dq_findings_related_student ON sis.dq_findings (tenant_id, related_student_id)
  WHERE related_student_id IS NOT NULL;
CREATE INDEX dq_findings_change_request ON sis.dq_findings (tenant_id, change_request_id)
  WHERE change_request_id IS NOT NULL;
"""


def upgrade() -> None:
    op.execute(TABLES_SQL)
    for table in ("sis.dq_runs", "sis.dq_findings"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        # Default privileges gave sos_app full DML; runs and findings are never deleted.
        op.execute(f"REVOKE DELETE, TRUNCATE ON {table} FROM sos_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sis.dq_findings")
    op.execute("DROP TABLE IF EXISTS sis.dq_runs")

"""Ask the school: conversations, answer details, rolling summaries and per-user memory.

docs/06 §5 (conversations, context, memory), docs/05 §6.4, ADR-0034; FR-KB-008, FR-KB-009,
FR-KB-012 (as amended by ADR-0034).

New tables (tenant-owned: RLS ENABLE + FORCE with ``tenant_isolation``, ``UNIQUE (tenant_id,
id)`` where referenced, composite FKs only, the ADR-0029 ``offboarding_purge`` policy and
``sos_purger`` SELECT, DELETE; no ``definer_access`` policy, no ``SECURITY DEFINER`` function):

- ``kb.conversations``: one user's Ask conversation. ``id`` IS the ``session_id`` its questions
  carry. ``title_ciphertext`` (tenant DEK, AAD ``tenant|kb.conversations|title_ciphertext|id``;
  a title can hold a name) and the rolling summary ``summary_ciphertext`` (same key; the
  created_at of the oldest and newest message it covers, ``summary_sources``: the source URIs
  cited by the answers it summarises, ids only, re-checked for visibility before every use).
  ``deleted_at``: removed from the user's history; the title and summary are cleared at once
  (CHECK), the questions stay in the query log until the 180-day purge. ``updated_at`` is set by
  the service (new message, rename, pin), never by a trigger, so a DEK re-encryption does not
  reorder anyone's history. ``version`` changes with the title, pin or deletion (ETag).
- ``kb.user_memories``: one user's memory items in one school (ADR-0034): ``text_ciphertext``
  (tenant DEK), ``source`` ``explicit|suggested``, ``status`` ``active|pending`` (a pending
  suggestion always has ``expires_at``; only suggestions are ever pending). FK to the
  membership ``(tenant_id, user_id)`` ON DELETE CASCADE; the conversation and question it came
  from are kept only as long as they exist (ON DELETE SET NULL of that column).
- ``kb.user_memory_settings``: the user's memory switch (no row = on), FK to the membership.

``kb.queries`` gains: ``conversation_id`` (composite FK; equals ``session_id`` when set; NULL for
questions asked before this revision, adopted later by the service), ``superseded_by`` (the
question that replaced it by a regenerate or an edit; composite self-FK, ON DELETE SET NULL of
that column), ``revises`` + ``revision`` (``regenerate|edit``: which question this one replaces),
``citations_ciphertext`` (the numbered citations with title and snippet, encrypted: they quote
records and documents), ``followups_ciphertext`` (the suggested follow-up questions) and
``summarized`` (the question went to the model with the conversation's rolling summary of older
turns; shown as "earlier messages summarised"). ``route``
also allows ``memory`` (a "remember that ..." instruction answered without the model).

``kb.llm_calls.role`` also allows ``followups``, ``summary``, ``memory_screen`` and
``query_rewrite`` (the cheap metered gateway calls of Ask: follow-up suggestions with an optional
memory suggestion, the rolling summary, the memory safety screen and the standalone rewrite of a
follow-up question).

``kb.queries`` also gains ``access_fingerprint`` (SHA-256 of the asker's document-visibility keys:
roles, sections, classes, school-wide and C3 flags; no person) and ``cached_from`` (the answer an
exact repeat reused; composite self-FK, ON DELETE SET NULL of that column) and
``cache_invalidated_at`` (set when a cited document gets a new version, an ACL change, is
archived or deleted), for the documents-only answer cache (docs/06 cost and performance).

Grants: the ``kb`` default privileges give ``sos_app`` DML and ``sos_readonly`` SELECT; reporting
has no use for memories, so ``sos_readonly`` gets nothing on the two memory tables.
``sos_platform`` has nothing on any of them.

No data migration: titles must be encrypted with the school's key, which only the application
holds, and the migrator cannot read rows under FORCE RLS. Conversations for earlier questions
are created by the service (``knowledge.adopt_conversations``, run with the daily query purge).

Expand-only (invariant 12). Downgrade drops the three tables and the new columns (lossy:
conversation titles, summaries, memories, citation details and follow-ups; the query log
itself is kept), and restores the 0034 metering checks and the 0021 route check as NOT VALID,
keeping rows already written with a new value.

Revision ID: 0038_ask_conversations
Revises: 0037_notice_drafting
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op

revision = "0038_ask_conversations"
down_revision = "0037_notice_drafting"
branch_labels = None
depends_on = None

OLD_ROLES = (
    "'answer','router','metadata','translation','extraction','eval_judge','circular','notice'"
)
NEW_ROLES = OLD_ROLES + ",'followups','summary','memory_screen','query_rewrite'"
OLD_ROUTES = "'tools','documents','both','refused'"
NEW_ROUTES = OLD_ROUTES + ",'memory'"

TABLES_SQL = r"""
CREATE TABLE kb.conversations (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL REFERENCES core.tenants (id),
  user_id            uuid NOT NULL,
  -- AES-256-GCM under the tenant DEK (docs/05 §9); a title can hold a name.
  title_ciphertext   bytea CHECK (octet_length(title_ciphertext) > 0),
  key_version        int NOT NULL CHECK (key_version >= 1),
  pinned             boolean NOT NULL DEFAULT false,
  -- Rolling summary of older turns (docs/06 §5): encrypted, with what it covers.
  summary_ciphertext bytea CHECK (octet_length(summary_ciphertext) > 0),
  summary_oldest_at  timestamptz,
  summary_through    timestamptz,
  -- Source URIs (ids only) cited by the summarised answers: re-checked before each use.
  summary_sources    jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(summary_sources) = 'array'),
  deleted_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  version            int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT conversations_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT conversations_title_present
    CHECK (deleted_at IS NOT NULL OR title_ciphertext IS NOT NULL),
  CONSTRAINT conversations_deleted_forgotten
    CHECK (deleted_at IS NULL OR (title_ciphertext IS NULL AND summary_ciphertext IS NULL)),
  CONSTRAINT conversations_summary_complete CHECK (
    (summary_ciphertext IS NULL AND summary_oldest_at IS NULL AND summary_through IS NULL)
    OR (summary_ciphertext IS NOT NULL AND summary_oldest_at IS NOT NULL
        AND summary_through IS NOT NULL AND summary_oldest_at <= summary_through))
);
CREATE INDEX conversations_user ON kb.conversations
  (tenant_id, user_id, pinned DESC, updated_at DESC, id DESC) WHERE deleted_at IS NULL;

ALTER TABLE kb.queries
  ADD COLUMN conversation_id      uuid,
  ADD COLUMN superseded_by        uuid,
  ADD COLUMN revises              uuid,
  ADD COLUMN revision             text CHECK (revision IN ('regenerate','edit')),
  ADD COLUMN citations_ciphertext bytea CHECK (octet_length(citations_ciphertext) > 0),
  ADD COLUMN followups_ciphertext bytea CHECK (octet_length(followups_ciphertext) > 0),
  ADD COLUMN access_fingerprint   bytea CHECK (octet_length(access_fingerprint) = 32),
  ADD COLUMN cached_from          uuid,
  ADD COLUMN cache_invalidated_at timestamptz,
  ADD COLUMN summarized           boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT queries_conversation_fk FOREIGN KEY (tenant_id, conversation_id)
    REFERENCES kb.conversations (tenant_id, id),
  ADD CONSTRAINT queries_superseded_by_fk FOREIGN KEY (tenant_id, superseded_by)
    REFERENCES kb.queries (tenant_id, id) ON DELETE SET NULL (superseded_by),
  ADD CONSTRAINT queries_revises_fk FOREIGN KEY (tenant_id, revises)
    REFERENCES kb.queries (tenant_id, id) ON DELETE SET NULL (revises),
  ADD CONSTRAINT queries_cached_from_fk FOREIGN KEY (tenant_id, cached_from)
    REFERENCES kb.queries (tenant_id, id) ON DELETE SET NULL (cached_from),
  ADD CONSTRAINT queries_conversation_is_session
    CHECK (conversation_id IS NULL OR conversation_id = session_id),
  ADD CONSTRAINT queries_revision_complete CHECK (revises IS NULL OR revision IS NOT NULL),
  ADD CONSTRAINT queries_revision_in_conversation
    CHECK (revision IS NULL OR conversation_id IS NOT NULL),
  ADD CONSTRAINT queries_not_superseded_by_itself CHECK (superseded_by IS DISTINCT FROM id);
CREATE INDEX queries_conversation ON kb.queries (tenant_id, conversation_id, created_at)
  WHERE conversation_id IS NOT NULL;
-- Exact repeats (answer cache): same question HMAC and access fingerprint, newest first.
CREATE INDEX queries_answer_cache ON kb.queries
  (tenant_id, question_hmac, access_fingerprint, created_at DESC)
  WHERE access_fingerprint IS NOT NULL AND cache_invalidated_at IS NULL;

CREATE TABLE kb.user_memories (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL REFERENCES core.tenants (id),
  user_id          uuid NOT NULL,
  -- The user's own preference or work context, AES-256-GCM under the tenant DEK (ADR-0034).
  text_ciphertext  bytea NOT NULL CHECK (octet_length(text_ciphertext) > 0),
  key_version      int NOT NULL CHECK (key_version >= 1),
  source           text NOT NULL CHECK (source IN ('explicit','suggested')),
  status           text NOT NULL CHECK (status IN ('active','pending')),
  conversation_id  uuid,
  query_id         uuid,
  expires_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  version          int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT user_memories_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT user_memories_member_fk FOREIGN KEY (tenant_id, user_id)
    REFERENCES core.memberships (tenant_id, user_id) ON DELETE CASCADE,
  CONSTRAINT user_memories_conversation_fk FOREIGN KEY (tenant_id, conversation_id)
    REFERENCES kb.conversations (tenant_id, id) ON DELETE SET NULL (conversation_id),
  CONSTRAINT user_memories_query_fk FOREIGN KEY (tenant_id, query_id)
    REFERENCES kb.queries (tenant_id, id) ON DELETE SET NULL (query_id),
  -- Only suggestions wait for the user's confirmation, and a pending one always expires.
  CONSTRAINT user_memories_pending_expires CHECK ((status = 'pending') = (expires_at IS NOT NULL)),
  CONSTRAINT user_memories_pending_suggested CHECK (status = 'active' OR source = 'suggested')
);
CREATE INDEX user_memories_user ON kb.user_memories (tenant_id, user_id, created_at DESC);
CREATE INDEX user_memories_expiry ON kb.user_memories (tenant_id, expires_at)
  WHERE expires_at IS NOT NULL;

CREATE TABLE kb.user_memory_settings (
  tenant_id   uuid NOT NULL REFERENCES core.tenants (id),
  user_id     uuid NOT NULL,
  enabled     boolean NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id),
  CONSTRAINT user_memory_settings_member_fk FOREIGN KEY (tenant_id, user_id)
    REFERENCES core.memberships (tenant_id, user_id) ON DELETE CASCADE
);

-- Reporting has no use for a person's memory items or their switch.
REVOKE ALL ON kb.user_memories FROM sos_readonly;
REVOKE ALL ON kb.user_memory_settings FROM sos_readonly;
"""

TENANT_TABLES = ("kb.conversations", "kb.user_memories", "kb.user_memory_settings")


def _checks(roles: str, routes: str, *, not_valid: bool) -> None:
    suffix = " NOT VALID" if not_valid else ""
    op.execute("ALTER TABLE kb.llm_calls DROP CONSTRAINT llm_calls_role_check")
    op.execute(
        f"ALTER TABLE kb.llm_calls ADD CONSTRAINT llm_calls_role_check CHECK (role IN ({roles})){suffix}"
    )
    op.execute("ALTER TABLE kb.queries DROP CONSTRAINT queries_route_check")
    op.execute(
        f"ALTER TABLE kb.queries ADD CONSTRAINT queries_route_check CHECK (route IN ({routes})){suffix}"
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
    _checks(NEW_ROLES, NEW_ROUTES, not_valid=False)


def downgrade() -> None:
    _checks(OLD_ROLES, OLD_ROUTES, not_valid=True)
    op.execute("DROP TABLE IF EXISTS kb.user_memory_settings")
    op.execute("DROP TABLE IF EXISTS kb.user_memories")
    op.execute("DROP INDEX IF EXISTS kb.queries_answer_cache")
    op.execute("DROP INDEX IF EXISTS kb.queries_conversation")
    op.execute(
        "ALTER TABLE kb.queries "
        "DROP CONSTRAINT IF EXISTS queries_not_superseded_by_itself, "
        "DROP CONSTRAINT IF EXISTS queries_revision_in_conversation, "
        "DROP CONSTRAINT IF EXISTS queries_revision_complete, "
        "DROP CONSTRAINT IF EXISTS queries_conversation_is_session, "
        "DROP CONSTRAINT IF EXISTS queries_cached_from_fk, "
        "DROP CONSTRAINT IF EXISTS queries_revises_fk, "
        "DROP CONSTRAINT IF EXISTS queries_superseded_by_fk, "
        "DROP CONSTRAINT IF EXISTS queries_conversation_fk, "
        "DROP COLUMN IF EXISTS summarized, "
        "DROP COLUMN IF EXISTS cache_invalidated_at, "
        "DROP COLUMN IF EXISTS cached_from, "
        "DROP COLUMN IF EXISTS access_fingerprint, "
        "DROP COLUMN IF EXISTS followups_ciphertext, "
        "DROP COLUMN IF EXISTS citations_ciphertext, "
        "DROP COLUMN IF EXISTS revision, "
        "DROP COLUMN IF EXISTS revises, "
        "DROP COLUMN IF EXISTS superseded_by, "
        "DROP COLUMN IF EXISTS conversation_id"
    )
    op.execute("DROP TABLE IF EXISTS kb.conversations")

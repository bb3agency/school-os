"""Synthetic rows in EVERY tenant table, for the offboarding deletion tests (FR-PLT-005).

``populate_school(admin_engine, tenant_id)`` writes at least one row into each table of the
tenant schemas for one school (as the test superuser, setup only), including the ones the app
role cannot delete in normal operation (append-only and frozen tables). The deletion tests
(ADR-0029; ``tests/tenancy/test_offboarding_purge.py``) prove the purge leaves
none of them, whatever new table a migration adds, and assert ``tables_without_rows`` lists
only the retained audit tables, so this helper grows with the schema. Checked on 2026-09-29
against ``0031_admin``: every tenant table except ``audit.events`` gets a row.

Synthetic values only (CLAUDE.md §6.11): names like "Synthetic Student", no real identifiers.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, text

TENANT_SCHEMAS = ("core", "sis", "kb", "audit", "ops")


@dataclass(frozen=True, slots=True)
class School:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    shared_user_id: uuid.UUID
    membership_id: uuid.UUID
    other_membership_id: uuid.UUID
    student_id: uuid.UUID
    document_id: uuid.UUID


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _run(conn: Connection, sql: str, **params: object) -> None:
    conn.execute(text(sql), params)


def populate_school(  # noqa: PLR0915 - one statement per table reads best as one list
    admin_engine: Engine,
    tenant_id: uuid.UUID,
    *,
    status: str = "active",
    shared_user_id: uuid.UUID | None = None,
    existing: bool = False,
) -> School:
    """Create the school (status ``status``) and one row in every tenant table.

    ``shared_user_id``: an existing user (a member of another school) who also gets a membership
    here, so the tests can check a shared profile is left alone. ``existing``: the school (and
    its key) already exists, e.g. provisioned through the control plane; only rows are added.
    """
    u = {k: uuid.uuid4() for k in ("user", "user2")}
    ids = {
        k: uuid.uuid4()
        for k in (
            "m1",
            "m2",
            "m3",
            "role",
            "year1",
            "year2",
            "class",
            "section",
            "student",
            "student2",
            "guardian",
            "enrol",
            "doc",
            "ver",
            "chunk",
            "batch",
            "row",
            "edit",
            "tpl",
            "value",
            "cr",
            "dqrun",
            "finding",
            "xbatch",
            "xpage",
            "xitem",
            "prun",
            "export",
            "efile",
            "job",
            "note",
            "bg",
            "query",
            "answer",
            "llm",
            "intent",
            "outbox",
            "scope",
            "attrdef",
        )
    }
    t = tenant_id
    with admin_engine.begin() as c:
        if existing:
            _run(
                c,
                "UPDATE core.tenants SET settings = settings || "
                '\'{"address_line1": "Synthetic Road 1"}\' WHERE id = :t',
                t=t,
            )
        else:
            _run(
                c,
                "INSERT INTO core.tenants (id, code, name, status, settings) "
                "VALUES (:t, :code, 'Synthetic Offboarding School', :st, "
                '\'{"address_line1": "Synthetic Road 1"}\')',
                t=t,
                code=f"o-{uuid.uuid4().hex[:12]}",
                st=status,
            )
        _run(
            c,
            "INSERT INTO core.tenant_keys (tenant_id, key_version, wrapped_dek, wrapped_hmac, "
            "kms_key_arn) VALUES (:t, 1, '\\x01', '\\x02', 'local-dev') "
            "ON CONFLICT DO NOTHING",
            t=t,
        )
        for key, name in (("user", "Synthetic Clerk"), ("user2", "Synthetic Teacher")):
            _run(
                c,
                "INSERT INTO core.users (id, idp_subject, display_name, email, phone_ciphertext) "
                "VALUES (:u, :s, :n, :e, '\\x0102')",
                u=u[key],
                s=f"sub-{uuid.uuid4().hex}",
                n=name,
                e=f"{key}-{uuid.uuid4().hex[:8]}@synthetic.test",
            )
        _run(
            c,
            "INSERT INTO core.memberships (id, tenant_id, user_id, status) VALUES "
            "(:m1, :t, :u1, 'active'), (:m2, :t, :u2, 'active')",
            m1=ids["m1"],
            m2=ids["m2"],
            t=t,
            u1=u["user"],
            u2=u["user2"],
        )
        if shared_user_id is not None:
            _run(
                c,
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (:m, :t, :u, 'active')",
                m=ids["m3"],
                t=t,
                u=shared_user_id,
            )
        _run(
            c,
            "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te) "
            "VALUES (:r, :t, 'synthetic_role', 'Synthetic role', 'Synthetic role te')",
            r=ids["role"],
            t=t,
        )
        _run(
            c,
            "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
            "SELECT :t, :r, key FROM core.permissions WHERE key NOT LIKE 'platform.%' LIMIT 1",
            t=t,
            r=ids["role"],
        )
        _run(
            c,
            "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
            "VALUES (:t, :m, :r)",
            t=t,
            m=ids["m1"],
            r=ids["role"],
        )
        _run(
            c,
            "INSERT INTO core.academic_years (id, tenant_id, label, starts_on, ends_on) VALUES "
            "(:y1, :t, '2025-26', '2025-06-01', '2026-04-30'), "
            "(:y2, :t, '2026-27', '2026-06-01', '2027-04-30')",
            y1=ids["year1"],
            y2=ids["year2"],
            t=t,
        )
        _run(
            c,
            "INSERT INTO core.classes (id, tenant_id, code, display_en, display_te, sort_order) "
            "VALUES (:c, :t, 'C5', 'Class 5', 'Class 5 te', 5)",
            c=ids["class"],
            t=t,
        )
        _run(
            c,
            "INSERT INTO core.sections (id, tenant_id, class_id, academic_year_id, name, "
            "class_teacher_membership_id) VALUES (:s, :t, :c, :y, 'A', :m)",
            s=ids["section"],
            t=t,
            c=ids["class"],
            y=ids["year1"],
            m=ids["m2"],
        )
        _run(
            c,
            "INSERT INTO core.membership_scopes (id, tenant_id, membership_id, scope_type, "
            "scope_ref) VALUES (:i, :t, :m, 'section', :s)",
            i=ids["scope"],
            t=t,
            m=ids["m2"],
            s=ids["section"],
        )
        # --- students (sis) -----------------------------------------------------------------
        _run(
            c,
            "INSERT INTO sis.attribute_definitions (id, tenant_id, key, data_type, "
            "classification, canonical_policy, label_en, label_te) VALUES "
            "(:i, :t, :k, 'text', 'C1', '{}', 'House', 'House te')",
            i=ids["attrdef"],
            t=t,
            k=f"house_{uuid.uuid4().hex[:6]}",
        )
        for sid, adm in ((ids["student"], "S-1"), (ids["student2"], "S-2")):
            _run(
                c,
                "INSERT INTO sis.students (id, tenant_id, admission_no, status) "
                "VALUES (:s, :t, :a, 'active')",
                s=sid,
                t=t,
                a=adm,
            )
            _run(
                c,
                "INSERT INTO sis.student_profiles (tenant_id, student_id) VALUES (:t, :s)",
                t=t,
                s=sid,
            )
        _run(
            c,
            "INSERT INTO sis.guardians (id, tenant_id, full_name) "
            "VALUES (:g, :t, 'Synthetic Guardian')",
            g=ids["guardian"],
            t=t,
        )
        _run(
            c,
            "INSERT INTO sis.student_guardians (tenant_id, student_id, guardian_id, relationship) "
            "VALUES (:t, :s, :g, 'mother')",
            t=t,
            s=ids["student"],
            g=ids["guardian"],
        )
        _run(
            c,
            "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, "
            "academic_year_id, status) VALUES (:e, :t, :s, :sec, :y, 'active')",
            e=ids["enrol"],
            t=t,
            s=ids["student"],
            sec=ids["section"],
            y=ids["year1"],
        )
        _run(
            c,
            "INSERT INTO sis.promotion_runs (id, tenant_id, from_academic_year_id, "
            "to_academic_year_id, plan_fingerprint, status) "
            "VALUES (:p, :t, :y1, :y2, :f, 'committed')",
            p=ids["prun"],
            t=t,
            y1=ids["year1"],
            y2=ids["year2"],
            f=_sha("promotion"),
        )
        _run(
            c,
            "INSERT INTO sis.promotion_items (tenant_id, run_id, student_id, outcome, "
            "from_enrollment_id, from_enrollment_version, previous_student_status) "
            "VALUES (:t, :p, :s, 'graduated', :e, 1, 'active')",
            t=t,
            p=ids["prun"],
            s=ids["student"],
            e=ids["enrol"],
        )
        # --- documents (kb) -----------------------------------------------------------------
        _run(
            c,
            "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
            "created_by) VALUES (:d, :t, 'evidence', 'evidence', 'Synthetic evidence', 'C2', :u)",
            d=ids["doc"],
            t=t,
            u=u["user"],
        )
        _run(
            c,
            "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
            "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
            "(:v, :t, :d, 1, :k, :sha, 'application/pdf', 10, 'ready', :u)",
            v=ids["ver"],
            t=t,
            d=ids["doc"],
            k=f"t/{t}/docs/{ids['doc']}/v1/original.pdf",
            sha=hashlib.sha256(b"doc").digest(),
            u=u["user"],
        )
        _run(
            c,
            "UPDATE kb.documents SET current_version_id = :v WHERE id = :d",
            v=ids["ver"],
            d=ids["doc"],
        )
        _run(
            c,
            "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, principal_ref) "
            "VALUES (:t, :d, 'role', 'synthetic_role')",
            t=t,
            d=ids["doc"],
        )
        _run(
            c,
            "INSERT INTO kb.document_chunks (id, tenant_id, document_id, version_id, chunk_no, "
            "content, token_count, embedding, embedding_model, doc_type, "
            "sensitivity) SELECT :i, :t, :d, :v, 0, 'Synthetic circular text', 3, "
            "array_fill(0.1::real, ARRAY[a.atttypmod])::halfvec, 'synthetic-embed', "
            "'evidence', 'C2' FROM pg_attribute a "
            "WHERE a.attrelid = 'kb.document_chunks'::regclass AND a.attname = 'embedding'",
            i=ids["chunk"],
            t=t,
            d=ids["doc"],
            v=ids["ver"],
        )
        _run(
            c,
            "INSERT INTO kb.embedding_cache (tenant_id, model, input_type, content_sha256, "
            "embedding) SELECT :t, 'synthetic-embed', 'query', :sha, "
            "array_fill(0.1::real, ARRAY[a.atttypmod])::halfvec FROM pg_attribute a "
            "WHERE a.attrelid = 'kb.embedding_cache'::regclass AND a.attname = 'embedding'",
            t=t,
            sha=hashlib.sha256(b"q").digest(),
        )
        _run(
            c,
            "INSERT INTO kb.llm_calls (id, tenant_id, feature, role, provider, model, outcome, "
            "attempts, latency_ms, input_tokens, output_tokens, cost_usd) VALUES "
            "(:i, :t, 'ask', 'answer', 'synthetic', 'synthetic-model', 'ok', 1, 5, 1, 1, 0)",
            i=ids["llm"],
            t=t,
        )
        _run(
            c,
            "INSERT INTO kb.queries (id, tenant_id, session_id, user_id, question_ciphertext, "
            "question_hmac, key_version, mode, status) VALUES "
            "(:i, :t, :sess, :u, '\\x0a', :h, 1, 'full', 'answered')",
            i=ids["query"],
            t=t,
            sess=uuid.uuid4(),
            u=u["user"],
            h=hashlib.sha256(b"h").digest(),
        )
        _run(
            c,
            "INSERT INTO kb.verified_answers (id, tenant_id, question_canonical, language, "
            "answer_text, citations, verified_by, verified_at, document_id) VALUES "
            "(:i, :t, 'synthetic question', 'en', 'Synthetic answer', "
            '\'[{"document_id": "x"}]\', :m, now(), :d)',
            i=ids["answer"],
            t=t,
            m=ids["m1"],
            d=ids["doc"],
        )
        _run(
            c,
            "INSERT INTO kb.upload_intents (id, tenant_id, purpose, document_id, version_no, "
            "object_key, declared_content_type, declared_size, max_bytes, created_by, "
            "expires_at) VALUES (:i, :t, 'evidence', :d, 2, :k, 'application/pdf', 10, 100, "
            ":u, now() + interval '10 minutes')",
            i=ids["intent"],
            t=t,
            d=uuid.uuid4(),
            k=f"t/{t}/uploads/{ids['intent']}/original.pdf",
            u=u["user"],
        )
        # --- imports, values, change requests, data quality --------------------------------
        _run(
            c,
            "INSERT INTO sis.import_mapping_templates (id, tenant_id, name, source, "
            "header_signature, headers, mapping, created_by) VALUES "
            "(:i, :t, 'Synthetic mapping', 'admission_register', :sig, '[]', '{}', :u)",
            i=ids["tpl"],
            t=t,
            sig=_sha("headers"),
            u=u["user"],
        )
        _run(
            c,
            "INSERT INTO ops.job_runs (id, tenant_id, task_name, idempotency_key, status) "
            "VALUES (:j, :t, 'imports.parse', :k, 'succeeded')",
            j=ids["job"],
            t=t,
            k=f"job-{uuid.uuid4().hex}",
        )
        _run(
            c,
            "INSERT INTO sis.import_batches (id, tenant_id, source, created_by, status, "
            "mapping_template_id, job_id) VALUES (:b, :t, 'admission_register', :u, 'parsed', "
            ":tpl, :j)",
            b=ids["batch"],
            t=t,
            u=u["user"],
            tpl=ids["tpl"],
            j=ids["job"],
        )
        _run(
            c,
            "INSERT INTO sis.import_rows (id, tenant_id, batch_id, row_no, parsed, status, "
            "student_id) VALUES (:r, :t, :b, 1, '{}', 'valid', :s)",
            r=ids["row"],
            t=t,
            b=ids["batch"],
            s=ids["student2"],
        )
        _run(
            c,
            "INSERT INTO sis.import_cell_edits (id, tenant_id, batch_id, batch_version, row_no, "
            "column_index, edited_by) VALUES (:e, :t, :b, 1, 1, 0, :u)",
            e=ids["edit"],
            t=t,
            b=ids["batch"],
            u=u["user"],
        )
        _run(
            c,
            "INSERT INTO sis.attribute_values (id, tenant_id, student_id, attribute_key, source, "
            "value_text, recorded_by, import_batch_id, evidence_document_id) VALUES "
            "(:v, :t, :s, 'full_name', 'admission_register', 'Synthetic Student', :u, :b, :d)",
            v=ids["value"],
            t=t,
            s=ids["student"],
            u=u["user"],
            b=ids["batch"],
            d=ids["doc"],
        )
        _run(
            c,
            "INSERT INTO sis.change_requests (id, tenant_id, student_id, attribute_key, reason, "
            "evidence_document_id, requested_by, expires_at, target_source, new_value_text, "
            "old_value_id, old_value_text, status) VALUES "
            "(:i, :t, :s, 'full_name', 'Synthetic correction reason', :d, :m, "
            "now() + interval '30 days', 'admission_register', 'Synthetic Student Two', :v, "
            "'Synthetic Student', 'pending')",
            i=ids["cr"],
            t=t,
            s=ids["student"],
            d=ids["doc"],
            m=ids["m1"],
            v=ids["value"],
        )
        _run(
            c,
            "INSERT INTO sis.attribute_values (id, tenant_id, student_id, attribute_key, source, "
            "value_text, recorded_by, change_request_id) VALUES "
            "(:v, :t, :s, 'full_name', 'manual_entry', 'Synthetic Student Two', :u, :cr)",
            v=uuid.uuid4(),
            t=t,
            s=ids["student"],
            u=u["user"],
            cr=ids["cr"],
        )
        _run(
            c,
            "INSERT INTO sis.dq_runs (id, tenant_id, trigger, status, started_by, "
            "requested_by_membership) VALUES (:i, :t, 'manual', 'queued', :u, :m)",
            i=ids["dqrun"],
            t=t,
            u=u["user"],
            m=ids["m1"],
        )
        _run(
            c,
            "INSERT INTO sis.dq_findings (id, tenant_id, fingerprint, student_id, rule_id, "
            "rule_version, severity, status, explanation_code, route_codes, conflict_hash, "
            "first_seen_run_id, last_seen_run_id) VALUES (:i, :t, :fp, :s, 'DQ-001', 1, 'high', "
            "'open', 'DQ-001', ARRAY['udise'], :h, :r, :r)",
            i=ids["finding"],
            t=t,
            fp=_sha(f"finding-{t}"),
            s=ids["student"],
            h=_sha("conflict"),
            r=ids["dqrun"],
        )
        # --- register-photo extraction -------------------------------------------------------
        _run(
            c,
            "INSERT INTO sis.extraction_batches (id, tenant_id, provider, created_by, "
            "created_by_membership, page_count, status) VALUES "
            "(:i, :t, 'fake', :u, :m, 1, 'review')",
            i=ids["xbatch"],
            t=t,
            u=u["user"],
            m=ids["m1"],
        )
        _run(
            c,
            "INSERT INTO sis.extraction_pages (id, tenant_id, batch_id, document_id, "
            "document_version_no, page_no, seq, status) VALUES "
            "(:i, :t, :b, :d, 1, 1, 1, 'done')",
            i=ids["xpage"],
            t=t,
            b=ids["xbatch"],
            d=ids["doc"],
        )
        _run(
            c,
            "INSERT INTO sis.extraction_items (id, tenant_id, batch_id, page_id, document_id, "
            "page_no, row_index, fields) VALUES (:i, :t, :b, :p, :d, 1, 0, '{}')",
            i=ids["xitem"],
            t=t,
            b=ids["xbatch"],
            p=ids["xpage"],
            d=ids["doc"],
        )
        # --- ops ----------------------------------------------------------------------------
        _run(
            c,
            "INSERT INTO ops.exports (id, tenant_id, kind, layout_version, formats, student_ids, "
            "student_count, requested_by, requested_by_membership, columns, status, job_id) "
            "VALUES (:i, :t, 'student_list', 1, ARRAY['csv'], ARRAY[:s]::uuid[], 1, :u, :m, "
            "ARRAY['admission_no'], 'queued', :j)",
            i=ids["export"],
            t=t,
            s=ids["student"],
            u=u["user"],
            m=ids["m1"],
            j=ids["job"],
        )
        _run(
            c,
            "INSERT INTO ops.export_files (id, tenant_id, export_id, format, object_key, "
            "content_type, size_bytes, sha256) VALUES (:i, :t, :e, 'csv', :k, 'text/csv', 1, :sha)",
            i=ids["efile"],
            t=t,
            e=ids["export"],
            k=f"t/{t}/exports/{ids['export']}/students.csv",
            sha=hashlib.sha256(b"csv").digest(),
        )
        _run(
            c,
            "INSERT INTO ops.tenant_exports (id, tenant_id, requested_by, "
            "requested_by_membership, job_id) VALUES (:i, :t, :u, :m, :j)",
            i=uuid.uuid4(),
            t=t,
            u=u["user"],
            m=ids["m1"],
            j=ids["job"],
        )
        _run(
            c,
            "INSERT INTO ops.retention_settings (id, tenant_id, rules, updated_by) "
            "VALUES (:i, :t, '{\"import_raw_files\": 30}', :u)",
            i=uuid.uuid4(),
            t=t,
            u=u["user"],
        )
        _run(
            c,
            "INSERT INTO ops.notifications (id, tenant_id, recipient_membership_id, template_key) "
            "VALUES (:i, :t, :m, 'imports.committed')",
            i=ids["note"],
            t=t,
            m=ids["m1"],
        )
        _run(
            c,
            "INSERT INTO ops.break_glass_grants (id, tenant_id, platform_user_id, reason, scope, "
            "status) VALUES (:i, :t, :p, 'Synthetic support request', '{}', 'requested')",
            i=ids["bg"],
            t=t,
            p=uuid.uuid4(),
        )
        _run(
            c,
            "INSERT INTO ops.idempotency_keys (tenant_id, user_id, key, method, route, "
            "request_sha256, status) VALUES (:t, :u, :k, 'POST', '/api/v1/students', :sha, "
            "'in_progress')",
            t=t,
            u=u["user"],
            k=f"idem-{uuid.uuid4().hex}",
            sha=hashlib.sha256(b"req").digest(),
        )
        _run(
            c,
            "INSERT INTO ops.outbox (id, tenant_id, event_type, payload) "
            "VALUES (:i, :t, 'students.changed', '{}')",
            i=ids["outbox"],
            t=t,
        )
        # --- audit chain (retained by design; ADR-0029) -------------------------------------
        _run(
            c,
            "INSERT INTO audit.chain_heads (tenant_id, last_seq, last_hash) "
            "VALUES (:t, 0, :h) ON CONFLICT (tenant_id) DO NOTHING",
            t=t,
            h=b"\x00" * 32,
        )
    return School(
        tenant_id=t,
        user_id=u["user"],
        shared_user_id=shared_user_id or u["user2"],
        membership_id=ids["m1"],
        other_membership_id=ids["m2"],
        student_id=ids["student"],
        document_id=ids["doc"],
    )


def tenant_tables(conn: Connection) -> list[str]:
    """Every table in the tenant schemas that carries ``tenant_id`` (partitions excluded)."""
    return list(
        conn.execute(
            text(
                """
                SELECT n.nspname || '.' || c.relname
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r', 'p')
                  AND NOT c.relispartition
                  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                              AND a.attname = 'tenant_id' AND NOT a.attisdropped)
                ORDER BY 1
                """
            ),
            {"schemas": list(TENANT_SCHEMAS)},
        ).scalars()
    )


def row_counts(admin_engine: Engine, tenant_id: uuid.UUID) -> dict[str, int]:
    """Rows per tenant table for one school (superuser read: RLS does not hide anything)."""
    with admin_engine.connect() as c:
        return {
            table: int(
                c.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),  # catalog names
                    {"t": tenant_id},
                ).scalar_one()
            )
            for table in tenant_tables(c)
        }


def tables_without_rows(admin_engine: Engine, tenant_id: uuid.UUID) -> list[str]:
    return sorted(t for t, n in row_counts(admin_engine, tenant_id).items() if n == 0)

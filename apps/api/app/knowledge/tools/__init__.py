"""Read-only record tools for the answer model (docs/06 §7; ADR-0008; FR-KB-004, SEC-020).

Responsibility: implement :class:`app.knowledge.interfaces.RecordTool` for the whitelist
``find_students``, ``get_student_facts``, ``get_value_history``, ``count_students``,
``list_findings``, ``list_documents`` and ``search_documents``. Each tool validates its input
against a strict schema (enums and caps from ``knowledge/config/tools.yaml``), checks its
permission, and calls the owning module's ``service`` with the caller's ``UserContext`` so RLS,
scopes and C3 rules apply exactly as in the UI. Results are ``search_result`` blocks with
``sos://`` sources (:mod:`app.knowledge.sources`).

Boundary: no writes, no network, no model calls (invariant 9): must not import gateway,
ingestion, service, httpx, boto3 or celery (import-linter ``knowledge-tools-read-only``).
"""

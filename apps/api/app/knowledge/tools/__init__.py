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

As built (M2 waves 4-5): ``access`` (the caller's ``AclKeys``, same rule as the documents
service), ``documents`` (:class:`DocumentSearch`, ``search_documents`` and ``list_documents``
over ``documents.service``), ``students`` (``find_students``, ``get_student_facts`` over
``students.service``), ``history`` (``get_value_history``), ``counts`` (``count_students``),
``findings`` (``list_findings`` over ``dq.service``), ``fees`` (``get_fee_dues`` over
``tally.service``, M6, behind the school's Tally connector flag; ADR-0032 Proposed), ``due``
(``list_due_tasks`` over ``circulars.service`` through a registered reader, D74) and
``registry`` (the tools described in ``tools.yaml``, offered per caller permission). D86 (FA-04,
FC-11): ``readiness`` (``get_readiness_summary`` over ``dq.service``), ``attendance``
(``get_attendance_summary`` over ``academics.service``, counts only, small sections hidden),
``certificates`` (``get_certificates`` over ``certificates.service``, one student) and
``compliance`` (``list_compliance_status`` over ``compliance.service``). None reads
``insights`` (invariant 14). The whole whitelist is built.
"""

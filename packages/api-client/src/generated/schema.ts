/* eslint-disable */
// GENERATED FILE: do not edit by hand.
// Source: ../../apps/api/openapi.json via openapi-typescript.
export interface paths {
    "/api/v1/academic-years": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Academic Years
         * @description Academic years, newest first (permission ``student.read_basic``).
         */
        get: operations["list_academic_years_api_v1_academic_years_get"];
        put?: never;
        /**
         * Create Academic Year
         * @description Create an academic year such as 2026-27; ``is_current`` makes it the only current year
         *     (permission ``tenant.structure.manage``). Accepts ``Idempotency-Key``.
         */
        post: operations["create_academic_year_api_v1_academic_years_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/academic-years/{year_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Academic Year
         * @description One academic year (permission ``student.read_basic``).
         */
        get: operations["get_academic_year_api_v1_academic_years__year_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Academic Year
         * @description Change a year's label or dates (permission ``tenant.structure.manage``; ``If-Match``).
         */
        patch: operations["update_academic_year_api_v1_academic_years__year_id__patch"];
        trace?: never;
    };
    "/api/v1/academic-years/{year_id}/make-current": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Make Academic Year Current
         * @description Make this the one current academic year (FR-TEN-010; permission
         *     ``tenant.structure.manage``; ``If-Match``).
         */
        post: operations["make_academic_year_current_api_v1_academic_years__year_id__make_current_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/academic-years/{year_id}/promotions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Promotions
         * @description Promotions out of this academic year, newest first, with ``can_undo`` and
         *     ``undo_until`` (permission ``tenant.structure.manage``).
         */
        get: operations["list_promotions_api_v1_academic_years__year_id__promotions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/academic-years/{year_id}/promotions:commit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Commit Promotion
         * @description Apply the promotion in one transaction (permission ``tenant.structure.manage``; accepts
         *     ``Idempotency-Key``): old enrolments are closed, new ones opened, graduates marked
         *     ``graduated``. 409 ``promotion_already_committed``, ``promotion_plan_changed`` or
         *     ``nothing_to_promote``; 422 ``no_target_section``. Can be undone within 24 hours.
         */
        post: operations["commit_promotion_api_v1_academic_years__year_id__promotions_commit_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/academic-years/{year_id}/promotions:preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Preview Promotion
         * @description Plan the year-end promotion of this academic year into ``to_academic_year_id`` without
         *     changing anything (permission ``tenant.structure.manage``). Class N goes to N+1 by class
         *     order; ``held_back_student_ids`` stay in their class; the last class graduates; students who
         *     left are skipped. Sections keep their name unless ``section_map`` says otherwise.
         *     ``problems`` lists students who cannot be placed; ``plan_fingerprint`` can be sent with the
         *     commit to make sure nothing changed in between.
         */
        post: operations["preview_promotion_api_v1_academic_years__year_id__promotions_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/academic-years/{year_id}/promotions:undo": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Undo Promotion
         * @description Undo this year's committed promotion within 24 hours (permission
         *     ``tenant.structure.manage``). 409 ``no_promotion``, ``promotion_undo_expired``, or
         *     ``promotion_has_dependents`` when an enrolment it touched changed afterwards.
         */
        post: operations["undo_promotion_api_v1_academic_years__year_id__promotions_undo_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/announcements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Announcements
         * @description Active platform announcements for this school (any active member; EN and TE text).
         */
        get: operations["list_announcements_api_v1_announcements_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/attributes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Attributes
         * @description Student attributes with classification (C2/C3), identity flag, allowed sources and
         *     English/Telugu labels (permission ``student.read_basic``).
         */
        get: operations["list_attributes_api_v1_attributes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/audit/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Audit Events
         * @description School audit log, newest first (permission ``audit.read``).
         *
         *     Filters: acting user, resource type/id, action, and a time range ``[from, to)``. Each event
         *     shows a summary of IDs, field names and codes only.
         */
        get: operations["list_audit_events_api_v1_audit_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/audit/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Verify Audit Chain
         * @description Check that the school's audit chain is unbroken (permission ``audit.read``; US-1001 AC2).
         */
        get: operations["verify_audit_chain_api_v1_audit_verify_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/breakglass/grants/{grant_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Revoke Grant
         * @description End support access now (step-up MFA), including emergency access.
         */
        post: operations["revoke_grant_api_v1_breakglass_grants__grant_id__revoke_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/breakglass/requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Requests
         * @description Support-access requests and grants of this school, newest first. New requests from
         *     SchoolOS support are fetched first, so a pending request shows up here right away.
         */
        get: operations["list_requests_api_v1_breakglass_requests_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/breakglass/requests/{request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Request
         * @description One request or grant: reason, scope, duration, who asked and its current status.
         */
        get: operations["get_request_api_v1_breakglass_requests__request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/breakglass/requests/{request_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve Request
         * @description Approve (step-up MFA). Access is read-only, limited to the request scope, starts now
         *     and ends by itself after the requested duration (at most 8 hours).
         */
        post: operations["approve_request_api_v1_breakglass_requests__request_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/breakglass/requests/{request_id}/deny": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Deny Request
         * @description Deny (step-up MFA). SchoolOS support gets no access.
         */
        post: operations["deny_request_api_v1_breakglass_requests__request_id__deny_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/change-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Change Requests
         * @description Change requests of students you can see, newest first (permission
         *     ``student.identity_change.request`` or ``student.identity_change.approve``). Values of
         *     sensitive (C3) fields are masked.
         */
        get: operations["list_change_requests_api_v1_change_requests_get"];
        put?: never;
        /**
         * Submit Change Request
         * @description Request a correction of an identity field (name, date of birth, gender, parents' names,
         *     admission number/date) with the new value, a reason (at least 10 characters) and an evidence
         *     document uploaded with purpose ``evidence`` (permission ``student.identity_change.request``).
         *     Someone else with ``student.identity_change.approve`` decides it. Accepts
         *     ``Idempotency-Key``. Errors: ``not_identity_attribute``, ``evidence_required`` (422),
         *     ``duplicate_pending_request`` (409).
         */
        post: operations["submit_change_request_api_v1_change_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/change-requests/{change_request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Change Request
         * @description One change request with old/new value, source, reason, evidence and decision; returns
         *     ``ETag`` for the decision calls (request or approve permission).
         */
        get: operations["get_change_request_api_v1_change_requests__change_request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/change-requests/{change_request_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve Change Request
         * @description Approve: records the new value as verified (the old value stays in history) and closes the
         *     request (permission ``student.identity_change.approve``, MFA within 5 minutes, not the
         *     requester, ``If-Match``). Errors: ``self_approval_forbidden`` (403), ``step_up_required``
         *     (428), ``request_not_pending`` / ``request_expired`` / ``request_outdated`` (409).
         */
        post: operations["approve_change_request_api_v1_change_requests__change_request_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/change-requests/{change_request_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cancel Change Request
         * @description Withdraw your own pending request (permission ``student.identity_change.request``,
         *     ``If-Match``). Error ``not_requester`` (403) for someone else's request.
         */
        post: operations["cancel_change_request_api_v1_change_requests__change_request_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/change-requests/{change_request_id}/memo": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Change Request Memo
         * @description Printable correction memo for the paper register, in English and Telugu (request or
         *     approve permission). Sensitive values appear only for ``student.read_sensitive`` holders.
         *     The view is audited.
         */
        get: operations["change_request_memo_api_v1_change_requests__change_request_id__memo_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/change-requests/{change_request_id}/reject": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reject Change Request
         * @description Reject with a reason (at least 10 characters) that the requester will see (permission
         *     ``student.identity_change.approve``, MFA within 5 minutes, not the requester, ``If-Match``).
         */
        post: operations["reject_change_request_api_v1_change_requests__change_request_id__reject_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/classes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Classes
         * @description Classes in display order (permission ``student.read_basic``; scoped holders see only
         *     their classes).
         */
        get: operations["list_classes_api_v1_classes_get"];
        put?: never;
        /**
         * Create Class
         * @description Add a class (permission ``tenant.structure.manage``). Accepts ``Idempotency-Key``.
         */
        post: operations["create_class_api_v1_classes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/classes/{class_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Class
         * @description One class (permission ``student.read_basic``; 404 outside the caller's scope).
         */
        get: operations["get_class_api_v1_classes__class_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Class
         * @description Rename or reorder a class; the code cannot change (permission
         *     ``tenant.structure.manage``; ``If-Match``).
         */
        patch: operations["update_class_api_v1_classes__class_id__patch"];
        trace?: never;
    };
    "/api/v1/classes/defaults": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Add Default Classes
         * @description Add any missing classes from Nursery to XII with English and Telugu names; existing
         *     classes are kept (permission ``tenant.structure.manage``).
         */
        post: operations["add_default_classes_api_v1_classes_defaults_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Documents
         * @description Documents you may see, newest first (permission ``document.read``; filtered by the
         *     document ACL and your class/section scopes).
         */
        get: operations["list_documents_api_v1_documents_get"];
        put?: never;
        /**
         * Register Document
         * @description Register an uploaded file with its metadata and ACL (permission ``document.upload``).
         *
         *     The file is checked by content (not extension): a mismatch answers 415 and the object is
         *     deleted; too large answers 413; the same file already visible to you answers 409
         *     ``duplicate_document``. Class teachers must limit the ACL to their own sections/classes.
         *     Answers 202: version 1 is ``queued`` for the malware scan. Accepts ``Idempotency-Key``.
         */
        post: operations["register_document_api_v1_documents_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents/{document_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Document
         * @description One document with its versions and processing status (permission ``document.read``;
         *     404 outside your ACL/scopes).
         */
        get: operations["get_document_api_v1_documents__document_id__get"];
        put?: never;
        post?: never;
        /**
         * Delete Document
         * @description Delete the document, all versions and stored files (permission ``document.manage_acl``).
         *     Evidence still linked to a student record answers 409 ``document_in_use``.
         */
        delete: operations["delete_document_api_v1_documents__document_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents/{document_id}/acl": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Acl
         * @description Replace who can see the document: roles, sections, classes or members (permission
         *     ``document.manage_acl``; ``If-Match``). An empty list limits it to school-wide readers.
         */
        put: operations["set_acl_api_v1_documents__document_id__acl_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents/{document_id}/download-url": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Download Url
         * @description A download link valid for 5 minutes, always saved as a file (permission
         *     ``document.read``). Only files that passed the malware scan are served (409 otherwise).
         */
        get: operations["get_download_url_api_v1_documents__document_id__download_url_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents/{document_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Add Version
         * @description Register an uploaded file as the next version; history is kept (permission
         *     ``document.upload``). Get the upload with ``POST /documents/uploads`` and ``document_id``.
         *     Accepts ``Idempotency-Key``.
         */
        post: operations["add_version_api_v1_documents__document_id__versions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/documents/uploads": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Upload
         * @description Get a presigned POST for one file (permission ``document.upload``).
         *
         *     Accepted: PDF, JPG, PNG, DOCX, XLSX up to 25 MB (evidence and register scans: PDF, JPG,
         *     PNG; spreadsheet imports: XLSX or CSV up to 10 MB). The form must be posted within 10
         *     minutes with the returned fields; the key, Content-Type and size are fixed by the policy.
         *     Send ``document_id`` to upload a new version. Accepts ``Idempotency-Key``.
         */
        post: operations["create_upload_api_v1_documents_uploads_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/findings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Findings
         * @description Findings, most severe first, with masked values, English/Telugu explanations and
         *     correction routes (permission ``dq.findings.read``). ``status`` defaults to unresolved
         *     (``open``, ``reopened``).
         */
        get: operations["list_findings_api_v1_dq_findings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/findings/{finding_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Finding
         * @description One finding (permission ``dq.findings.read``); ``ETag`` is its version.
         */
        get: operations["get_finding_api_v1_dq_findings__finding_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/findings/{finding_id}/resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Resolve Finding
         * @description Resolve with a note or a linked change request (permission ``dq.findings.resolve``).
         *     If the conflict is still there, the next check reopens it. Optional ``If-Match``.
         */
        post: operations["resolve_finding_api_v1_dq_findings__finding_id__resolve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/findings/{finding_id}/waive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Waive Finding
         * @description Accept a finding with a reason (permission ``dq.findings.waive`` with a fresh MFA
         *     sign-in, else 428 ``step_up_required``). Optional ``If-Match``.
         */
        post: operations["waive_finding_api_v1_dq_findings__finding_id__waive_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Profiles
         * @description Export pre-check profiles, e.g. CISCE registration and UDISE+ (permission
         *     ``dq.findings.read``).
         */
        get: operations["list_profiles_api_v1_dq_profiles_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/rules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Rules
         * @description The rule catalog DQ-001..DQ-012 with English/Telugu texts (permission
         *     ``dq.findings.read``).
         */
        get: operations["list_rules_api_v1_dq_rules_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start Run
         * @description Check sections, classes, students or an import batch, optionally for an export profile
         *     such as ``cisce-registration-2026`` (permission ``dq.findings.read``). Small scopes are
         *     checked at once (status ``completed``); bigger ones are queued (status ``queued``) and you
         *     are notified when they finish. Accepts ``Idempotency-Key``.
         */
        post: operations["start_run_api_v1_dq_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run
         * @description A check run with its counts (permission ``dq.findings.read``; class teachers see their
         *     own runs).
         */
        get: operations["get_run_api_v1_dq_runs__run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/dq/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Summary
         * @description Unresolved findings by severity and rule for the pre-check screen: blockers apart from
         *     warnings (permission ``dq.findings.read``).
         */
        get: operations["get_summary_api_v1_dq_summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/export-profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Export Profiles
         * @description Board and portal pre-check profiles (e.g. ``cisce-registration-2026``, ``udise-plus``)
         *     with their field order for the "ready to enter" sheet (permission ``export.board`` or
         *     ``export.portal``). ``allowed`` says whether you can run each one.
         */
        get: operations["list_export_profiles_api_v1_export_profiles_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/exports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Exports
         * @description Exports, newest first (any export permission or ``export.read_all``).
         *     ``requested_by=me`` (default): your own. ``requested_by=all``: every export of the school
         *     (``export.read_all``, else 403). Each item says who requested it (``requested_by``), whether
         *     it is yours (``own``) and whether you may download it (``can_download``).
         */
        get: operations["list_exports_api_v1_exports_get"];
        put?: never;
        /**
         * Create Precheck Export
         * @description Make a pre-check report for a board or portal profile (``export.board`` for board
         *     profiles, ``export.portal`` for portal profiles; also ``student.read_basic`` and
         *     ``dq.findings.read``) with a recent sign-in with MFA (428 ``step_up_required``). Choose
         *     sections or classes (empty = every student you can see), the formats (``xlsx``, ``pdf``)
         *     and the language (``en``, ``te``). The students are checked again and the files are made
         *     in the background (202); you are notified when they are ready. Restricted (C3) values such
         *     as the UDISE+ ``category`` are hidden unless ``include_sensitive`` is true (needs
         *     ``student.read_sensitive``, else 403 ``sensitive_not_allowed``); the audit log then lists
         *     the restricted columns included. Errors: 422 ``unknown_profile``, ``no_students``,
         *     ``too_many_students``. Accepts ``Idempotency-Key``.
         */
        post: operations["create_precheck_export_api_v1_exports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/exports/{export_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Export
         * @description One export: status (``queued``, ``running``, ``ready``, ``failed``, ``expired``), files,
         *     when they are deleted and who requested it. Your own, or anyone's with
         *     ``export.read_all``; 404 otherwise.
         */
        get: operations["get_export_api_v1_exports__export_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/exports/{export_id}/download-url": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Export Download Url
         * @description A download link for one file of a ready export, valid at most 5 minutes (the first
         *     format unless ``format`` is given). Your own export: student lists and exports with
         *     restricted values need a recent sign-in with MFA (428). Someone else's export needs
         *     ``export.download_any`` and always a recent sign-in with MFA (428); 403 ``not_own_export``
         *     if you can see it (``export.read_all``) but not download it, 404 otherwise. Errors: 409
         *     ``export_not_ready``, ``export_failed``, ``export_expired``. Every download is recorded in
         *     the audit log.
         */
        get: operations["get_export_download_url_api_v1_exports__export_id__download_url_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/exports/student-list": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Student List Export
         * @description Export a student list with the columns you choose as CSV or XLSX (permission
         *     ``student.export``, recent sign-in with MFA). Columns are attribute keys (``GET
         *     /attributes``) or ``class``, ``section``, ``roll_no``; restricted (C3) columns need
         *     ``student.read_sensitive`` (403 ``sensitive_not_allowed``) and the Aadhaar-as-printed fields
         *     are never exported (422 ``column_not_exportable``). Accepts ``Idempotency-Key``.
         */
        post: operations["create_student_list_export_api_v1_exports_student_list_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/extraction-batches": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Batches
         * @description Register-photo batches, newest first, with progress counters (permission
         *     ``import.run``).
         */
        get: operations["list_batches_api_v1_extraction_batches_get"];
        put?: never;
        /**
         * Create Batch
         * @description Read register-page photos into the verification queue (permission ``import.run``).
         *
         *     Send the ids of ``register_scan`` documents that passed the malware scan: JPG or PNG, one
         *     page each (PDF answers 422 ``pdf_not_supported`` for now: upload a photo of each page).
         *     Answers 202; follow progress with ``GET /extraction-batches/{id}``. Accepts
         *     ``Idempotency-Key``.
         */
        post: operations["create_batch_api_v1_extraction_batches_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/extraction-batches/{batch_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Batch
         * @description One batch with progress per page (US-402 AC4). A page with ``image_withheld`` showed a
         *     full Aadhaar number: its image is not shown (permission ``import.run``).
         */
        get: operations["get_batch_api_v1_extraction_batches__batch_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/extraction-items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Items
         * @description The verification queue in page order (permission ``import.run``). Each field carries its
         *     confidence and region; ``low_confidence_fields`` lists the ones to check carefully.
         */
        get: operations["list_items_api_v1_extraction_items_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/extraction-items/{item_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Item
         * @description One row with a 5-minute link to its page image and students with the same admission
         *     number (permission ``import.run``; the image also needs ``document.read`` on the page).
         */
        get: operations["get_item_api_v1_extraction_items__item_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/extraction-items/{item_id}/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Confirm Item
         * @description Save the row as you read it on the page (permission ``import.commit``).
         *
         *     Creates a student (or adds to ``student_id``) with source ``admission_register`` and the
         *     page as evidence. A row already checked answers 409 ``item_already_reviewed``; changing an
         *     existing register identity value answers 403 ``identity_change_required`` (use a change
         *     request). Accepts ``Idempotency-Key``.
         */
        post: operations["confirm_item_api_v1_extraction_items__item_id__confirm_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/extraction-items/{item_id}/reject": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reject Item
         * @description Discard a row that is not a student entry; nothing is recorded (permission
         *     ``import.commit``).
         */
        post: operations["reject_item_api_v1_extraction_items__item_id__reject_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/fleet/heartbeat": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Heartbeat
         * @description HMAC-authenticated heartbeat from a dedicated host (SEC-028; docs/16 §12).
         */
        post: operations["heartbeat_api_v1_fleet_heartbeat_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/import-templates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Templates
         * @description Saved column mappings; a file with the same headers reuses one automatically
         *     (permission ``import.run``).
         */
        get: operations["list_templates_api_v1_import_templates_get"];
        put?: never;
        /**
         * Create Template
         * @description Save an import's column mapping as a template (permission ``import.run``). Accepts
         *     ``Idempotency-Key``.
         */
        post: operations["create_template_api_v1_import_templates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Imports
         * @description The school's imports, newest first (permission ``import.run``).
         */
        get: operations["list_imports_api_v1_imports_get"];
        put?: never;
        /**
         * Create Import
         * @description Import an uploaded spreadsheet (permission ``import.run``).
         *
         *     Upload the file first with ``POST /documents/uploads`` (purpose ``import_file``: XLSX or
         *     CSV, at most 10 MB) and register it; once it passed the virus check, send its
         *     ``document_id`` and the ``source`` the data comes from (e.g. ``admission_register``,
         *     ``udise_plus``). Answers 202: the file is read (formulas are never run) and columns are
         *     matched to fields in the background. Accepts ``Idempotency-Key``.
         */
        post: operations["create_import_api_v1_imports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{import_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Import
         * @description One import: status, columns with suggested and chosen fields, row counts, revert
         *     deadline (permission ``import.run``). ``ETag`` is needed to change the mapping.
         */
        get: operations["get_import_api_v1_imports__import_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{import_id}/commit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Commit Import
         * @description Add a checked file to the student records, all rows or none (permission
         *     ``import.commit``; 202). Values are recorded from the import's source; identity values
         *     from the admission register stay provisional until verified. With rows in error send
         *     ``{"skip_error_rows": true}`` to add only the valid rows. Accepts ``Idempotency-Key``.
         */
        post: operations["commit_import_api_v1_imports__import_id__commit_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{import_id}/mapping": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Set Mapping
         * @description Choose which field each column fills (permission ``import.run``; ``If-Match``). Check
         *     the file again afterwards with ``POST /imports/{id}/validate``.
         */
        put: operations["set_mapping_api_v1_imports__import_id__mapping_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{import_id}/revert": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Revert Import
         * @description Undo an added import within 24 hours (permission ``import.commit``). Refused with 409
         *     ``import_has_dependents`` when records from it were changed or are used since, and 409
         *     ``revert_window_closed`` after 24 hours.
         */
        post: operations["revert_import_api_v1_imports__import_id__revert_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{import_id}/rows": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Rows
         * @description Checked rows in file order with row-level errors and warnings (permission
         *     ``import.run``); ``status=error`` lists the rows to fix. Restricted (C3) values are never
         *     shown, only which of them a row has.
         */
        get: operations["list_rows_api_v1_imports__import_id__rows_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{import_id}/validate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Validate Import
         * @description Check every row with the current mapping (permission ``import.run``; 202). Nothing is
         *     saved to student records. Accepts ``Idempotency-Key``.
         */
        post: operations["validate_import_api_v1_imports__import_id__validate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Me
         * @description The signed-in user in the active school: roles, effective permissions, scopes, language
         *     and the schools they can switch to (permission: any active member).
         */
        get: operations["get_me_api_v1_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/me/accept-invitations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Accept My Invitations
         * @description Accept the signed-in user's pending invitations (ADR-0019). The BFF calls this after the
         *     OIDC callback, before ``/me/login-event``. Works without ``X-Active-Tenant``; a privileged
         *     active membership without MFA gets 403 ``mfa_required`` (FR-IAM-002).
         */
        post: operations["accept_my_invitations_api_v1_me_accept_invitations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/me/active-tenant": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Set Active Tenant
         * @description Check that the user may work in ``tenant_id`` and return their context there
         *     (FR-IAM-013; permission: authenticated). The BFF then sends ``X-Active-Tenant``.
         */
        post: operations["set_active_tenant_api_v1_me_active_tenant_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/me/login-event": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Record Login Event
         * @description Called once by the BFF after sign-in: audits ``auth.login.succeeded`` (or
         *     ``auth.login.denied`` with the reason) in the school's log (permission: authenticated;
         *     rate-limited per user).
         */
        post: operations["record_login_event_api_v1_me_login_event_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/me/schools": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List My Schools
         * @description Schools the signed-in user can work in, for the school picker. Works without
         *     ``X-Active-Tenant`` (FR-IAM-013; permission: authenticated). A privileged membership
         *     without MFA gets 403 ``mfa_required``, as on every other route (FR-IAM-002).
         */
        get: operations["list_my_schools_api_v1_me_schools_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Notifications
         * @description Your notifications, newest first, in your language (English or Telugu).
         */
        get: operations["list_notifications_api_v1_notifications_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/notifications/{notification_id}/read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Mark Read
         * @description Mark one of your notifications as read (repeating it changes nothing).
         */
        post: operations["mark_read_api_v1_notifications__notification_id__read_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/notifications/read-all": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Mark All Read
         * @description Mark all your notifications as read.
         */
        post: operations["mark_all_read_api_v1_notifications_read_all_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/notifications/unread-count": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Unread Count
         * @description How many of your notifications are unread (for the bell badge).
         */
        get: operations["unread_count_api_v1_notifications_unread_count_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/permissions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Permissions
         * @description The grantable permission catalog (permission ``user.manage``).
         */
        get: operations["list_permissions_api_v1_permissions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/announcements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Announcements */
        get: operations["list_announcements_api_v1_platform_announcements_get"];
        put?: never;
        /** Create Announcement */
        post: operations["create_announcement_api_v1_platform_announcements_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/announcements/{announcement_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Announcement */
        patch: operations["update_announcement_api_v1_platform_announcements__announcement_id__patch"];
        trace?: never;
    };
    "/api/v1/platform/announcements/{announcement_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Announcement */
        post: operations["cancel_announcement_api_v1_platform_announcements__announcement_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/audit/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Audit Events
         * @description Filters: actor, action, tenant_id, from, to. ``Accept: text/csv`` returns CSV.
         */
        get: operations["list_audit_events_api_v1_platform_audit_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/audit/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Verify Audit */
        post: operations["verify_audit_api_v1_platform_audit_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/break-glass-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Breakglass */
        get: operations["list_breakglass_api_v1_platform_break_glass_requests_get"];
        put?: never;
        /** Create Breakglass */
        post: operations["create_breakglass_api_v1_platform_break_glass_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/break-glass-requests/{request_id}/emergency-confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Confirm Breakglass
         * @description Two different operators must confirm emergency access (SEC-029).
         */
        post: operations["confirm_breakglass_api_v1_platform_break_glass_requests__request_id__emergency_confirm_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/dashboard": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Dashboard */
        get: operations["get_dashboard_api_v1_platform_dashboard_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/deployments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Deployments */
        get: operations["list_deployments_api_v1_platform_deployments_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/deployments/{deployment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Deployment */
        get: operations["get_deployment_api_v1_platform_deployments__deployment_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Deployment */
        patch: operations["update_deployment_api_v1_platform_deployments__deployment_id__patch"];
        trace?: never;
    };
    "/api/v1/platform/deployments/{deployment_id}/decommission": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decommission Deployment */
        post: operations["decommission_deployment_api_v1_platform_deployments__deployment_id__decommission_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/deployments/{deployment_id}/heartbeat-key:rotate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rotate Heartbeat Key
         * @description Returns the new key ONCE for the runbook (SSM Parameter Store).
         */
        post: operations["rotate_heartbeat_key_api_v1_platform_deployments__deployment_id__heartbeat_key_rotate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/flags": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Flags */
        get: operations["list_flags_api_v1_platform_flags_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/flags/{key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Put Flag */
        put: operations["put_flag_api_v1_platform_flags__key__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/flags/{key}/tenants/{tenant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Put Flag Override */
        put: operations["put_flag_override_api_v1_platform_flags__key__tenants__tenant_id__put"];
        post?: never;
        /** Delete Flag Override */
        delete: operations["delete_flag_override_api_v1_platform_flags__key__tenants__tenant_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/fleet/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Fleet Versions */
        get: operations["fleet_versions_api_v1_platform_fleet_versions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/invoice-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Invoice Run
         * @description Generate drafts for a month now (idempotent per month; the beat job does the same).
         */
        post: operations["invoice_run_api_v1_platform_invoice_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/invoices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Invoices */
        get: operations["list_invoices_api_v1_platform_invoices_get"];
        put?: never;
        /** Create Invoice */
        post: operations["create_invoice_api_v1_platform_invoices_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/invoices/{invoice_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Invoice */
        get: operations["get_invoice_api_v1_platform_invoices__invoice_id__get"];
        put?: never;
        post?: never;
        /** Discard Invoice */
        delete: operations["discard_invoice_api_v1_platform_invoices__invoice_id__delete"];
        options?: never;
        head?: never;
        /** Update Invoice */
        patch: operations["update_invoice_api_v1_platform_invoices__invoice_id__patch"];
        trace?: never;
    };
    "/api/v1/platform/invoices/{invoice_id}/issue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Issue Invoice
         * @description Assigns the next gapless number of the financial year (e.g. SOS/26-27/000123).
         */
        post: operations["issue_invoice_api_v1_platform_invoices__invoice_id__issue_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/invoices/{invoice_id}/payments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Payment */
        post: operations["record_payment_api_v1_platform_invoices__invoice_id__payments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/invoices/{invoice_id}/void": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Void Invoice */
        post: operations["void_invoice_api_v1_platform_invoices__invoice_id__void_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Job */
        get: operations["get_job_api_v1_platform_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Me */
        get: operations["me_api_v1_platform_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/operators": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Operators */
        get: operations["list_operators_api_v1_platform_operators_get"];
        put?: never;
        /** Invite Operator */
        post: operations["invite_operator_api_v1_platform_operators_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/operators/{operator_id}/deactivate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Deactivate Operator */
        post: operations["deactivate_operator_api_v1_platform_operators__operator_id__deactivate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/operators/{operator_id}/roles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Operator Roles */
        put: operations["set_operator_roles_api_v1_platform_operators__operator_id__roles_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/payments/{payment_id}/reverse": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reverse Payment */
        post: operations["reverse_payment_api_v1_platform_payments__payment_id__reverse_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/plans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Plans */
        get: operations["list_plans_api_v1_platform_plans_get"];
        put?: never;
        /** Create Plan */
        post: operations["create_plan_api_v1_platform_plans_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/plans/{plan_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Plan */
        get: operations["get_plan_api_v1_platform_plans__plan_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Plan */
        patch: operations["update_plan_api_v1_platform_plans__plan_id__patch"];
        trace?: never;
    };
    "/api/v1/platform/plans/{plan_id}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Publish Plan */
        post: operations["publish_plan_api_v1_platform_plans__plan_id__publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/plans/{plan_id}/retire": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retire Plan */
        post: operations["retire_plan_api_v1_platform_plans__plan_id__retire_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Subscriptions */
        get: operations["list_subscriptions_api_v1_platform_subscriptions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Subscription */
        get: operations["get_subscription_api_v1_platform_subscriptions__sub_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}/activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Activate Subscription */
        post: operations["activate_subscription_api_v1_platform_subscriptions__sub_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cancel Subscription
         * @description Cancels at period end (a trial is cancelled at once).
         */
        post: operations["cancel_subscription_api_v1_platform_subscriptions__sub_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}/change-plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Change Plan
         * @description Takes effect at the next period (no proration in M0); immediately for a trial.
         */
        post: operations["change_plan_api_v1_platform_subscriptions__sub_id__change_plan_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}/extend-trial": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Extend Trial */
        post: operations["extend_trial_api_v1_platform_subscriptions__sub_id__extend_trial_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}/price-override": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Price Override */
        put: operations["set_price_override_api_v1_platform_subscriptions__sub_id__price_override_put"];
        post?: never;
        /** Clear Price Override */
        delete: operations["clear_price_override_api_v1_platform_subscriptions__sub_id__price_override_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}/reactivate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reactivate Subscription */
        post: operations["reactivate_subscription_api_v1_platform_subscriptions__sub_id__reactivate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/subscriptions/{sub_id}/suspend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Suspend Subscription
         * @description Never automatic: only past_due after the 15-day grace; exam windows need an owner.
         */
        post: operations["suspend_subscription_api_v1_platform_subscriptions__sub_id__suspend_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/support/tickets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Tickets */
        get: operations["list_tickets_api_v1_platform_support_tickets_get"];
        put?: never;
        /** Open Ticket */
        post: operations["open_ticket_api_v1_platform_support_tickets_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/support/tickets/{ticket_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Ticket */
        get: operations["get_ticket_api_v1_platform_support_tickets__ticket_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Ticket */
        patch: operations["update_ticket_api_v1_platform_support_tickets__ticket_id__patch"];
        trace?: never;
    };
    "/api/v1/platform/support/tickets/{ticket_id}/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Ticket Message */
        post: operations["add_ticket_message_api_v1_platform_support_tickets__ticket_id__messages_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Tenants */
        get: operations["list_tenants_api_v1_platform_tenants_get"];
        put?: never;
        /**
         * Provision Tenant
         * @description Provision a school on the shared tier or register a dedicated host (FR-PLT-002/003).
         *
         *     Submitting the same request again (any Idempotency-Key) resumes an unfinished provisioning
         *     or replays the finished one; the same code with a different request is 409 ``duplicate``;
         *     a provisioning another request is running is 409 ``provisioning_in_progress``. The
         *     dedicated heartbeat key is returned only in the first response.
         */
        post: operations["provision_tenant_api_v1_platform_tenants_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Tenant */
        get: operations["get_tenant_api_v1_platform_tenants__tenant_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Activate Tenant
         * @description Go-live (provisioning -> active). 409 ``provisioning_incomplete`` until provisioning has
         *     finished; also refused by the database without a data key.
         */
        post: operations["activate_tenant_api_v1_platform_tenants__tenant_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/billing-account": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Billing Account */
        get: operations["get_billing_account_api_v1_platform_tenants__tenant_id__billing_account_get"];
        /** Put Billing Account */
        put: operations["put_billing_account_api_v1_platform_tenants__tenant_id__billing_account_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/offboarding": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request Offboarding
         * @description Two-person rule step 1 (SEC-029).
         */
        post: operations["request_offboarding_api_v1_platform_tenants__tenant_id__offboarding_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/offboarding:approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve Offboarding
         * @description Two-person rule step 2: a different operator (409 ``same_operator`` otherwise).
         */
        post: operations["approve_offboarding_api_v1_platform_tenants__tenant_id__offboarding_approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/owner-invite:resend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resend Owner Invite */
        post: operations["resend_owner_invite_api_v1_platform_tenants__tenant_id__owner_invite_resend_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/provisioning:resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Resume Provisioning
         * @description Resume an unfinished or failed provisioning (FR-PLT-002, docs/16 §5.4).
         *
         *     Idempotent: a finished provisioning is returned as it is. 409 ``provisioning_in_progress``
         *     while another request holds it; 409 ``resume_needs_request`` for a provisioning started
         *     before resumable provisioning (submit the same request again).
         */
        post: operations["resume_provisioning_api_v1_platform_tenants__tenant_id__provisioning_resume_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/reactivate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reactivate Tenant */
        post: operations["reactivate_tenant_api_v1_platform_tenants__tenant_id__reactivate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/suspend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suspend Tenant */
        post: operations["suspend_tenant_api_v1_platform_tenants__tenant_id__suspend_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/tenants/{tenant_id}/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Tenant Usage */
        get: operations["tenant_usage_api_v1_platform_tenants__tenant_id__usage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/platform/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** All Usage */
        get: operations["all_usage_api_v1_platform_usage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Roles
         * @description Roles of this school with their permissions (permission ``user.manage``).
         */
        get: operations["list_roles_api_v1_roles_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Sections
         * @description Sections, optionally for one year and/or class (permission ``student.read_basic``;
         *     class teachers see only their sections).
         */
        get: operations["list_sections_api_v1_sections_get"];
        put?: never;
        /**
         * Create Section
         * @description Add a section to a class for an academic year, optionally with its class teacher
         *     (permission ``tenant.structure.manage``). Accepts ``Idempotency-Key``.
         */
        post: operations["create_section_api_v1_sections_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sections/{section_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Section
         * @description One section (permission ``student.read_basic``; 404 outside the caller's scope).
         */
        get: operations["get_section_api_v1_sections__section_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Section
         * @description Rename a section or assign/clear its class teacher (permission
         *     ``tenant.structure.manage``; ``If-Match``).
         */
        patch: operations["update_section_api_v1_sections__section_id__patch"];
        trace?: never;
    };
    "/api/v1/students": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Search Students
         * @description List students by class, section and status (permission ``student.read_basic``; class
         *     and subject teachers see only students in their sections/classes this year). To search by
         *     name, parent name or admission number use ``POST /students/search``: the ``query`` and
         *     ``admission_no`` parameters still work but are deprecated because URLs are logged by
         *     proxies and load balancers. A response to a request that used them carries a
         *     ``Deprecation`` header (RFC 9745), a ``Sunset: Thu, 31 Dec 2026 23:59:59 GMT`` header
         *     (RFC 8594; the parameters may stop working after that date) and
         *     ``Link: </api/v1/students/search>; rel="successor-version"``.
         */
        get: operations["search_students_api_v1_students_get"];
        put?: never;
        /**
         * Create Student
         * @description Add a student with first values, each with its source; optionally enrol in a section
         *     (permission ``student.create``). Accepts ``Idempotency-Key``.
         */
        post: operations["create_student_api_v1_students_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/students/{student_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Student
         * @description Canonical profile and current per-source values (permission ``student.read_basic``).
         *     Sensitive fields are hidden without ``student.read_sensitive`` and masked with it.
         */
        get: operations["get_student_api_v1_students__student_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Student
         * @description Change the record status, e.g. ``left`` (permission ``student.update_nonidentity``;
         *     ``If-Match`` required).
         */
        patch: operations["update_student_api_v1_students__student_id__patch"];
        trace?: never;
    };
    "/api/v1/students/{student_id}/enrollments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Enrollments
         * @description Every enrolment of the student (any year, active or closed), newest first; each carries
         *     its ``version`` for ``If-Match`` (permission ``student.read_basic``).
         */
        get: operations["list_enrollments_api_v1_students__student_id__enrollments_get"];
        put?: never;
        /**
         * Enrol Student
         * @description Enrol in a section; an active enrolment in the same year becomes ``transferred``
         *     (permission ``student.update_nonidentity``). Accepts ``Idempotency-Key``.
         */
        post: operations["enrol_student_api_v1_students__student_id__enrollments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/students/{student_id}/enrollments/{enrollment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Enrollment
         * @description Correct the roll number, or move an active enrolment to another section of the same
         *     class and year (permission ``student.update_nonidentity``; ``If-Match`` with the
         *     enrolment's version, 412 ``precondition_failed`` when stale). Moving to another class is a
         *     new enrolment (``POST …/enrollments``).
         */
        patch: operations["update_enrollment_api_v1_students__student_id__enrollments__enrollment_id__patch"];
        trace?: never;
    };
    "/api/v1/students/{student_id}/enrollments/{enrollment_id}/end": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * End Enrollment
         * @description Close an active enrolment as ``completed`` (default) or ``transferred`` on ``ended_on``
         *     (default today); the record status is unchanged (permission
         *     ``student.update_nonidentity``; ``If-Match`` with the enrolment's version). 409
         *     ``enrollment_not_active`` when it is already closed.
         */
        post: operations["end_enrollment_api_v1_students__student_id__enrollments__enrollment_id__end_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/students/{student_id}/guardians": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Guardians
         * @description Parents and guardians (permission ``student.read_basic``); phone and address masked.
         */
        get: operations["list_guardians_api_v1_students__student_id__guardians_get"];
        put?: never;
        /**
         * Add Guardian
         * @description Add a guardian, or link an existing one by ``guardian_id`` (permission
         *     ``student.update_nonidentity``). Accepts ``Idempotency-Key``.
         */
        post: operations["add_guardian_api_v1_students__student_id__guardians_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/students/{student_id}/guardians/{guardian_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Remove Guardian
         * @description Unlink a guardian from the student (permission ``student.update_nonidentity``;
         *     ``If-Match`` with the guardian's ETag). A guardian no other student is linked to is deleted
         *     with their phone and address.
         */
        delete: operations["remove_guardian_api_v1_students__student_id__guardians__guardian_id__delete"];
        options?: never;
        head?: never;
        /**
         * Update Guardian
         * @description Change a guardian's name, phone, address, relationship or primary flag (permission
         *     ``student.update_nonidentity``; ``If-Match`` with the guardian's ETag).
         */
        patch: operations["update_guardian_api_v1_students__student_id__guardians__guardian_id__patch"];
        trace?: never;
    };
    "/api/v1/students/{student_id}/sensitive-reveal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reveal Sensitive
         * @description Show one sensitive (C3) value; every reveal is audited (permission
         *     ``student.read_sensitive``, class teachers only for their sections).
         */
        post: operations["reveal_sensitive_api_v1_students__student_id__sensitive_reveal_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/students/{student_id}/values": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Values
         * @description Full value history, newest first, for one attribute or all (permission
         *     ``student.read_basic``).
         */
        get: operations["list_values_api_v1_students__student_id__values_get"];
        put?: never;
        /**
         * Record Value
         * @description Record a value from a source; the previous value of that source stays in history
         *     (permission ``student.update_nonidentity``). Identity fields from the admission register
         *     need a change request (403 ``identity_change_required``). Optional ``If-Match``.
         */
        post: operations["record_value_api_v1_students__student_id__values_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/students/{student_id}/values/{value_id}/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify Value
         * @description Mark the current value of a non-identity attribute verified or rejected (permission
         *     ``student.update_nonidentity``). Identity values are verified by change requests.
         */
        post: operations["verify_value_api_v1_students__student_id__values__value_id__verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/students/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Search Students By Body
         * @description Find students by partial name in English or Telugu, admission number, class/section
         *     (``9b``, ``IX-B``) or parent name, with the filters in the JSON body so personal data never
         *     appears in a URL (SEC-008; permission ``student.read_basic``; class and subject teachers see
         *     only students in their sections/classes this year). Same results, page size and cursor as
         *     ``GET /students``; send ``next_cursor`` back as ``cursor`` with the same filters. Read-only:
         *     nothing is written, so no ``Idempotency-Key``. A full Aadhaar number anywhere in the body is
         *     refused (422 ``aadhaar_full_number_rejected``).
         */
        post: operations["search_students_by_body_api_v1_students_search_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/support/tickets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Tickets
         * @description This school's tickets, newest first (permission ``support.ticket.create``).
         */
        get: operations["list_tickets_api_v1_support_tickets_get"];
        put?: never;
        /**
         * Open Ticket
         * @description Open a support ticket (permission ``support.ticket.create``). Do not include student
         *     names, dates of birth, Aadhaar or phone numbers: text is redacted before it is stored.
         */
        post: operations["open_ticket_api_v1_support_tickets_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/support/tickets/{ticket_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Ticket
         * @description One of this school's tickets with its messages (internal notes are never shown).
         */
        get: operations["get_ticket_api_v1_support_tickets__ticket_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/support/tickets/{ticket_id}/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reply To Ticket
         * @description Reply on this school's ticket (permission ``support.ticket.create``).
         */
        post: operations["reply_to_ticket_api_v1_support_tickets__ticket_id__messages_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/tenant": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Tenant
         * @description This school's profile and settings (permission: any active member).
         */
        get: operations["get_tenant_api_v1_tenant_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Tenant Settings
         * @description Change languages, date format, idle timeout (5-30 min), AI features and the monthly AI
         *     budget (permission ``tenant.settings.manage``, step-up; ``If-Match`` required).
         */
        patch: operations["update_tenant_settings_api_v1_tenant_patch"];
        trace?: never;
    };
    "/api/v1/tenant/billing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Billing
         * @description Current plan, status, period and usage vs limits (permission ``tenant.billing.read``).
         */
        get: operations["get_billing_api_v1_tenant_billing_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/tenant/billing/invoices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Billing Invoices
         * @description This school's issued invoices, newest first (last 24; permission ``tenant.billing.read``).
         */
        get: operations["list_billing_invoices_api_v1_tenant_billing_invoices_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Users
         * @description Staff accounts of this school (permission ``user.manage``).
         */
        get: operations["list_users_api_v1_users_get"];
        put?: never;
        /**
         * Invite User
         * @description Invite a staff member with roles and scopes (permission ``user.manage``, step-up).
         *
         *     Roles carrying permissions the inviter lacks are refused (403 ``role_not_grantable``).
         *     Accepts ``Idempotency-Key``.
         */
        post: operations["invite_user_api_v1_users_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/{user_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get User
         * @description One staff account (permission ``user.manage``). Returns an ``ETag``.
         */
        get: operations["get_user_api_v1_users__user_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update User Status
         * @description Activate, suspend or remove a staff member (permission ``user.manage``, step-up;
         *     ``If-Match`` required). The last active owner cannot be suspended (409 ``last_owner``).
         */
        patch: operations["update_user_status_api_v1_users__user_id__patch"];
        trace?: never;
    };
    "/api/v1/users/{user_id}/roles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Replace User Roles
         * @description Replace a staff member's roles (permission ``role.assign``, step-up). Effective within
         *     60 s (FR-IAM-014).
         */
        put: operations["replace_user_roles_api_v1_users__user_id__roles_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/{user_id}/scopes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Replace User Scopes
         * @description Replace a staff member's class/section scopes (permission ``role.assign``, step-up).
         */
        put: operations["replace_user_scopes_api_v1_users__user_id__scopes_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Healthz
         * @description Liveness: the process is up. No dependencies are checked.
         */
        get: operations["healthz_healthz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/readyz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Readyz
         * @description Readiness: database and Redis/Valkey reachable.
         */
        get: operations["readyz_readyz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AcademicYearCreate */
        AcademicYearCreate: {
            /**
             * Ends On
             * Format: date
             */
            ends_on: string;
            /**
             * Is Current
             * @default false
             */
            is_current: boolean;
            /** Label */
            label: string;
            /**
             * Starts On
             * Format: date
             */
            starts_on: string;
        };
        /** AcademicYearOut */
        AcademicYearOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Ends On
             * Format: date
             */
            ends_on: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Current */
            is_current: boolean;
            /** Label */
            label: string;
            /**
             * Starts On
             * Format: date
             */
            starts_on: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /** AcademicYearUpdate */
        AcademicYearUpdate: {
            /** Ends On */
            ends_on?: string | null;
            /** Label */
            label?: string | null;
            /** Starts On */
            starts_on?: string | null;
        };
        /**
         * AcceptedInvitationsOut
         * @description Schools whose invitation was accepted by this sign-in (ADR-0019).
         */
        AcceptedInvitationsOut: {
            /** Accepted */
            accepted: string[];
        };
        /** AclEntry */
        AclEntry: {
            /** Principal Ref */
            principal_ref: string;
            /**
             * Principal Type
             * @enum {string}
             */
            principal_type: "role" | "section" | "class" | "membership";
        };
        /** AclEntryOut */
        AclEntryOut: {
            /** Principal Ref */
            principal_ref: string;
            /**
             * Principal Type
             * @enum {string}
             */
            principal_type: "role" | "section" | "class" | "membership";
        };
        /** AclUpdate */
        AclUpdate: {
            /** Acl */
            acl: components["schemas"]["AclEntry"][];
        };
        /** ActiveTenantIn */
        ActiveTenantIn: {
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** AnnouncementBrief */
        AnnouncementBrief: {
            /** Body En */
            body_en: string;
            /** Body Te */
            body_te: string;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Severity */
            severity: string;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /** Title En */
            title_en: string;
            /** Title Te */
            title_te: string;
        };
        /** AnnouncementIn */
        AnnouncementIn: {
            /**
             * Audience
             * @default all
             * @enum {string}
             */
            audience: "all" | "tier" | "tenants";
            /** Audience Tenant Ids */
            audience_tenant_ids?: string[];
            /** Audience Tier */
            audience_tier?: ("shared" | "dedicated") | null;
            /** Body En */
            body_en: string;
            /** Body Te */
            body_te: string;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /**
             * Severity
             * @default info
             * @enum {string}
             */
            severity: "info" | "maintenance" | "warning" | "critical";
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /**
             * Status
             * @default scheduled
             * @enum {string}
             */
            status: "draft" | "scheduled";
            /** Title En */
            title_en: string;
            /** Title Te */
            title_te: string;
        };
        /** AnnouncementOut */
        AnnouncementOut: {
            /** Audience */
            audience: string;
            /** Audience Tenant Ids */
            audience_tenant_ids: string[];
            /** Audience Tier */
            audience_tier: string | null;
            /** Body En */
            body_en: string;
            /** Body Te */
            body_te: string;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Severity */
            severity: string;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /** Status */
            status: string;
            /** Title En */
            title_en: string;
            /** Title Te */
            title_te: string;
            /** Version */
            version: number;
        };
        /** AuditVerifyOut */
        app__audit__viewer__AuditVerifyOut: {
            /** Checked */
            checked: number;
            /** First Bad Seq */
            first_bad_seq: number | null;
            /** Ok */
            ok: boolean;
            /** Reason */
            reason: string | null;
        };
        /** Page[TicketOut] */
        app__authz__http__Page_TicketOut_: {
            /** Data */
            data: components["schemas"]["TicketOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** MeOut */
        app__identity__schemas__MeOut: {
            /** Display Name */
            display_name: string;
            /**
             * Membership Id
             * Format: uuid
             */
            membership_id: string;
            /** Mfa */
            mfa: boolean;
            /** Permissions */
            permissions: string[];
            /**
             * Preferred Language
             * @enum {string}
             */
            preferred_language: "en" | "te";
            /** Roles */
            roles: string[];
            /** Scopes */
            scopes: components["schemas"]["ScopeOut"][];
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /**
             * Tenant Ids
             * @description Schools this user can switch to (active memberships).
             */
            tenant_ids: string[];
            /**
             * Tenant Status
             * @description Status of the active school. While it is suspended or offboarding only the owner and principal can use SchoolOS, for Plan & billing (BR-08).
             * @default active
             * @enum {string}
             */
            tenant_status: "active" | "suspended" | "offboarding";
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /** AuditVerifyOut */
        app__platform__schemas__AuditVerifyOut: {
            /** Checked */
            checked: number;
            /** First Bad Seq */
            first_bad_seq: number | null;
            /**
             * Job Id
             * Format: uuid
             */
            job_id: string;
            /** Ok */
            ok: boolean;
            /** Reason */
            reason: string | null;
        };
        /** MeOut */
        app__platform__schemas__MeOut: {
            /**
             * Operator Id
             * Format: uuid
             */
            operator_id: string;
            /** Permissions */
            permissions: string[];
            /** Roles */
            roles: string[];
            /** Step Up Fresh */
            step_up_fresh: boolean;
        };
        /** Page[TicketOut] */
        app__platform__schemas__Page_TicketOut_: {
            /** Data */
            data: components["schemas"]["TicketOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** ApproveIn */
        ApproveIn: {
            /** Note */
            note?: string | null;
        };
        /** AttributeOut */
        AttributeOut: {
            /** Allowed Sources */
            allowed_sources: string[] | null;
            /** Allowed Values */
            allowed_values: string[] | null;
            /** Classification */
            classification: string;
            /** Data Type */
            data_type: string;
            /** Is Global */
            is_global: boolean;
            /** Is Identity */
            is_identity: boolean;
            /** Key */
            key: string;
            /** Label En */
            label_en: string;
            /** Label Te */
            label_te: string;
            /** Precedence */
            precedence: string[];
            /** Sort Order */
            sort_order: number;
        };
        /** AuditEventOut */
        AuditEventOut: {
            /** Action */
            action: string;
            /** Actor Id */
            actor_id: string | null;
            /** Actor Type */
            actor_type: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            /** Request Id */
            request_id: string | null;
            /** Resource Id */
            resource_id: string | null;
            /** Resource Type */
            resource_type: string;
            /** Seq */
            seq: number;
            /** Summary */
            summary: {
                [key: string]: unknown;
            };
        };
        /**
         * BatchCreate
         * @description Start extraction for register-page photos already uploaded as ``register_scan``
         *     documents (JPG or PNG, one page each, malware scan passed).
         */
        BatchCreate: {
            /** Document Ids */
            document_ids: string[];
        };
        /**
         * BatchDetail
         * @description A batch with progress per page (US-402 AC4).
         */
        BatchDetail: {
            /** Completed At */
            completed_at: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Error Code */
            error_code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Items Confirmed */
            items_confirmed: number;
            /** Items Low Confidence */
            items_low_confidence: number;
            /** Items Pending */
            items_pending: number;
            /** Items Rejected */
            items_rejected: number;
            /** Items Total */
            items_total: number;
            /** Page Count */
            page_count: number;
            /** Pages */
            pages: components["schemas"]["PageOut"][];
            /** Pages Done */
            pages_done: number;
            /** Pages Failed */
            pages_failed: number;
            /** Pages Withheld */
            pages_withheld: number;
            /** Processed At */
            processed_at: string | null;
            /** Provider */
            provider: string;
            /** Source */
            source: string;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "processing" | "review" | "completed" | "failed";
            /** Version */
            version: number;
        };
        /** BatchOut */
        BatchOut: {
            /** Completed At */
            completed_at: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Error Code */
            error_code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Items Confirmed */
            items_confirmed: number;
            /** Items Low Confidence */
            items_low_confidence: number;
            /** Items Pending */
            items_pending: number;
            /** Items Rejected */
            items_rejected: number;
            /** Items Total */
            items_total: number;
            /** Page Count */
            page_count: number;
            /** Pages Done */
            pages_done: number;
            /** Pages Failed */
            pages_failed: number;
            /** Pages Withheld */
            pages_withheld: number;
            /** Processed At */
            processed_at: string | null;
            /** Provider */
            provider: string;
            /** Source */
            source: string;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "processing" | "review" | "completed" | "failed";
            /** Version */
            version: number;
        };
        /** Bilingual */
        Bilingual: {
            /** Code */
            code: string;
            /** En */
            en: string;
            /** Te */
            te: string;
        };
        /** BillingAccountIn */
        BillingAccountIn: {
            /** Address Line1 */
            address_line1: string;
            /** Address Line2 */
            address_line2?: string | null;
            /** Billing Contact Name */
            billing_contact_name?: string | null;
            /** Billing Email */
            billing_email: string;
            /** Billing Phone */
            billing_phone?: string | null;
            /** City */
            city: string;
            /** District */
            district?: string | null;
            /** Gstin */
            gstin?: string | null;
            /** Legal Name */
            legal_name: string;
            /** Pan */
            pan?: string | null;
            /** Po Reference */
            po_reference?: string | null;
            /** Postal Code */
            postal_code: string;
            /**
             * State Code
             * @default 37
             */
            state_code: string;
        };
        /**
         * BillingAccountOut
         * @description School business contact (not student data; 08 §14).
         */
        BillingAccountOut: {
            /** Address Line1 */
            address_line1: string;
            /** Address Line2 */
            address_line2: string | null;
            /** Billing Contact Name */
            billing_contact_name: string | null;
            /** Billing Email */
            billing_email: string;
            /** Billing Phone */
            billing_phone: string | null;
            /** City */
            city: string;
            /** District */
            district: string | null;
            /** Gstin */
            gstin: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Legal Name */
            legal_name: string;
            /** Pan */
            pan: string | null;
            /** Po Reference */
            po_reference: string | null;
            /** Postal Code */
            postal_code: string;
            /** State Code */
            state_code: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Version */
            version: number;
        };
        /** BreakGlassIn */
        BreakGlassIn: {
            /** Duration Minutes */
            duration_minutes: number;
            /**
             * Emergency
             * @default false
             */
            emergency: boolean;
            /** Reason */
            reason: string;
            /**
             * Reason Code
             * @enum {string}
             */
            reason_code: "support_request" | "security_incident" | "legal_obligation";
            /** Scope */
            scope: {
                [key: string]: string;
            };
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** BreakGlassOut */
        BreakGlassOut: {
            /** Created At */
            created_at: string | null;
            /** Duration Minutes */
            duration_minutes: number;
            /** Emergency */
            emergency: boolean;
            /** Emergency Confirmed By 1 */
            emergency_confirmed_by_1: string | null;
            /** Emergency Confirmed By 2 */
            emergency_confirmed_by_2: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reason */
            reason: string;
            /** Reason Code */
            reason_code: string;
            /**
             * Requested By
             * Format: uuid
             */
            requested_by: string;
            /** Scope */
            scope: {
                [key: string]: unknown;
            };
            /** Status */
            status: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** CanonicalOut */
        CanonicalOut: {
            /** Conflicts */
            conflicts: string[];
            /** Masked */
            masked: boolean;
            /** Provisional */
            provisional: boolean;
            /** Source */
            source: string | null;
            /** Value */
            value: string | null;
            /** Verified */
            verified: boolean;
        };
        /** ChangePlanIn */
        ChangePlanIn: {
            /**
             * Plan Id
             * Format: uuid
             */
            plan_id: string;
        };
        /**
         * ChangeRequestCreate
         * @description Request a correction of one identity attribute from one source (FR-CR-001).
         */
        ChangeRequestCreate: {
            /** Attribute Key */
            attribute_key: string;
            /**
             * Evidence Document Id
             * Format: uuid
             */
            evidence_document_id: string;
            /** New Value */
            new_value?: string | null;
            /** New Value Date */
            new_value_date?: string | null;
            /** Reason */
            reason: string;
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
            /**
             * Target Source
             * @default admission_register
             * @enum {string}
             */
            target_source: "admission_register" | "aadhaar_as_printed" | "udise_plus" | "board_registration" | "birth_certificate" | "parent_form" | "tc_incoming" | "manual_entry";
        };
        /** ChangeRequestOut */
        ChangeRequestOut: {
            /** Applied Value Id */
            applied_value_id: string | null;
            /** Attribute Key */
            attribute_key: string;
            /** Attribute Label En */
            attribute_label_en: string;
            /** Attribute Label Te */
            attribute_label_te: string;
            /** Can Cancel */
            can_cancel: boolean;
            /** Can Decide */
            can_decide: boolean;
            /** Decided At */
            decided_at: string | null;
            /** Decided By */
            decided_by: string | null;
            /** Decision Note */
            decision_note: string | null;
            /**
             * Evidence Document Id
             * Format: uuid
             */
            evidence_document_id: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Masked */
            masked: boolean;
            /** New Value */
            new_value: string;
            /** Old Value */
            old_value: string | null;
            /** Old Value Id */
            old_value_id: string | null;
            /** Reason */
            reason: string;
            /**
             * Requested At
             * Format: date-time
             */
            requested_at: string;
            /**
             * Requested By
             * Format: uuid
             */
            requested_by: string;
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "approved" | "rejected" | "expired" | "cancelled";
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
            /** Target Source */
            target_source: string;
            /** Version */
            version: number;
        };
        /** ClassCreate */
        ClassCreate: {
            /** Code */
            code: string;
            /** Display En */
            display_en: string;
            /** Display Te */
            display_te: string;
            /** Sort Order */
            sort_order: number;
        };
        /** ClassOut */
        ClassOut: {
            /** Code */
            code: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Display En */
            display_en: string;
            /** Display Te */
            display_te: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Sort Order */
            sort_order: number;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /** ClassSection */
        ClassSection: {
            /**
             * Academic Year Id
             * Format: uuid
             */
            academic_year_id: string;
            /**
             * Class Id
             * Format: uuid
             */
            class_id: string;
            /** Label */
            label: string;
            /** Roll No */
            roll_no?: string | null;
            /**
             * Section Id
             * Format: uuid
             */
            section_id: string;
        };
        /**
         * ClassUpdate
         * @description The code is immutable (exports and registers refer to it).
         */
        ClassUpdate: {
            /** Display En */
            display_en?: string | null;
            /** Display Te */
            display_te?: string | null;
            /** Sort Order */
            sort_order?: number | null;
        };
        /** ColumnMap */
        ColumnMap: {
            /** Index */
            index: number;
            /** Target */
            target: string;
        };
        /** ColumnOut */
        ColumnOut: {
            /** Header */
            header: string;
            /** Index */
            index: number;
            /** Score */
            score: number;
            /** Suggested */
            suggested: string | null;
            /** Target */
            target: string | null;
        };
        /**
         * CommitIn
         * @description ``skip_error_rows``: commit only the valid rows (rows with errors are marked skipped).
         */
        CommitIn: {
            /**
             * Skip Error Rows
             * @default false
             */
            skip_error_rows: boolean;
        };
        /** DashboardOut */
        DashboardOut: {
            /** Ai Spend Mtd Inr */
            ai_spend_mtd_inr?: string | null;
            /** Ai Spend Top */
            ai_spend_top?: {
                [key: string]: unknown;
            }[] | null;
            /** Arr Inr */
            arr_inr?: string | null;
            /** Fleet By Status */
            fleet_by_status?: {
                [key: string]: number;
            } | null;
            /** Fleet Versions */
            fleet_versions?: {
                [key: string]: number;
            } | null;
            /** Mrr Inr */
            mrr_inr?: string | null;
            /** Oldest Overdue Due Date */
            oldest_overdue_due_date?: string | null;
            /** Open Tickets By Priority */
            open_tickets_by_priority?: {
                [key: string]: number;
            } | null;
            /** Past Due Amount Inr */
            past_due_amount_inr?: string | null;
            /** Past Due Count */
            past_due_count?: number | null;
            /** Schools By Status */
            schools_by_status?: {
                [key: string]: number;
            } | null;
            /** Schools By Tier */
            schools_by_tier?: {
                [key: string]: number;
            } | null;
            /** Tickets Sla Breached */
            tickets_sla_breached?: number | null;
            /** Trials Ending 14D */
            trials_ending_14d?: number | null;
            /** Trials Running */
            trials_running?: number | null;
        };
        /** DeploymentOut */
        DeploymentOut: {
            /** App Version */
            app_version: string | null;
            /** Backup Region */
            backup_region: string;
            /** Custom Domain */
            custom_domain: string | null;
            /** Heartbeat Key Id */
            heartbeat_key_id: string | null;
            /** Heartbeat Next Key Id */
            heartbeat_next_key_id: string | null;
            /** Host Ref */
            host_ref: string | null;
            /** Hostname */
            hostname: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Last Heartbeat At */
            last_heartbeat_at: string | null;
            /**
             * Mode
             * @enum {string}
             */
            mode: "shared" | "dedicated";
            /** Region */
            region: string;
            /** School Name */
            school_name: string;
            /** Status */
            status: string;
            /** Target Version */
            target_version: string | null;
            /** Tenant Code */
            tenant_code: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /**
             * Tenant Status
             * @enum {string}
             */
            tenant_status: "provisioning" | "active" | "suspended" | "offboarding" | "deleted";
            /** Version */
            version: number;
        };
        /** DeploymentPatch */
        DeploymentPatch: {
            /** Custom Domain */
            custom_domain?: string | null;
            /** Host Ref */
            host_ref?: string | null;
            /** Hostname */
            hostname?: string | null;
            /** Target Version */
            target_version?: string | null;
        };
        /**
         * DocumentCreate
         * @description Register an uploaded object as a new document (metadata FR-DOC-005 + ACL).
         */
        DocumentCreate: {
            /** Academic Year Id */
            academic_year_id?: string | null;
            /** Acl */
            acl?: components["schemas"]["AclEntry"][];
            /** Doc Type */
            doc_type?: ("circular" | "policy" | "minutes" | "register_scan" | "certificate" | "letter" | "form" | "report" | "verified_answer" | "other" | "evidence" | "import_file") | null;
            /** Issued On */
            issued_on?: string | null;
            /** Issuer */
            issuer?: string | null;
            /** Language */
            language?: ("en" | "te" | "mixed") | null;
            /** Sensitivity */
            sensitivity?: ("C1" | "C2" | "C3") | null;
            /** Title */
            title: string;
            /**
             * Upload Id
             * Format: uuid
             */
            upload_id: string;
        };
        /** DocumentDetail */
        DocumentDetail: {
            /** Academic Year Id */
            academic_year_id: string | null;
            /** Acl */
            acl: components["schemas"]["AclEntryOut"][];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            current_version: components["schemas"]["VersionOut"] | null;
            /**
             * Doc Type
             * @enum {string}
             */
            doc_type: "circular" | "policy" | "minutes" | "register_scan" | "certificate" | "letter" | "form" | "report" | "verified_answer" | "other" | "evidence" | "import_file";
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Issued On */
            issued_on: string | null;
            /** Issuer */
            issuer: string | null;
            /** Language */
            language: ("en" | "te" | "mixed") | null;
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "evidence" | "register_scan" | "circular" | "policy" | "other" | "import_file";
            /**
             * Sensitivity
             * @enum {string}
             */
            sensitivity: "C1" | "C2" | "C3";
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "archived";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
            /** Versions */
            versions: components["schemas"]["VersionOut"][];
        };
        /** DocumentOut */
        DocumentOut: {
            /** Academic Year Id */
            academic_year_id: string | null;
            /** Acl */
            acl: components["schemas"]["AclEntryOut"][];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            current_version: components["schemas"]["VersionOut"] | null;
            /**
             * Doc Type
             * @enum {string}
             */
            doc_type: "circular" | "policy" | "minutes" | "register_scan" | "certificate" | "letter" | "form" | "report" | "verified_answer" | "other" | "evidence" | "import_file";
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Issued On */
            issued_on: string | null;
            /** Issuer */
            issuer: string | null;
            /** Language */
            language: ("en" | "te" | "mixed") | null;
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "evidence" | "register_scan" | "circular" | "policy" | "other" | "import_file";
            /**
             * Sensitivity
             * @enum {string}
             */
            sensitivity: "C1" | "C2" | "C3";
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "archived";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /** DownloadUrlOut */
        DownloadUrlOut: {
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Filename */
            filename: string;
            /** Mime Type */
            mime_type: string;
            /** Url */
            url: string;
            /** Version No */
            version_no: number;
        };
        /**
         * EnrollmentEnd
         * @description Close an active enrolment: ``completed`` (year finished, left the school) or
         *     ``transferred`` (moved elsewhere). ``ended_on`` defaults to today (India time).
         */
        EnrollmentEnd: {
            /** Ended On */
            ended_on?: string | null;
            /**
             * Status
             * @default completed
             * @enum {string}
             */
            status: "completed" | "transferred";
        };
        /**
         * EnrollmentIn
         * @description Enrol in a section; an active enrolment in the same academic year becomes ``transferred``.
         */
        EnrollmentIn: {
            /** Roll No */
            roll_no?: string | null;
            /**
             * Section Id
             * Format: uuid
             */
            section_id: string;
            /** Started On */
            started_on?: string | null;
        };
        /** EnrollmentOut */
        EnrollmentOut: {
            /**
             * Academic Year Id
             * Format: uuid
             */
            academic_year_id: string;
            /** Ended On */
            ended_on: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Roll No */
            roll_no: string | null;
            /**
             * Section Id
             * Format: uuid
             */
            section_id: string;
            /** Started On */
            started_on: string | null;
            /** Status */
            status: string;
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
            /** Version */
            version: number;
        };
        /**
         * EnrollmentPatch
         * @description Correct an enrolment: roll number (``null`` clears it) and/or the section, which must be
         *     another section of the same class in the same academic year (an active enrolment only).
         *     Omit a field to keep it. Moving to another class is a new enrolment, not a correction.
         */
        EnrollmentPatch: {
            /** Roll No */
            roll_no?: string | null;
            /** Section Id */
            section_id?: string | null;
        };
        /** ExportDownloadOut */
        ExportDownloadOut: {
            /** Content Type */
            content_type: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Filename */
            filename: string;
            /**
             * Format
             * @enum {string}
             */
            format: "xlsx" | "pdf" | "csv";
            /** Url */
            url: string;
        };
        /** ExportFileOut */
        ExportFileOut: {
            /** Content Type */
            content_type: string;
            /**
             * Format
             * @enum {string}
             */
            format: "xlsx" | "pdf" | "csv";
            /** Size Bytes */
            size_bytes: number;
        };
        /** ExportOut */
        ExportOut: {
            /** Can Download */
            can_download: boolean;
            /** Columns */
            columns: string[] | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error Code */
            error_code: string | null;
            /** Expires At */
            expires_at: string | null;
            /** Files */
            files: components["schemas"]["ExportFileOut"][];
            /** Finished At */
            finished_at: string | null;
            /** Formats */
            formats: ("xlsx" | "pdf" | "csv")[];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Include Sensitive */
            include_sensitive: boolean;
            /**
             * Kind
             * @enum {string}
             */
            kind: "board_precheck" | "portal_precheck" | "student_list";
            /**
             * Language
             * @enum {string}
             */
            language: "en" | "te";
            /** Layout Version */
            layout_version: number;
            /** Own */
            own: boolean;
            /** Profile Key */
            profile_key: string | null;
            /** Profile Version */
            profile_version: number | null;
            requested_by: components["schemas"]["ExportRequesterOut"];
            /** Scope */
            scope: {
                [key: string]: string[];
            };
            /** Started At */
            started_at: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "running" | "ready" | "failed" | "expired";
            /** Student Count */
            student_count: number;
        };
        /** ExportProfileOut */
        ExportProfileOut: {
            /** Allowed */
            allowed: boolean;
            /** Fields */
            fields: string[];
            /** Key */
            key: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "board" | "portal";
            /** Label En */
            label_en: string;
            /** Label Te */
            label_te: string;
            /** Layout Version */
            layout_version: number;
            /** Permission */
            permission: string;
            /** Required Fields */
            required_fields: string[];
            /** Version */
            version: number;
        };
        /**
         * ExportRequesterOut
         * @description Who requested an export: the staff member's membership id and display name (``null``
         *     when the account is no longer visible). Nothing else about the person (ADR-0021).
         */
        ExportRequesterOut: {
            /** Display Name */
            display_name: string | null;
            /**
             * Membership Id
             * Format: uuid
             */
            membership_id: string;
        };
        /**
         * ExportScopeIn
         * @description Sections or classes of the current academic year (at most one of the two); empty = every
         *     student you can see.
         */
        ExportScopeIn: {
            /** Class Ids */
            class_ids?: string[] | null;
            /** Section Ids */
            section_ids?: string[] | null;
        };
        /** ExtendTrialIn */
        ExtendTrialIn: {
            /**
             * Trial Ends At
             * Format: date-time
             */
            trial_ends_at: string;
        };
        /** FieldOut */
        FieldOut: {
            /** Bbox */
            bbox: number[] | null;
            /** Confidence */
            confidence: number | null;
            /** Low Confidence */
            low_confidence: boolean;
            /** Masked */
            masked: boolean;
            /** Value */
            value: string;
        };
        /** FindingOut */
        FindingOut: {
            /** Attribute Key */
            attribute_key: string | null;
            /** Blocker */
            blocker: boolean;
            /** Change Request Id */
            change_request_id: string | null;
            /** Details */
            details: {
                [key: string]: unknown;
            };
            explanation: components["schemas"]["Bilingual"];
            /**
             * First Seen At
             * Format: date-time
             */
            first_seen_at: string;
            /** First Seen Run Id */
            first_seen_run_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Last Seen At
             * Format: date-time
             */
            last_seen_at: string;
            /** Last Seen Run Id */
            last_seen_run_id: string | null;
            /** Match Class */
            match_class: string | null;
            match_explanation: components["schemas"]["Bilingual"] | null;
            /** Profile Key */
            profile_key: string | null;
            /** Related Student Id */
            related_student_id: string | null;
            /** Reopened Count */
            reopened_count: number;
            /** Resolution */
            resolution: ("note" | "change_request" | "auto_cleared") | null;
            /** Resolution Note */
            resolution_note: string | null;
            /** Resolved At */
            resolved_at: string | null;
            /** Resolved By */
            resolved_by: string | null;
            /** Routes */
            routes: components["schemas"]["Bilingual"][];
            /** Rule Id */
            rule_id: string;
            /** Rule Version */
            rule_version: number;
            /**
             * Severity
             * @enum {string}
             */
            severity: "blocker" | "high" | "medium" | "low" | "info";
            /** Sources */
            sources: string[];
            /**
             * Status
             * @enum {string}
             */
            status: "open" | "resolved" | "waived" | "reopened";
            student: components["schemas"]["StudentRef"];
            /** Values */
            values: components["schemas"]["FindingValue"][];
            /** Version */
            version: number;
            /** Waived At */
            waived_at: string | null;
            /** Waived By */
            waived_by: string | null;
            /** Waived Reason */
            waived_reason: string | null;
        };
        /**
         * FindingValue
         * @description A compared value: always masked; ``value`` only for non-sensitive (C2) values that are
         *     still current (C3 values are revealed on the student record, with an audit event).
         */
        FindingValue: {
            /** Attribute Key */
            attribute_key: string;
            /** Masked */
            masked: string | null;
            /** Sensitive */
            sensitive: boolean;
            /** Source */
            source: string;
            /** Value */
            value: string | null;
            /**
             * Value Id
             * Format: uuid
             */
            value_id: string;
        };
        /** FlagIn */
        FlagIn: {
            /** Description */
            description?: string | null;
            /** Enabled */
            enabled: boolean;
            /** Rollout Percent */
            rollout_percent?: number | null;
        };
        /** FlagOut */
        FlagOut: {
            /** Description */
            description: string | null;
            /** Enabled */
            enabled: boolean;
            /** Key */
            key: string;
            /** Rollout Percent */
            rollout_percent: number | null;
            /** Tenant Id */
            tenant_id: string | null;
            /** Updated At */
            updated_at: string | null;
            /** Version */
            version: number;
        };
        /** FlagOverrideIn */
        FlagOverrideIn: {
            /** Enabled */
            enabled: boolean;
        };
        /** FleetVersionOut */
        FleetVersionOut: {
            /** Deployments */
            deployments: number;
            /** Version */
            version: string;
        };
        /**
         * GrantOut
         * @description A support-access request and, once decided, its grant.
         *
         *     ``status``: requested (waiting for the school) · approved (emergency access confirmed by two
         *     SchoolOS staff, not yet usable) · active (support can read until ``expires_at``) · expired ·
         *     revoked (ended by the school) · denied. ``operator_display_name`` is the SchoolOS employee
         *     who asked; ``membership_id`` is their temporary access in this school.
         */
        GrantOut: {
            /** Approved By Membership */
            approved_by_membership: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Decided At */
            decided_at: string | null;
            /** Denied By Membership */
            denied_by_membership: string | null;
            /** Duration Minutes */
            duration_minutes: number | null;
            /** Emergency */
            emergency: boolean;
            /** Expires At */
            expires_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Membership Id */
            membership_id: string | null;
            /** Operator Display Name */
            operator_display_name: string | null;
            /** Platform Request Id */
            platform_request_id: string | null;
            /** Reason */
            reason: string;
            /** Reason Code */
            reason_code: string;
            /** Requested At */
            requested_at: string | null;
            /** Revoked At */
            revoked_at: string | null;
            /** Revoked By Membership */
            revoked_by_membership: string | null;
            /** Scope */
            scope: {
                [key: string]: unknown;
            };
            /** Starts At */
            starts_at: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "requested" | "approved" | "active" | "expired" | "revoked" | "denied";
        };
        /**
         * GuardianCreate
         * @description Add a new guardian (``full_name`` ...) or link an existing one (``guardian_id``).
         */
        GuardianCreate: {
            /** Address */
            address?: string | null;
            /** Full Name */
            full_name?: string | null;
            /** Guardian Id */
            guardian_id?: string | null;
            /**
             * Is Primary
             * @default false
             */
            is_primary: boolean;
            /** Phone */
            phone?: string | null;
            /**
             * Relationship
             * @enum {string}
             */
            relationship: "father" | "mother" | "guardian";
        };
        /** GuardianOut */
        GuardianOut: {
            /** Address */
            address: string | null;
            /** Full Name */
            full_name: string;
            /** Has Address */
            has_address: boolean;
            /** Has Phone */
            has_phone: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Primary */
            is_primary: boolean;
            /** Masked */
            masked: boolean;
            /** Phone */
            phone: string | null;
            /** Relationship */
            relationship: string;
            /** Version */
            version: number;
        };
        /**
         * GuardianPatch
         * @description Omit a field to keep it; ``phone``/``address`` ``null`` clears it.
         */
        GuardianPatch: {
            /** Address */
            address?: string | null;
            /** Full Name */
            full_name?: string | null;
            /** Is Primary */
            is_primary?: boolean | null;
            /** Phone */
            phone?: string | null;
            /** Relationship */
            relationship?: ("father" | "mother" | "guardian") | null;
        };
        /** HealthOut */
        HealthOut: {
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** HeartbeatKeyOut */
        HeartbeatKeyOut: {
            /**
             * Deployment Id
             * Format: uuid
             */
            deployment_id: string;
            /** Heartbeat Key */
            heartbeat_key: string;
            /** Heartbeat Key Id */
            heartbeat_key_id: string;
        };
        /** HeartbeatOut */
        HeartbeatOut: {
            /** Announcements */
            announcements: components["schemas"]["AnnouncementBrief"][];
            /** Min Supported Version */
            min_supported_version: string;
            /**
             * Received At
             * Format: date-time
             */
            received_at: string;
            /** Target Version */
            target_version: string | null;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * ImportCreate
         * @description Start importing an uploaded file (purpose ``import_file``, scanned) from one source.
         */
        ImportCreate: {
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /**
             * Kind
             * @default spreadsheet
             * @constant
             */
            kind: "spreadsheet";
            /**
             * Source
             * @enum {string}
             */
            source: "admission_register" | "aadhaar_as_printed" | "udise_plus" | "board_registration" | "birth_certificate" | "parent_form" | "tc_incoming" | "manual_entry";
        };
        /** ImportOut */
        ImportOut: {
            /** Can Commit */
            can_commit: boolean;
            /** Can Revert */
            can_revert: boolean;
            /** Columns */
            columns: components["schemas"]["ColumnOut"][];
            /** Committed At */
            committed_at: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Document Id */
            document_id: string | null;
            /** Error Code */
            error_code: string | null;
            /** Error Count */
            error_count: number;
            /** File Kind */
            file_kind: string | null;
            /** Header Row */
            header_row: number | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Job Id */
            job_id: string | null;
            /** Kind */
            kind: string;
            /** Mapping Template Id */
            mapping_template_id: string | null;
            /** Raw File Deleted At */
            raw_file_deleted_at: string | null;
            /** Revert Deadline */
            revert_deadline: string | null;
            /** Reverted At */
            reverted_at: string | null;
            /** Row Count */
            row_count: number;
            /** Source */
            source: string;
            /** Stats */
            stats: {
                [key: string]: number;
            };
            /**
             * Status
             * @enum {string}
             */
            status: "uploaded" | "parsing" | "parsed" | "validating" | "validated" | "committing" | "committed" | "reverting" | "reverted" | "failed";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /**
         * ImportRowOut
         * @description One row: non-C3 values as they will be recorded, and the C3 keys present (never values).
         */
        ImportRowOut: {
            /** Action */
            action: ("create" | "update") | null;
            /** Admission No */
            admission_no: string | null;
            /** Class Section */
            class_section: string | null;
            /** Errors */
            errors: components["schemas"]["Issue"][];
            /** Roll No */
            roll_no: string | null;
            /** Row No */
            row_no: number;
            /** Section Id */
            section_id: string | null;
            /** Sensitive */
            sensitive: string[];
            /**
             * Status
             * @enum {string}
             */
            status: "valid" | "error" | "committed" | "skipped" | "reverted";
            /** Student Id */
            student_id: string | null;
            /** Values */
            values: {
                [key: string]: string;
            };
            /** Warnings */
            warnings: components["schemas"]["Issue"][];
        };
        /** ImportSummary */
        ImportSummary: {
            /** Committed At */
            committed_at: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Error Count */
            error_count: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reverted At */
            reverted_at: string | null;
            /** Row Count */
            row_count: number;
            /** Source */
            source: string;
            /**
             * Status
             * @enum {string}
             */
            status: "uploaded" | "parsing" | "parsed" | "validating" | "validated" | "committing" | "committed" | "reverting" | "reverted" | "failed";
        };
        /**
         * InviteIn
         * @description Invite a staff member. ``idp_subject`` is the identity provider's ``sub`` of the account
         *     created for them (admin-created username; docs/07 §5.1).
         */
        InviteIn: {
            /** Display Name */
            display_name: string;
            /** Email */
            email?: string | null;
            /** Idp Subject */
            idp_subject: string;
            /**
             * Preferred Language
             * @default en
             * @enum {string}
             */
            preferred_language: "en" | "te";
            /** Roles */
            roles: string[];
            /** Scopes */
            scopes?: components["schemas"]["ScopeIn"][];
        };
        /** InvoiceCreate */
        InvoiceCreate: {
            /**
             * Period Start
             * Format: date
             */
            period_start: string;
            /**
             * Subscription Id
             * Format: uuid
             */
            subscription_id: string;
        };
        /** InvoiceLineIn */
        InvoiceLineIn: {
            /** Description */
            description: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "subscription" | "per_student" | "addon" | "usage_overage" | "discount" | "adjustment";
            /**
             * Quantity
             * @default 1
             */
            quantity: number | string;
            /** Unit Price Inr */
            unit_price_inr: number | string;
        };
        /** InvoiceLineOut */
        InvoiceLineOut: {
            /** Amount Inr */
            amount_inr: string;
            /** Description */
            description: string;
            /** Gst Rate */
            gst_rate: string;
            /** Kind */
            kind: string;
            /** Line No */
            line_no: number;
            /** Quantity */
            quantity: string;
            /** Sac Code */
            sac_code: string;
            /** Unit Price Inr */
            unit_price_inr: string;
        };
        /** InvoiceOut */
        InvoiceOut: {
            /** Amount Paid Inr */
            amount_paid_inr: string;
            /** Balance Due Inr */
            balance_due_inr: string;
            /** Cgst Inr */
            cgst_inr: string;
            /** Due Date */
            due_date: string | null;
            /** Financial Year */
            financial_year: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Igst Inr */
            igst_inr: string;
            /** Invoice Number */
            invoice_number: string | null;
            /** Issue Date */
            issue_date: string | null;
            /** Lines */
            lines?: components["schemas"]["InvoiceLineOut"][];
            /** Notes */
            notes: string | null;
            /**
             * Period End
             * Format: date
             */
            period_end: string;
            /**
             * Period Start
             * Format: date
             */
            period_start: string;
            /** Place Of Supply State Code */
            place_of_supply_state_code: string;
            /** Recipient Gstin */
            recipient_gstin: string | null;
            /** Recipient Legal Name */
            recipient_legal_name: string;
            /** Sgst Inr */
            sgst_inr: string;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "issued" | "paid" | "void";
            /**
             * Subscription Id
             * Format: uuid
             */
            subscription_id: string;
            /** Supplier Gstin */
            supplier_gstin: string;
            /** Supplier Legal Name */
            supplier_legal_name: string;
            /** Supplier State Code */
            supplier_state_code: string;
            /**
             * Tax Type
             * @enum {string}
             */
            tax_type: "cgst_sgst" | "igst";
            /** Taxable Value Inr */
            taxable_value_inr: string;
            /** Tds Inr */
            tds_inr: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Total Inr */
            total_inr: string;
            /** Version */
            version: number;
            /** Void Reason */
            void_reason: string | null;
        };
        /** InvoicePatch */
        InvoicePatch: {
            /** Lines */
            lines?: components["schemas"]["InvoiceLineIn"][] | null;
            /** Notes */
            notes?: string | null;
        };
        /** InvoiceRunIn */
        InvoiceRunIn: {
            /** Month */
            month: string;
        };
        /** Issue */
        Issue: {
            /** Code */
            code: string;
            /** Field */
            field: string;
            /** Message Key */
            message_key: string;
            /** Ref */
            ref?: string | null;
        };
        /**
         * ItemConfirm
         * @description The values a person read on the page (edited where the machine was wrong).
         *
         *     Keys are register fields (``admission_no``, ``full_name``, ``dob`` (YYYY-MM-DD), ``gender``,
         *     ``father_name``, ``mother_name``, ``admission_date``, ``mother_tongue``, ``nationality``); an
         *     empty or ``null`` value is not recorded. Send ``student_id`` to add the values to an
         *     existing student; otherwise a new student is created (``full_name`` required), optionally
         *     enrolled in ``section_id``.
         */
        ItemConfirm: {
            /** Fields */
            fields: {
                [key: string]: string | null;
            };
            /** Roll No */
            roll_no?: string | null;
            /** Section Id */
            section_id?: string | null;
            /** Student Id */
            student_id?: string | null;
            /**
             * Student Status
             * @default active
             * @enum {string}
             */
            student_status: "provisional" | "active" | "left" | "graduated";
        };
        /**
         * ItemDetail
         * @description One row for review with its page image beside it (US-402 AC1).
         *
         *     ``image`` is a presigned link valid for 5 minutes, or ``null`` with ``image_unavailable``:
         *     ``withheld_sensitive_number`` (the page showed a full Aadhaar number, PRV-016),
         *     ``not_visible`` (you may not open the document) or ``not_ready``. ``possible_matches`` are
         *     students of this school with the same admission number (link instead of creating twice).
         */
        ItemDetail: {
            /**
             * Batch Id
             * Format: uuid
             */
            batch_id: string;
            /** Corrected Fields */
            corrected_fields: string[];
            /** Created Student */
            created_student: boolean;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Fields */
            fields: {
                [key: string]: components["schemas"]["FieldOut"];
            };
            /**
             * Id
             * Format: uuid
             */
            id: string;
            image: components["schemas"]["PageImage"] | null;
            /** Image Unavailable */
            image_unavailable: ("withheld_sensitive_number" | "not_visible" | "not_ready") | null;
            /** Low Confidence */
            low_confidence: boolean;
            /** Low Confidence Fields */
            low_confidence_fields: string[];
            /** Masked */
            masked: boolean;
            /**
             * Page Id
             * Format: uuid
             */
            page_id: string;
            /** Page No */
            page_no: number;
            /** Possible Matches */
            possible_matches: components["schemas"]["StudentSummary"][];
            /** Reject Reason */
            reject_reason: ("not_a_student_row" | "duplicate" | "unreadable" | "other") | null;
            /** Reviewed At */
            reviewed_at: string | null;
            /** Reviewed By */
            reviewed_by: string | null;
            /** Row Index */
            row_index: number;
            /**
             * Status
             * @enum {string}
             */
            status: "pending_review" | "confirmed" | "rejected";
            /** Student Id */
            student_id: string | null;
            /** Value Ids */
            value_ids: string[];
            /** Version */
            version: number;
        };
        /** ItemOut */
        ItemOut: {
            /**
             * Batch Id
             * Format: uuid
             */
            batch_id: string;
            /** Corrected Fields */
            corrected_fields: string[];
            /** Created Student */
            created_student: boolean;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Fields */
            fields: {
                [key: string]: components["schemas"]["FieldOut"];
            };
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Low Confidence */
            low_confidence: boolean;
            /** Low Confidence Fields */
            low_confidence_fields: string[];
            /** Masked */
            masked: boolean;
            /**
             * Page Id
             * Format: uuid
             */
            page_id: string;
            /** Page No */
            page_no: number;
            /** Reject Reason */
            reject_reason: ("not_a_student_row" | "duplicate" | "unreadable" | "other") | null;
            /** Reviewed At */
            reviewed_at: string | null;
            /** Reviewed By */
            reviewed_by: string | null;
            /** Row Index */
            row_index: number;
            /**
             * Status
             * @enum {string}
             */
            status: "pending_review" | "confirmed" | "rejected";
            /** Student Id */
            student_id: string | null;
            /** Value Ids */
            value_ids: string[];
            /** Version */
            version: number;
        };
        /** ItemReject */
        ItemReject: {
            /**
             * Reason
             * @enum {string}
             */
            reason: "not_a_student_row" | "duplicate" | "unreadable" | "other";
        };
        /** JobOut */
        JobOut: {
            /** Error */
            error: string | null;
            /** Finished At */
            finished_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Idempotency Key */
            idempotency_key: string;
            /** Progress */
            progress: {
                [key: string]: unknown;
            } | null;
            /** Started At */
            started_at: string | null;
            /** Status */
            status: string;
            /** Task Name */
            task_name: string;
        };
        /** LoginEventOut */
        LoginEventOut: {
            /** Recorded */
            recorded: boolean;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /**
         * MappingIn
         * @description The full column mapping (unlisted columns are not imported).
         */
        MappingIn: {
            /** Columns */
            columns: components["schemas"]["ColumnMap"][];
        };
        /** MarkedReadOut */
        MarkedReadOut: {
            /** Updated */
            updated: number;
        };
        /**
         * MembershipStatusIn
         * @description Activate, suspend or remove a staff member's access to this school.
         */
        MembershipStatusIn: {
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "suspended" | "removed";
        };
        /**
         * NotificationOut
         * @description One notification, rendered in the reader's language (``Accept-Language``: en or te).
         */
        NotificationOut: {
            /** Body */
            body: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Language
             * @enum {string}
             */
            language: "en" | "te";
            /** Params */
            params: {
                [key: string]: unknown;
            };
            /** Read At */
            read_at: string | null;
            /** Resource Id */
            resource_id: string | null;
            /** Resource Type */
            resource_type: string | null;
            /** Template Key */
            template_key: string;
            /** Title */
            title: string;
        };
        /** OffboardRequestIn */
        OffboardRequestIn: {
            /** Reason */
            reason: string;
        };
        /** OperatorInvite */
        OperatorInvite: {
            /** Display Name */
            display_name: string;
            /** Email */
            email: string;
            /** Idp Subject */
            idp_subject: string;
            /** Roles */
            roles: ("platform_owner" | "platform_engineer" | "support_agent" | "billing_admin" | "platform_viewer")[];
        };
        /** OperatorOut */
        OperatorOut: {
            /** Created At */
            created_at: string | null;
            /** Display Name */
            display_name: string;
            /** Email */
            email: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Last Login At */
            last_login_at: string | null;
            /** Mfa Enrolled */
            mfa_enrolled: boolean;
            /** Roles */
            roles: ("platform_owner" | "platform_engineer" | "support_agent" | "billing_admin" | "platform_viewer")[];
            /**
             * Status
             * @enum {string}
             */
            status: "invited" | "active" | "deactivated";
        };
        /** OperatorRolesIn */
        OperatorRolesIn: {
            /** Roles */
            roles: ("platform_owner" | "platform_engineer" | "support_agent" | "billing_admin" | "platform_viewer")[];
        };
        /** OwnerIn */
        OwnerIn: {
            /** Display Name */
            display_name: string;
            /** Email */
            email?: string | null;
            /** Idp Subject */
            idp_subject: string;
            /**
             * Language
             * @default en
             * @enum {string}
             */
            language: "en" | "te";
        };
        /** Page[AcademicYearOut] */
        Page_AcademicYearOut_: {
            /** Data */
            data: components["schemas"]["AcademicYearOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[AnnouncementOut] */
        Page_AnnouncementOut_: {
            /** Data */
            data: components["schemas"]["AnnouncementOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[AuditEventOut] */
        Page_AuditEventOut_: {
            /** Data */
            data: components["schemas"]["AuditEventOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[BatchOut] */
        Page_BatchOut_: {
            /** Data */
            data: components["schemas"]["BatchOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[BreakGlassOut] */
        Page_BreakGlassOut_: {
            /** Data */
            data: components["schemas"]["BreakGlassOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[ChangeRequestOut] */
        Page_ChangeRequestOut_: {
            /** Data */
            data: components["schemas"]["ChangeRequestOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[ClassOut] */
        Page_ClassOut_: {
            /** Data */
            data: components["schemas"]["ClassOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[DeploymentOut] */
        Page_DeploymentOut_: {
            /** Data */
            data: components["schemas"]["DeploymentOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[DocumentOut] */
        Page_DocumentOut_: {
            /** Data */
            data: components["schemas"]["DocumentOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[ExportOut] */
        Page_ExportOut_: {
            /** Data */
            data: components["schemas"]["ExportOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[FindingOut] */
        Page_FindingOut_: {
            /** Data */
            data: components["schemas"]["FindingOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[FlagOut] */
        Page_FlagOut_: {
            /** Data */
            data: components["schemas"]["FlagOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[GrantOut] */
        Page_GrantOut_: {
            /** Data */
            data: components["schemas"]["GrantOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[ImportRowOut] */
        Page_ImportRowOut_: {
            /** Data */
            data: components["schemas"]["ImportRowOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[ImportSummary] */
        Page_ImportSummary_: {
            /** Data */
            data: components["schemas"]["ImportSummary"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[InvoiceOut] */
        Page_InvoiceOut_: {
            /** Data */
            data: components["schemas"]["InvoiceOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[ItemOut] */
        Page_ItemOut_: {
            /** Data */
            data: components["schemas"]["ItemOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[NotificationOut] */
        Page_NotificationOut_: {
            /** Data */
            data: components["schemas"]["NotificationOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[OperatorOut] */
        Page_OperatorOut_: {
            /** Data */
            data: components["schemas"]["OperatorOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[PermissionOut] */
        Page_PermissionOut_: {
            /** Data */
            data: components["schemas"]["PermissionOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[PlanOut] */
        Page_PlanOut_: {
            /** Data */
            data: components["schemas"]["PlanOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[PlatformAuditEventOut] */
        Page_PlatformAuditEventOut_: {
            /** Data */
            data: components["schemas"]["PlatformAuditEventOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[RoleOut] */
        Page_RoleOut_: {
            /** Data */
            data: components["schemas"]["RoleOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[SectionOut] */
        Page_SectionOut_: {
            /** Data */
            data: components["schemas"]["SectionOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[StudentSummary] */
        Page_StudentSummary_: {
            /** Data */
            data: components["schemas"]["StudentSummary"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[SubscriptionOut] */
        Page_SubscriptionOut_: {
            /** Data */
            data: components["schemas"]["SubscriptionOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[TenantInvoice] */
        Page_TenantInvoice_: {
            /** Data */
            data: components["schemas"]["TenantInvoice"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[TenantSummaryOut] */
        Page_TenantSummaryOut_: {
            /** Data */
            data: components["schemas"]["TenantSummaryOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** Page[UserOut] */
        Page_UserOut_: {
            /** Data */
            data: components["schemas"]["UserOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** PageImage */
        PageImage: {
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Mime Type */
            mime_type: string;
            /** Url */
            url: string;
        };
        /**
         * PageOut
         * @description One register page. ``aadhaar_detected``: its text showed a full Aadhaar number (PRV-016);
         *     the image is then either ``image_redacted`` (number blacked out; ``document_version_no`` is
         *     the redacted copy) or ``image_withheld`` (could not be redacted: the original was discarded
         *     and the page's rows cannot be confirmed).
         */
        PageOut: {
            /** Aadhaar Detected */
            aadhaar_detected: boolean;
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Document Version No */
            document_version_no: number;
            /** Error Code */
            error_code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Image Redacted */
            image_redacted: boolean;
            /** Image Withheld */
            image_withheld: boolean;
            /** Low Confidence Count */
            low_confidence_count: number;
            /** Page No */
            page_no: number;
            /** Processed At */
            processed_at: string | null;
            /** Row Count */
            row_count: number;
            /** Seq */
            seq: number;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "done" | "failed";
        };
        /** PaymentIn */
        PaymentIn: {
            /** Amount Inr */
            amount_inr: number | string;
            /**
             * Method
             * @enum {string}
             */
            method: "bank_transfer" | "upi" | "cheque" | "other";
            /** Notes */
            notes?: string | null;
            /**
             * Received On
             * Format: date
             */
            received_on: string;
            /** Reference */
            reference: string;
            /**
             * Tds Inr
             * @default 0
             */
            tds_inr: number | string;
        };
        /** PaymentOut */
        PaymentOut: {
            /** Amount Inr */
            amount_inr: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Invoice Id
             * Format: uuid
             */
            invoice_id: string;
            /** Method */
            method: string;
            /** Provider */
            provider: string;
            /**
             * Received On
             * Format: date
             */
            received_on: string;
            /** Recorded At */
            recorded_at: string | null;
            /** Reference */
            reference: string;
            /**
             * Status
             * @enum {string}
             */
            status: "recorded" | "reversed";
            /** Tds Inr */
            tds_inr: string;
        };
        /** PermissionOut */
        PermissionOut: {
            /** Description */
            description: string;
            /** Key */
            key: string;
            /** Sensitivity */
            sensitivity: string;
            /** Step Up */
            step_up: boolean;
        };
        /** PlanIn */
        PlanIn: {
            /** Base Price Inr */
            base_price_inr: number | string;
            /**
             * Billing Period
             * @default monthly
             * @enum {string}
             */
            billing_period: "monthly" | "annual";
            /** Code */
            code: string;
            /** Features */
            features?: {
                [key: string]: boolean;
            };
            /**
             * Gst Rate
             * @default 18
             * @enum {string}
             */
            gst_rate: "0" | "5" | "12" | "18" | "28";
            /** Included Students */
            included_students?: number | null;
            limits?: components["schemas"]["PlanLimits"];
            /** Name */
            name: string;
            /** Per Student Price Inr */
            per_student_price_inr?: number | string | null;
            /**
             * Pricing Model
             * @default flat
             * @enum {string}
             */
            pricing_model: "flat" | "per_student";
            /** Sac Code */
            sac_code?: string | null;
            /**
             * Tier
             * @default shared
             * @enum {string}
             */
            tier: "shared" | "dedicated";
            /**
             * Trial Days
             * @default 30
             */
            trial_days: number;
        };
        /** PlanLimits */
        PlanLimits: {
            /** Ai Budget Inr */
            ai_budget_inr?: number | string | null;
            /** Ai Tokens Month */
            ai_tokens_month?: number | null;
            /** Documents */
            documents?: number | null;
            /** Staff Users */
            staff_users?: number | null;
            /** Storage Gb */
            storage_gb?: number | null;
            /** Students */
            students?: number | null;
        };
        /** PlanOut */
        PlanOut: {
            /** Base Price Inr */
            base_price_inr: string;
            /** Billing Period */
            billing_period: string;
            /** Code */
            code: string;
            /** Created At */
            created_at: string | null;
            /** Features */
            features: {
                [key: string]: unknown;
            };
            /** Gst Rate */
            gst_rate: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Included Students */
            included_students: number | null;
            /** Limits */
            limits: {
                [key: string]: unknown;
            };
            /** Name */
            name: string;
            /** Per Student Price Inr */
            per_student_price_inr: string | null;
            /** Pricing Model */
            pricing_model: string;
            /** Published At */
            published_at: string | null;
            /** Sac Code */
            sac_code: string;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "published" | "retired";
            /**
             * Tier
             * @enum {string}
             */
            tier: "shared" | "dedicated";
            /** Trial Days */
            trial_days: number;
            /** Version */
            version: number;
        };
        /** PlanPatch */
        PlanPatch: {
            /** Base Price Inr */
            base_price_inr?: number | string | null;
            /** Features */
            features?: {
                [key: string]: boolean;
            } | null;
            /** Included Students */
            included_students?: number | null;
            limits?: components["schemas"]["PlanLimits"] | null;
            /** Name */
            name?: string | null;
            /** Per Student Price Inr */
            per_student_price_inr?: number | string | null;
            /** Trial Days */
            trial_days?: number | null;
        };
        /** PlatformAuditEventOut */
        PlatformAuditEventOut: {
            /** Action */
            action: string;
            /** Actor Id */
            actor_id: string | null;
            /** Actor Type */
            actor_type: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            /** Request Id */
            request_id: string | null;
            /** Resource Id */
            resource_id: string | null;
            /** Resource Type */
            resource_type: string;
            /** Seq */
            seq: number;
            /** Subject Tenant Id */
            subject_tenant_id: string | null;
            /** Summary */
            summary: {
                [key: string]: unknown;
            };
        };
        /**
         * PrecheckCreate
         * @description US-501 AC4: a board or portal pre-check report.
         */
        PrecheckCreate: {
            /**
             * Format
             * @default [
             *       "xlsx",
             *       "pdf"
             *     ]
             */
            format: ("xlsx" | "pdf")[];
            /**
             * Include Sensitive
             * @default false
             */
            include_sensitive: boolean;
            /**
             * Language
             * @default en
             * @enum {string}
             */
            language: "en" | "te";
            /** Profile Key */
            profile_key: string;
            scope?: components["schemas"]["ExportScopeIn"];
        };
        /** PriceOverrideIn */
        PriceOverrideIn: {
            /** Price Override Inr */
            price_override_inr: number | string;
            /** Reason */
            reason: string;
        };
        /** ProfileOut */
        ProfileOut: {
            /** Key */
            key: string;
            /** Label En */
            label_en: string;
            /** Label Te */
            label_te: string;
            /** Needs Apaar */
            needs_apaar: boolean;
            /** Required Fields */
            required_fields: string[];
            /** Version */
            version: number;
        };
        /**
         * PromotionCommitIn
         * @description The preview request plus, optionally, the preview's ``plan_fingerprint``: when sent, the
         *     commit is refused (409 ``promotion_plan_changed``) if enrolments changed since the preview.
         */
        PromotionCommitIn: {
            /** Held Back Student Ids */
            held_back_student_ids?: string[];
            /** Plan Fingerprint */
            plan_fingerprint?: string | null;
            /** Section Map */
            section_map?: components["schemas"]["SectionMapEntry"][];
            /**
             * To Academic Year Id
             * Format: uuid
             */
            to_academic_year_id: string;
        };
        /** PromotionCounts */
        PromotionCounts: {
            /** Graduated */
            graduated: number;
            /** Held Back */
            held_back: number;
            /** Promoted */
            promoted: number;
            /** Skipped */
            skipped: number;
        };
        /**
         * PromotionGroupOut
         * @description Students moving from one section to one target section with one outcome.
         */
        PromotionGroupOut: {
            /** Count */
            count: number;
            /** From Label */
            from_label: string;
            /**
             * From Section Id
             * Format: uuid
             */
            from_section_id: string;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "promoted" | "held_back" | "graduated" | "skipped";
            /** To Label */
            to_label: string | null;
            /** To Section Id */
            to_section_id: string | null;
        };
        /**
         * PromotionIn
         * @description Move this year's active enrolments into ``to_academic_year_id``: class N -> N+1 by class
         *     order, ``held_back_student_ids`` into the same class again, the final class graduates.
         */
        PromotionIn: {
            /** Held Back Student Ids */
            held_back_student_ids?: string[];
            /** Section Map */
            section_map?: components["schemas"]["SectionMapEntry"][];
            /**
             * To Academic Year Id
             * Format: uuid
             */
            to_academic_year_id: string;
        };
        /** PromotionPreviewOut */
        PromotionPreviewOut: {
            /** Can Commit */
            can_commit: boolean;
            counts: components["schemas"]["PromotionCounts"];
            /**
             * From Academic Year Id
             * Format: uuid
             */
            from_academic_year_id: string;
            /** Groups */
            groups: components["schemas"]["PromotionGroupOut"][];
            /** Plan Fingerprint */
            plan_fingerprint: string;
            /** Problems */
            problems: components["schemas"]["PromotionProblemOut"][];
            /** Students */
            students: components["schemas"]["PromotionStudentOut"][];
            /**
             * To Academic Year Id
             * Format: uuid
             */
            to_academic_year_id: string;
        };
        /**
         * PromotionProblemOut
         * @description Students who cannot be placed: add the section in the new year or map it.
         */
        PromotionProblemOut: {
            /**
             * Code
             * @constant
             */
            code: "no_target_section";
            /** Count */
            count: number;
            /** From Label */
            from_label: string;
            /**
             * From Section Id
             * Format: uuid
             */
            from_section_id: string;
            /**
             * Target Class Id
             * Format: uuid
             */
            target_class_id: string;
        };
        /** PromotionRunOut */
        PromotionRunOut: {
            /**
             * Can Undo
             * @description Committed and still within 24 hours of the commit.
             */
            can_undo: boolean;
            /**
             * Committed At
             * Format: date-time
             */
            committed_at: string;
            /** Committed By */
            committed_by: string | null;
            counts: components["schemas"]["PromotionCounts"];
            /**
             * From Academic Year Id
             * Format: uuid
             */
            from_academic_year_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Plan Fingerprint */
            plan_fingerprint: string;
            /**
             * Status
             * @enum {string}
             */
            status: "committed" | "undone";
            /**
             * To Academic Year Id
             * Format: uuid
             */
            to_academic_year_id: string;
            /**
             * Undo Until
             * Format: date-time
             */
            undo_until: string;
            /** Undone At */
            undone_at: string | null;
            /** Undone By */
            undone_by: string | null;
            /** Version */
            version: number;
        };
        /** PromotionStudentOut */
        PromotionStudentOut: {
            /**
             * Enrollment Id
             * Format: uuid
             */
            enrollment_id: string;
            /**
             * From Section Id
             * Format: uuid
             */
            from_section_id: string;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "promoted" | "held_back" | "graduated" | "skipped";
            /**
             * Reason
             * @description Why a student is skipped (left, graduated, already_enrolled) or cannot be placed (no_target_section).
             */
            reason?: string | null;
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
            /** To Section Id */
            to_section_id: string | null;
        };
        /** ProvisionIn */
        ProvisionIn: {
            billing_account: components["schemas"]["BillingAccountIn"];
            /** Boards */
            boards?: string[];
            /** Code */
            code: string;
            /** Custom Domain */
            custom_domain?: string | null;
            /** Override Reason */
            override_reason?: string | null;
            owner?: components["schemas"]["OwnerIn"] | null;
            /**
             * Plan Id
             * Format: uuid
             */
            plan_id: string;
            /** Price Override Inr */
            price_override_inr?: number | string | null;
            /** School Name */
            school_name: string;
            /**
             * Start As
             * @default trial
             * @enum {string}
             */
            start_as: "trial" | "active";
            /**
             * Tier
             * @default shared
             * @enum {string}
             */
            tier: "shared" | "dedicated";
        };
        /**
         * ProvisioningOut
         * @description Where a school's provisioning stands (FR-PLT-002, docs/16 §5.4). Codes only.
         *
         *     ``resumable``: not completed and no runner holds it; an operator may resume it
         *     (``POST /platform/tenants/{id}/provisioning:resume``).
         */
        ProvisioningOut: {
            /** Attempts */
            attempts: number;
            /** Failed Step */
            failed_step: ("initialise" | "owner_invite") | null;
            /** In Progress */
            in_progress: boolean;
            /** Last Error */
            last_error: string | null;
            /** Resumable */
            resumable: boolean;
            /**
             * State
             * @enum {string}
             */
            state: "registered" | "initialised" | "completed" | "failed";
            /** Updated At */
            updated_at: string | null;
        };
        /** ProvisionOut */
        ProvisionOut: {
            /**
             * Billing Account Id
             * Format: uuid
             */
            billing_account_id: string;
            /**
             * Deployment Id
             * Format: uuid
             */
            deployment_id: string;
            /** Heartbeat Key */
            heartbeat_key?: string | null;
            /** Heartbeat Key Id */
            heartbeat_key_id?: string | null;
            /**
             * Owner Invite
             * @enum {string}
             */
            owner_invite: "created" | "pending_role" | "not_applicable" | "existing";
            /**
             * Subscription Id
             * Format: uuid
             */
            subscription_id: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /**
             * Tenant Status
             * @enum {string}
             */
            tenant_status: "provisioning" | "active" | "suspended" | "offboarding" | "deleted";
            /**
             * Tier
             * @enum {string}
             */
            tier: "shared" | "dedicated";
        };
        /** ReadyOut */
        ReadyOut: {
            /** Checks */
            checks: {
                [key: string]: "ok" | "fail";
            };
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Reasoned */
        Reasoned: {
            /** Reason */
            reason: string;
        };
        /** RejectIn */
        RejectIn: {
            /** Reason */
            reason: string;
        };
        /**
         * ResolveIn
         * @description US-502 AC1: a note, a change request, or both.
         */
        ResolveIn: {
            /** Change Request Id */
            change_request_id?: string | null;
            /** Note */
            note?: string | null;
        };
        /**
         * RevealIn
         * @description Reveal one C3 field (audited). ``guardian_phone``/``guardian_address`` need
         *     ``guardian_id``; ``value_id`` reveals a specific (possibly historical) value.
         */
        RevealIn: {
            /** Attribute Key */
            attribute_key: string;
            /** Guardian Id */
            guardian_id?: string | null;
            /** Value Id */
            value_id?: string | null;
        };
        /** RevealOut */
        RevealOut: {
            /** Attribute Key */
            attribute_key: string;
            /** Display */
            display: string | null;
            /** Guardian Id */
            guardian_id: string | null;
            /** Source */
            source: string | null;
            /** Value */
            value: string | null;
            /** Value Id */
            value_id: string | null;
        };
        /** RoleOut */
        RoleOut: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is System */
            is_system: boolean;
            /** Key */
            key: string;
            /** Name En */
            name_en: string;
            /** Name Te */
            name_te: string;
            /** Permissions */
            permissions: string[];
        };
        /** RolesIn */
        RolesIn: {
            /** Roles */
            roles: string[];
        };
        /** RuleCount */
        RuleCount: {
            /** Count */
            count: number;
            /** Rule Id */
            rule_id: string;
            /**
             * Severity
             * @enum {string}
             */
            severity: "blocker" | "high" | "medium" | "low" | "info";
        };
        /** RuleOut */
        RuleOut: {
            /** Attribute Keys */
            attribute_keys: string[];
            /** Check */
            check: string;
            explanation: components["schemas"]["Bilingual"];
            /** Id */
            id: string;
            /** Requires Profile */
            requires_profile: boolean;
            /** Routes */
            routes: components["schemas"]["Bilingual"][];
            /** Scope */
            scope: string;
            severity: components["schemas"]["SeverityPolicyOut"];
            /** Sources */
            sources: string[];
            /** Version */
            version: number;
        };
        /** RunCreate */
        RunCreate: {
            /** Profile Key */
            profile_key?: string | null;
            scope?: components["schemas"]["RunScopeIn"];
        };
        /** RunOut */
        RunOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error Code */
            error_code: string | null;
            /** Event Type */
            event_type: string | null;
            /** Finished At */
            finished_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Profile Key */
            profile_key: string | null;
            /** Scope */
            scope: {
                [key: string]: unknown;
            };
            /** Started At */
            started_at: string | null;
            /** Stats */
            stats: {
                [key: string]: unknown;
            } | null;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "running" | "completed" | "failed";
            /**
             * Trigger
             * @enum {string}
             */
            trigger: "manual" | "event";
        };
        /**
         * RunScopeIn
         * @description At most one of the fields; empty = every student you can see.
         */
        RunScopeIn: {
            /** Batch Id */
            batch_id?: string | null;
            /** Class Ids */
            class_ids?: string[] | null;
            /** Section Ids */
            section_ids?: string[] | null;
            /** Student Ids */
            student_ids?: string[] | null;
        };
        /**
         * SchoolChoiceOut
         * @description A school the signed-in user may work in (school picker; no personal data).
         */
        SchoolChoiceOut: {
            /** Code */
            code: string;
            /** Name */
            name: string;
            /** Status */
            status: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** SchoolChoicesOut */
        SchoolChoicesOut: {
            /** Data */
            data: components["schemas"]["SchoolChoiceOut"][];
        };
        /** SchoolTicketMessageIn */
        SchoolTicketMessageIn: {
            /** Body */
            body: string;
        };
        /**
         * ScopeIn
         * @description ``school`` (no ref), ``class`` (class id) or ``section`` (section id).
         */
        ScopeIn: {
            /** Ref */
            ref?: string | null;
            /**
             * Type
             * @enum {string}
             */
            type: "school" | "class" | "section";
        };
        /** ScopeOut */
        ScopeOut: {
            /** Ref */
            ref: string | null;
            /**
             * Type
             * @enum {string}
             */
            type: "school" | "class" | "section";
        };
        /** ScopesIn */
        ScopesIn: {
            /** Scopes */
            scopes?: components["schemas"]["ScopeIn"][];
        };
        /** SectionCreate */
        SectionCreate: {
            /**
             * Academic Year Id
             * Format: uuid
             */
            academic_year_id: string;
            /**
             * Class Id
             * Format: uuid
             */
            class_id: string;
            /** Class Teacher Membership Id */
            class_teacher_membership_id?: string | null;
            /** Name */
            name: string;
        };
        /**
         * SectionMapEntry
         * @description Send the students of ``from_section_id`` whose target class is the class of
         *     ``to_section_id`` to that section (default: the section with the same name).
         */
        SectionMapEntry: {
            /**
             * From Section Id
             * Format: uuid
             */
            from_section_id: string;
            /**
             * To Section Id
             * Format: uuid
             */
            to_section_id: string;
        };
        /** SectionOut */
        SectionOut: {
            /**
             * Academic Year Id
             * Format: uuid
             */
            academic_year_id: string;
            /**
             * Class Id
             * Format: uuid
             */
            class_id: string;
            /** Class Teacher Membership Id */
            class_teacher_membership_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: number;
        };
        /**
         * SectionUpdate
         * @description Omit a field to keep it; send ``class_teacher_membership_id: null`` to clear it.
         */
        SectionUpdate: {
            /** Class Teacher Membership Id */
            class_teacher_membership_id?: string | null;
            /** Name */
            name?: string | null;
        };
        /** SeverityPolicyOut */
        SeverityPolicyOut: {
            /** Cap */
            cap: ("blocker" | "high" | "medium" | "low" | "info") | null;
            /** Floor */
            floor: ("blocker" | "high" | "medium" | "low" | "info") | null;
            /** Level */
            level: ("blocker" | "high" | "medium" | "low" | "info") | null;
            /**
             * Mode
             * @enum {string}
             */
            mode: "fixed" | "match_class";
        };
        /**
         * StudentCreate
         * @description New student with its first values (docs/09: source required per value).
         *
         *     ``values`` must include ``full_name``. ``section_id`` enrols the student in that section's
         *     academic year.
         */
        StudentCreate: {
            /** Roll No */
            roll_no?: string | null;
            /** Section Id */
            section_id?: string | null;
            /**
             * Status
             * @default active
             * @enum {string}
             */
            status: "provisional" | "active" | "left" | "graduated";
            /** Values */
            values: components["schemas"]["ValueIn"][];
        };
        /**
         * StudentListCreate
         * @description A student list with chosen columns (``student.export``, step-up).
         */
        StudentListCreate: {
            /** Columns */
            columns: string[];
            /**
             * Format
             * @default xlsx
             * @enum {string}
             */
            format: "csv" | "xlsx";
            /**
             * Language
             * @default en
             * @enum {string}
             */
            language: "en" | "te";
            scope?: components["schemas"]["ExportScopeIn"];
        };
        /** StudentMatch */
        StudentMatch: {
            /** Field */
            field: string | null;
            /** Score */
            score: number | null;
        };
        /**
         * StudentOut
         * @description Canonical profile + current per-source values (US-301).
         */
        StudentOut: {
            /** Admission No */
            admission_no: string | null;
            /** Canonical */
            canonical: {
                [key: string]: components["schemas"]["CanonicalOut"];
            };
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            enrollment: components["schemas"]["ClassSection"] | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Sensitive Revealable */
            sensitive_revealable: boolean;
            /** Status */
            status: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Values */
            values: {
                [key: string]: components["schemas"]["ValueOut"][];
            };
            /** Version */
            version: number;
        };
        /** StudentPatch */
        StudentPatch: {
            /**
             * Status
             * @enum {string}
             */
            status: "provisional" | "active" | "left" | "graduated";
        };
        /** StudentRef */
        StudentRef: {
            /** Admission No */
            admission_no: string | null;
            /** Display Name */
            display_name: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
        };
        /**
         * StudentSearchIn
         * @description ``POST /students/search`` body (SEC-008): names and admission numbers are personal data
         *     and never travel in the URL, where proxies and load balancers log them. Same filters, page
         *     size and cursor as ``GET /students``.
         */
        StudentSearchIn: {
            /** Admission No */
            admission_no?: string | null;
            /** Class Id */
            class_id?: string | null;
            /**
             * Cursor
             * @description Opaque cursor from next_cursor.
             */
            cursor?: string | null;
            /**
             * Limit
             * @description Page size (max 200).
             * @default 50
             */
            limit: number;
            /**
             * Query
             * @description Partial name (English or Telugu), parent name, admission number or class/section token such as 9b or IX-B.
             */
            query?: string | null;
            /** Section Id */
            section_id?: string | null;
            /** Status */
            status?: ("provisional" | "active" | "left" | "graduated") | null;
        };
        /** StudentSummary */
        StudentSummary: {
            /** Admission No */
            admission_no: string | null;
            /** Class Section */
            class_section: string | null;
            /** Display Name */
            display_name: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            match: components["schemas"]["StudentMatch"];
            /** Section Id */
            section_id: string | null;
            /** Status */
            status: string;
        };
        /** SubscriptionOut */
        SubscriptionOut: {
            /**
             * Billing Account Id
             * Format: uuid
             */
            billing_account_id: string;
            /** Cancel At Period End */
            cancel_at_period_end: boolean;
            /** Cancelled At */
            cancelled_at: string | null;
            /**
             * Current Period End
             * Format: date
             */
            current_period_end: string;
            /**
             * Current Period Start
             * Format: date
             */
            current_period_start: string;
            /** Grace Ends On */
            grace_ends_on: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Past Due Since */
            past_due_since: string | null;
            /** Pending Plan Id */
            pending_plan_id: string | null;
            /**
             * Plan Id
             * Format: uuid
             */
            plan_id: string;
            /** Price Override Inr */
            price_override_inr: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "trial" | "active" | "past_due" | "suspended" | "cancelled";
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Trial Ends At */
            trial_ends_at: string | null;
            /** Version */
            version: number;
        };
        /**
         * SummaryOut
         * @description Open findings for the pre-check screen: blockers apart from warnings (US-501 AC2).
         */
        SummaryOut: {
            /** Blockers */
            blockers: number;
            /** By Rule */
            by_rule: components["schemas"]["RuleCount"][];
            /** By Severity */
            by_severity: {
                [key: string]: number;
            };
            last_run: components["schemas"]["RunOut"] | null;
            /** Profile Key */
            profile_key: string | null;
            /** Students With Blockers */
            students_with_blockers: number;
            /** Warnings */
            warnings: number;
        };
        /** SuspendSubscriptionIn */
        SuspendSubscriptionIn: {
            /**
             * Exam Window Override
             * @default false
             */
            exam_window_override: boolean;
            /** Reason */
            reason: string;
        };
        /**
         * TemplateCreate
         * @description Save the mapping of an import as a reusable template (matched by the file's headers).
         */
        TemplateCreate: {
            /**
             * Import Id
             * Format: uuid
             */
            import_id: string;
            /** Name */
            name: string;
        };
        /** TemplateOut */
        TemplateOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Headers */
            headers: string[];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Last Used At */
            last_used_at: string | null;
            /** Mapping */
            mapping: {
                [key: string]: string;
            };
            /** Name */
            name: string;
            /** Source */
            source: string;
            /** Version */
            version: number;
        };
        /**
         * TenantBillingOut
         * @description Plan & billing page (FR-PLT-030). ``available`` is false on dedicated hosts until the
         *     heartbeat carries a billing summary (M1); invoices are then sent by email.
         */
        TenantBillingOut: {
            /**
             * Amount Due Inr
             * @default 0.00
             */
            amount_due_inr: string;
            /** Available */
            available: boolean;
            /** Billing Period */
            billing_period?: string | null;
            /** Cancel At Period End */
            cancel_at_period_end?: boolean | null;
            /** Current Period End */
            current_period_end?: string | null;
            /** Current Period Start */
            current_period_start?: string | null;
            /** Grace Ends On */
            grace_ends_on?: string | null;
            /** Past Due Since */
            past_due_since?: string | null;
            /** Plan Code */
            plan_code?: string | null;
            /** Plan Name */
            plan_name?: string | null;
            /** Status */
            status?: string | null;
            /** Tier */
            tier?: string | null;
            /** Trial Ends At */
            trial_ends_at?: string | null;
            /**
             * Usage
             * @default []
             */
            usage: components["schemas"]["UsageAgainstLimit"][];
            /** Usage Date */
            usage_date?: string | null;
        };
        /** TenantDetailOut */
        TenantDetailOut: {
            /** App Version */
            app_version: string | null;
            /** Boards */
            boards: string[];
            /** Code */
            code: string;
            counts: components["schemas"]["UsageCountsOut"] | null;
            /** Created At */
            created_at: string | null;
            /** Deployment Status */
            deployment_status: string;
            /** Flag Overrides */
            flag_overrides: {
                [key: string]: boolean;
            };
            /** Invoices */
            invoices: components["schemas"]["InvoiceOut"][];
            /** Last Heartbeat At */
            last_heartbeat_at: string | null;
            /** Offboard Approved At */
            offboard_approved_at: string | null;
            /** Offboard Requested At */
            offboard_requested_at: string | null;
            /** Open Tickets */
            open_tickets: number;
            /** Plan Code */
            plan_code: string | null;
            provisioning?: components["schemas"]["ProvisioningOut"] | null;
            /** School Name */
            school_name: string;
            subscription: components["schemas"]["SubscriptionOut"] | null;
            /** Subscription Status */
            subscription_status: ("trial" | "active" | "past_due" | "suspended" | "cancelled") | null;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /**
             * Tenant Status
             * @enum {string}
             */
            tenant_status: "provisioning" | "active" | "suspended" | "offboarding" | "deleted";
            /** Tenant Status Reason */
            tenant_status_reason: string | null;
            /**
             * Tier
             * @enum {string}
             */
            tier: "shared" | "dedicated";
        };
        /** TenantInvoice */
        TenantInvoice: {
            /** Amount Due Inr */
            amount_due_inr: string;
            /** Due Date */
            due_date: string | null;
            /**
             * Invoice Id
             * Format: uuid
             */
            invoice_id: string;
            /** Invoice Number */
            invoice_number: string | null;
            /** Issue Date */
            issue_date: string | null;
            /**
             * Period End
             * Format: date
             */
            period_end: string;
            /**
             * Period Start
             * Format: date
             */
            period_start: string;
            /** Status */
            status: string;
            /** Total Inr */
            total_inr: string;
        };
        /** TenantOut */
        TenantOut: {
            /** Boards */
            boards: string[];
            /** Code */
            code: string;
            /**
             * Deployment Mode
             * @enum {string}
             */
            deployment_mode: "shared" | "dedicated";
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /**
             * Plan Tier
             * @enum {string}
             */
            plan_tier: "shared" | "dedicated";
            settings: components["schemas"]["TenantSettings"];
            /** State Code */
            state_code: string;
            /**
             * Status
             * @enum {string}
             */
            status: "provisioning" | "active" | "suspended" | "offboarding" | "deleted";
            /** Version */
            version: number;
        };
        /**
         * TenantSettings
         * @description Validated school settings stored in ``core.tenants.settings`` (FR-TEN-012).
         *
         *     Other keys in the stored object (e.g. retention rules, ``/admin/retention`` in M1) are kept
         *     untouched when these settings change.
         */
        TenantSettings: {
            /**
             * Ai Features Enabled
             * @default true
             */
            ai_features_enabled: boolean;
            /**
             * Ai Monthly Budget Inr
             * @default 5000
             */
            ai_monthly_budget_inr: number;
            /**
             * Date Format
             * @default DD/MM/YYYY
             * @enum {string}
             */
            date_format: "DD/MM/YYYY" | "DD-MM-YYYY" | "YYYY-MM-DD";
            /**
             * Idle Timeout Minutes
             * @default 15
             */
            idle_timeout_minutes: number;
            /** Languages */
            languages?: ("en" | "te")[];
        };
        /**
         * TenantSettingsPatch
         * @description Send only the settings to change.
         */
        TenantSettingsPatch: {
            /** Ai Features Enabled */
            ai_features_enabled?: boolean | null;
            /** Ai Monthly Budget Inr */
            ai_monthly_budget_inr?: number | null;
            /** Date Format */
            date_format?: ("DD/MM/YYYY" | "DD-MM-YYYY" | "YYYY-MM-DD") | null;
            /** Idle Timeout Minutes */
            idle_timeout_minutes?: number | null;
            /** Languages */
            languages?: ("en" | "te")[] | null;
        };
        /**
         * TenantSummaryOut
         * @description A school as the control plane sees it: IDs, codes, public name, statuses, counts.
         */
        TenantSummaryOut: {
            /** App Version */
            app_version: string | null;
            /** Code */
            code: string;
            /** Created At */
            created_at: string | null;
            /** Deployment Status */
            deployment_status: string;
            /** Last Heartbeat At */
            last_heartbeat_at: string | null;
            /** Plan Code */
            plan_code: string | null;
            /** School Name */
            school_name: string;
            /** Subscription Status */
            subscription_status: ("trial" | "active" | "past_due" | "suspended" | "cancelled") | null;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /**
             * Tenant Status
             * @enum {string}
             */
            tenant_status: "provisioning" | "active" | "suspended" | "offboarding" | "deleted";
            /**
             * Tier
             * @enum {string}
             */
            tier: "shared" | "dedicated";
        };
        /** TicketCreateOperator */
        TicketCreateOperator: {
            /** Body */
            body: string;
            /**
             * Category
             * @enum {string}
             */
            category: "access" | "import" | "data_quality" | "exports" | "documents" | "ask" | "billing" | "bug" | "other";
            /**
             * Channel
             * @enum {string}
             */
            channel: "email" | "phone" | "whatsapp";
            /**
             * Priority
             * @default p3
             * @enum {string}
             */
            priority: "p1" | "p2" | "p3" | "p4";
            /** Subject */
            subject: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** TicketCreateSchool */
        TicketCreateSchool: {
            /** Body */
            body: string;
            /**
             * Category
             * @enum {string}
             */
            category: "access" | "import" | "data_quality" | "exports" | "documents" | "ask" | "billing" | "bug" | "other";
            /**
             * Priority
             * @default p3
             * @enum {string}
             */
            priority: "p1" | "p2" | "p3" | "p4";
            /** Subject */
            subject: string;
        };
        /** TicketMessageIn */
        TicketMessageIn: {
            /** Body */
            body: string;
            /**
             * Internal Note
             * @default false
             */
            internal_note: boolean;
        };
        /** TicketMessageOut */
        TicketMessageOut: {
            /** Author Id */
            author_id: string | null;
            /** Author Type */
            author_type: string;
            /** Body */
            body: string;
            /** Created At */
            created_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Internal Note */
            internal_note: boolean;
        };
        /** TicketOut */
        TicketOut: {
            /** Assigned To */
            assigned_to: string | null;
            /** Category */
            category: string;
            /** Channel */
            channel: string;
            /** Closed At */
            closed_at: string | null;
            /** Created At */
            created_at: string | null;
            /** First Responded At */
            first_responded_at: string | null;
            /**
             * First Response Due At
             * Format: date-time
             */
            first_response_due_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Messages */
            messages?: components["schemas"]["TicketMessageOut"][];
            /** Number */
            number: string;
            /** Personal Data Flagged */
            personal_data_flagged: boolean;
            /** Priority */
            priority: string;
            /**
             * Resolution Due At
             * Format: date-time
             */
            resolution_due_at: string;
            /** Resolved At */
            resolved_at: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "open" | "in_progress" | "waiting_on_school" | "resolved" | "closed";
            /** Subject */
            subject: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Ticket No */
            ticket_no: number;
            /** Updated At */
            updated_at: string | null;
            /** Version */
            version: number;
        };
        /** TicketPatch */
        TicketPatch: {
            /** Assigned To */
            assigned_to?: string | null;
            /** Personal Data Flagged */
            personal_data_flagged?: boolean | null;
            /** Priority */
            priority?: ("p1" | "p2" | "p3" | "p4") | null;
            /** Status */
            status?: ("open" | "in_progress" | "waiting_on_school" | "resolved" | "closed") | null;
        };
        /** UnreadCountOut */
        UnreadCountOut: {
            /** Count */
            count: number;
        };
        /**
         * UploadCreate
         * @description Ask for a presigned POST. ``document_id`` asks to upload a new version of that document.
         */
        UploadCreate: {
            /** Content Type */
            content_type: string;
            /** Document Id */
            document_id?: string | null;
            /** Filename */
            filename: string;
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "evidence" | "register_scan" | "circular" | "policy" | "other" | "import_file";
            /** Size Bytes */
            size_bytes: number;
        };
        /** UploadOut */
        UploadOut: {
            /** Batch Id */
            batch_id?: string | null;
            /** Document Id */
            document_id?: string | null;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Fields */
            fields: {
                [key: string]: string;
            };
            /** Max Bytes */
            max_bytes: number;
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "evidence" | "register_scan" | "circular" | "policy" | "other" | "import_file";
            /**
             * Upload Id
             * Format: uuid
             */
            upload_id: string;
            /** Url */
            url: string;
        };
        /** UsageAgainstLimit */
        UsageAgainstLimit: {
            /** Limit */
            limit: string | null;
            /** Metric */
            metric: string;
            /** Percent */
            percent: number | null;
            /** Used */
            used: string;
        };
        /** UsageCountsOut */
        UsageCountsOut: {
            /** Academic Years */
            academic_years: number;
            /** Active Memberships */
            active_memberships: number;
            /** Sections */
            sections: number;
            /** Users */
            users: number;
        };
        /** UsageDailyOut */
        UsageDailyOut: {
            /** Active Users */
            active_users: number;
            /** Ai Cost Inr */
            ai_cost_inr: string;
            /** Ai Input Tokens */
            ai_input_tokens: number;
            /** Ai Output Tokens */
            ai_output_tokens: number;
            /** Ai Queries */
            ai_queries: number;
            /** Documents */
            documents: number;
            /** Source */
            source: string;
            /** Staff Users */
            staff_users: number;
            /** Storage Bytes */
            storage_bytes: number;
            /** Students Active */
            students_active: number;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /**
             * Usage Date
             * Format: date
             */
            usage_date: string;
        };
        /** UserOut */
        UserOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Display Name */
            display_name: string;
            /** Email */
            email: string | null;
            /** Expires At */
            expires_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Last Login At */
            last_login_at: string | null;
            /**
             * Membership Id
             * Format: uuid
             */
            membership_id: string;
            /**
             * Preferred Language
             * @enum {string}
             */
            preferred_language: "en" | "te";
            /** Roles */
            roles: string[];
            /** Scopes */
            scopes: components["schemas"]["ScopeOut"][];
            /**
             * Status
             * @enum {string}
             */
            status: "invited" | "active" | "suspended" | "removed";
            /** Version */
            version: number;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /**
         * ValueIn
         * @description One observed value from one source (FR-STU-002). Identity attributes from the admission
         *     register cannot be changed here once recorded: use a change request.
         */
        ValueIn: {
            /** Attribute Key */
            attribute_key: string;
            /** Evidence Document Id */
            evidence_document_id?: string | null;
            /**
             * Source
             * @enum {string}
             */
            source: "admission_register" | "aadhaar_as_printed" | "udise_plus" | "board_registration" | "birth_certificate" | "parent_form" | "tc_incoming" | "manual_entry";
            /** Value */
            value: string;
        };
        /** ValueOut */
        ValueOut: {
            /** Attribute Key */
            attribute_key: string;
            /** Change Request Id */
            change_request_id: string | null;
            /** Current */
            current: boolean;
            /** Evidence Document Id */
            evidence_document_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Import Batch Id */
            import_batch_id: string | null;
            /** Masked */
            masked: boolean;
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
            /**
             * Recorded By
             * Format: uuid
             */
            recorded_by: string;
            /** Source */
            source: string;
            /** Superseded By */
            superseded_by: string | null;
            /** Value */
            value: string | null;
            /**
             * Verification Status
             * @enum {string}
             */
            verification_status: "unverified" | "verified" | "rejected";
            /** Verified At */
            verified_at: string | null;
            /** Verified By */
            verified_by: string | null;
        };
        /** ValueRecorded */
        ValueRecorded: {
            /** Attribute Key */
            attribute_key: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Source */
            source: string;
            /**
             * Student Id
             * Format: uuid
             */
            student_id: string;
            /** Student Version */
            student_version: number;
            /** Superseded */
            superseded: string | null;
        };
        /** VerifyIn */
        VerifyIn: {
            /**
             * Status
             * @default verified
             * @enum {string}
             */
            status: "verified" | "rejected";
        };
        /** VersionCreate */
        VersionCreate: {
            /**
             * Upload Id
             * Format: uuid
             */
            upload_id: string;
        };
        /** VersionOut */
        VersionOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error */
            error: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Mime Type */
            mime_type: string;
            /** Size Bytes */
            size_bytes: number;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "scanning" | "extracting" | "chunking" | "embedding" | "ready" | "failed" | "quarantined";
            /** Version No */
            version_no: number;
        };
        /** WaiveIn */
        WaiveIn: {
            /** Reason */
            reason: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_academic_years_api_v1_academic_years_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_AcademicYearOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_academic_year_api_v1_academic_years_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AcademicYearCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AcademicYearOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_academic_year_api_v1_academic_years__year_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                year_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AcademicYearOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_academic_year_api_v1_academic_years__year_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                year_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AcademicYearUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AcademicYearOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    make_academic_year_current_api_v1_academic_years__year_id__make_current_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                year_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AcademicYearOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_promotions_api_v1_academic_years__year_id__promotions_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                year_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PromotionRunOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    commit_promotion_api_v1_academic_years__year_id__promotions_commit_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                year_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PromotionCommitIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PromotionRunOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_promotion_api_v1_academic_years__year_id__promotions_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                year_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PromotionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PromotionPreviewOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    undo_promotion_api_v1_academic_years__year_id__promotions_undo_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                year_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PromotionRunOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_announcements_api_v1_announcements_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnnouncementBrief"][];
                };
            };
        };
    };
    list_attributes_api_v1_attributes_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AttributeOut"][];
                };
            };
        };
    };
    list_audit_events_api_v1_audit_events_get: {
        parameters: {
            query?: {
                action?: string | null;
                /** @description Acting user id */
                actor?: string | null;
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                from?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                resource_id?: string | null;
                resource_type?: string | null;
                to?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_AuditEventOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_audit_chain_api_v1_audit_verify_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["app__audit__viewer__AuditVerifyOut"];
                };
            };
        };
    };
    revoke_grant_api_v1_breakglass_grants__grant_id__revoke_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                grant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GrantOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_requests_api_v1_breakglass_requests_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                /** @description Only this status. */
                status?: ("requested" | "approved" | "active" | "expired" | "revoked" | "denied") | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_GrantOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_request_api_v1_breakglass_requests__request_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GrantOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_request_api_v1_breakglass_requests__request_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GrantOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    deny_request_api_v1_breakglass_requests__request_id__deny_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GrantOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_change_requests_api_v1_change_requests_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                status?: ("pending" | "approved" | "rejected" | "expired" | "cancelled") | null;
                student_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ChangeRequestOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submit_change_request_api_v1_change_requests_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChangeRequestCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeRequestOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_change_request_api_v1_change_requests__change_request_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                change_request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeRequestOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_change_request_api_v1_change_requests__change_request_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                change_request_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ApproveIn"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeRequestOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_change_request_api_v1_change_requests__change_request_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                change_request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeRequestOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_request_memo_api_v1_change_requests__change_request_id__memo_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                change_request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Print-ready A4 memo */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/html": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reject_change_request_api_v1_change_requests__change_request_id__reject_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                change_request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RejectIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeRequestOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_classes_api_v1_classes_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ClassOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_class_api_v1_classes_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClassCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_class_api_v1_classes__class_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                class_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_class_api_v1_classes__class_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                class_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClassUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_default_classes_api_v1_classes_defaults_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ClassOut_"];
                };
            };
        };
    };
    list_documents_api_v1_documents_get: {
        parameters: {
            query?: {
                academic_year_id?: string | null;
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                doc_type?: ("circular" | "policy" | "minutes" | "register_scan" | "certificate" | "letter" | "form" | "report" | "verified_answer" | "other" | "evidence" | "import_file") | null;
                /** @description Page size (max 200). */
                limit?: number;
                purpose?: ("evidence" | "register_scan" | "circular" | "policy" | "other" | "import_file") | null;
                status?: ("active" | "archived") | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_DocumentOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    register_document_api_v1_documents_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DocumentCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_document_api_v1_documents__document_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_document_api_v1_documents__document_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_acl_api_v1_documents__document_id__acl_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AclUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_download_url_api_v1_documents__document_id__download_url_get: {
        parameters: {
            query?: {
                version?: number | null;
            };
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DownloadUrlOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_version_api_v1_documents__document_id__versions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VersionCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_upload_api_v1_documents_uploads_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UploadCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UploadOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_findings_api_v1_dq_findings_get: {
        parameters: {
            query?: {
                attribute_key?: string | null;
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                profile_key?: string | null;
                rule_id?: ("DQ-001" | "DQ-002" | "DQ-003" | "DQ-004" | "DQ-005" | "DQ-006" | "DQ-007" | "DQ-008" | "DQ-009" | "DQ-010" | "DQ-011" | "DQ-012")[] | null;
                section_id?: string | null;
                severity?: ("blocker" | "high" | "medium" | "low" | "info")[] | null;
                status?: ("open" | "resolved" | "waived" | "reopened")[] | null;
                student_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_FindingOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_finding_api_v1_dq_findings__finding_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                finding_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FindingOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolve_finding_api_v1_dq_findings__finding_id__resolve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                finding_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ResolveIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FindingOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    waive_finding_api_v1_dq_findings__finding_id__waive_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                finding_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WaiveIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FindingOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_profiles_api_v1_dq_profiles_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProfileOut"][];
                };
            };
        };
    };
    list_rules_api_v1_dq_rules_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RuleOut"][];
                };
            };
        };
    };
    start_run_api_v1_dq_runs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_api_v1_dq_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_summary_api_v1_dq_summary_get: {
        parameters: {
            query?: {
                profile_key?: string | null;
                section_ids?: string[] | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SummaryOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_export_profiles_api_v1_export_profiles_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExportProfileOut"][];
                };
            };
        };
    };
    list_exports_api_v1_exports_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                requested_by?: "me" | "all";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ExportOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_precheck_export_api_v1_exports_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrecheckCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_export_api_v1_exports__export_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                export_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_export_download_url_api_v1_exports__export_id__download_url_get: {
        parameters: {
            query?: {
                format?: ("xlsx" | "pdf" | "csv") | null;
            };
            header?: never;
            path: {
                export_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExportDownloadOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_student_list_export_api_v1_exports_student_list_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StudentListCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_batches_api_v1_extraction_batches_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_BatchOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_batch_api_v1_extraction_batches_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BatchCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BatchOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_batch_api_v1_extraction_batches__batch_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                batch_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BatchDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_items_api_v1_extraction_items_get: {
        parameters: {
            query?: {
                batch_id?: string | null;
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                status?: ("pending_review" | "confirmed" | "rejected") | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ItemOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_item_api_v1_extraction_items__item_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                item_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ItemDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirm_item_api_v1_extraction_items__item_id__confirm_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                item_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ItemConfirm"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ItemOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reject_item_api_v1_extraction_items__item_id__reject_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                item_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ItemReject"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ItemOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    heartbeat_api_v1_fleet_heartbeat_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HeartbeatOut"];
                };
            };
        };
    };
    list_templates_api_v1_import_templates_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TemplateOut"][];
                };
            };
        };
    };
    create_template_api_v1_import_templates_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TemplateCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TemplateOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_imports_api_v1_imports_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                status?: ("uploaded" | "parsing" | "parsed" | "validating" | "validated" | "committing" | "committed" | "reverting" | "reverted" | "failed") | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ImportSummary_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_import_api_v1_imports_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_import_api_v1_imports__import_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    commit_import_api_v1_imports__import_id__commit_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["CommitIn"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_mapping_api_v1_imports__import_id__mapping_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MappingIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revert_import_api_v1_imports__import_id__revert_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_rows_api_v1_imports__import_id__rows_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                status?: ("valid" | "error" | "committed" | "skipped" | "reverted" | "warning") | null;
            };
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ImportRowOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    validate_import_api_v1_imports__import_id__validate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                import_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_me_api_v1_me_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["app__identity__schemas__MeOut"];
                };
            };
        };
    };
    accept_my_invitations_api_v1_me_accept_invitations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AcceptedInvitationsOut"];
                };
            };
        };
    };
    set_active_tenant_api_v1_me_active_tenant_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ActiveTenantIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["app__identity__schemas__MeOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    record_login_event_api_v1_me_login_event_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LoginEventOut"];
                };
            };
        };
    };
    list_my_schools_api_v1_me_schools_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchoolChoicesOut"];
                };
            };
        };
    };
    list_notifications_api_v1_notifications_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                /** @description Only unread notifications. */
                unread?: boolean;
            };
            header?: {
                "Accept-Language"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_NotificationOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    mark_read_api_v1_notifications__notification_id__read_post: {
        parameters: {
            query?: never;
            header?: {
                "Accept-Language"?: string | null;
            };
            path: {
                notification_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NotificationOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    mark_all_read_api_v1_notifications_read_all_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MarkedReadOut"];
                };
            };
        };
    };
    unread_count_api_v1_notifications_unread_count_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UnreadCountOut"];
                };
            };
        };
    };
    list_permissions_api_v1_permissions_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_PermissionOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_announcements_api_v1_platform_announcements_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_AnnouncementOut_"];
                };
            };
        };
    };
    create_announcement_api_v1_platform_announcements_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnnouncementIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnnouncementOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_announcement_api_v1_platform_announcements__announcement_id__patch: {
        parameters: {
            query?: never;
            header?: {
                "If-Match"?: string | null;
            };
            path: {
                announcement_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnnouncementIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnnouncementOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_announcement_api_v1_platform_announcements__announcement_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                announcement_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnnouncementOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_audit_events_api_v1_platform_audit_events_get: {
        parameters: {
            query?: {
                action?: string | null;
                actor?: string | null;
                cursor?: number | null;
                from?: string | null;
                limit?: number;
                tenant_id?: string | null;
                to?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_PlatformAuditEventOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_audit_api_v1_platform_audit_verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["app__platform__schemas__AuditVerifyOut"];
                };
            };
        };
    };
    list_breakglass_api_v1_platform_break_glass_requests_get: {
        parameters: {
            query?: {
                tenant_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_BreakGlassOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_breakglass_api_v1_platform_break_glass_requests_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BreakGlassIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BreakGlassOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirm_breakglass_api_v1_platform_break_glass_requests__request_id__emergency_confirm_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BreakGlassOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_dashboard_api_v1_platform_dashboard_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DashboardOut"];
                };
            };
        };
    };
    list_deployments_api_v1_platform_deployments_get: {
        parameters: {
            query?: {
                status?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_DeploymentOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_deployment_api_v1_platform_deployments__deployment_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeploymentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_deployment_api_v1_platform_deployments__deployment_id__patch: {
        parameters: {
            query?: never;
            header?: {
                "If-Match"?: string | null;
            };
            path: {
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeploymentPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeploymentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decommission_deployment_api_v1_platform_deployments__deployment_id__decommission_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeploymentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rotate_heartbeat_key_api_v1_platform_deployments__deployment_id__heartbeat_key_rotate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                deployment_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HeartbeatKeyOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_flags_api_v1_platform_flags_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_FlagOut_"];
                };
            };
        };
    };
    put_flag_api_v1_platform_flags__key__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                key: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FlagIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FlagOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_flag_override_api_v1_platform_flags__key__tenants__tenant_id__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                key: string;
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FlagOverrideIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FlagOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_flag_override_api_v1_platform_flags__key__tenants__tenant_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                key: string;
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    fleet_versions_api_v1_platform_fleet_versions_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FleetVersionOut"][];
                };
            };
        };
    };
    invoice_run_api_v1_platform_invoice_runs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InvoiceRunIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_invoices_api_v1_platform_invoices_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                financial_year?: string | null;
                limit?: number;
                status?: string | null;
                tenant_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_InvoiceOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_invoice_api_v1_platform_invoices_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InvoiceCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvoiceOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_invoice_api_v1_platform_invoices__invoice_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvoiceOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discard_invoice_api_v1_platform_invoices__invoice_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_invoice_api_v1_platform_invoices__invoice_id__patch: {
        parameters: {
            query?: never;
            header?: {
                "If-Match"?: string | null;
            };
            path: {
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InvoicePatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvoiceOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    issue_invoice_api_v1_platform_invoices__invoice_id__issue_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvoiceOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    record_payment_api_v1_platform_invoices__invoice_id__payments_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PaymentIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaymentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    void_invoice_api_v1_platform_invoices__invoice_id__void_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                invoice_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Reasoned"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvoiceOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_job_api_v1_platform_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    me_api_v1_platform_me_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["app__platform__schemas__MeOut"];
                };
            };
        };
    };
    list_operators_api_v1_platform_operators_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_OperatorOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    invite_operator_api_v1_platform_operators_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OperatorInvite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OperatorOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    deactivate_operator_api_v1_platform_operators__operator_id__deactivate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operator_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OperatorOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_operator_roles_api_v1_platform_operators__operator_id__roles_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operator_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OperatorRolesIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OperatorOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reverse_payment_api_v1_platform_payments__payment_id__reverse_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                payment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Reasoned"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PaymentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_plans_api_v1_platform_plans_get: {
        parameters: {
            query?: {
                status?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_PlanOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_plan_api_v1_platform_plans_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PlanIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_plan_api_v1_platform_plans__plan_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_plan_api_v1_platform_plans__plan_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PlanPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    publish_plan_api_v1_platform_plans__plan_id__publish_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retire_plan_api_v1_platform_plans__plan_id__retire_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_subscriptions_api_v1_platform_subscriptions_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                status?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_SubscriptionOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_subscription_api_v1_platform_subscriptions__sub_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    activate_subscription_api_v1_platform_subscriptions__sub_id__activate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_subscription_api_v1_platform_subscriptions__sub_id__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Reasoned"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_plan_api_v1_platform_subscriptions__sub_id__change_plan_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChangePlanIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    extend_trial_api_v1_platform_subscriptions__sub_id__extend_trial_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExtendTrialIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_price_override_api_v1_platform_subscriptions__sub_id__price_override_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PriceOverrideIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clear_price_override_api_v1_platform_subscriptions__sub_id__price_override_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reactivate_subscription_api_v1_platform_subscriptions__sub_id__reactivate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    suspend_subscription_api_v1_platform_subscriptions__sub_id__suspend_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sub_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SuspendSubscriptionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubscriptionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_tickets_api_v1_platform_support_tickets_get: {
        parameters: {
            query?: {
                assigned_to?: string | null;
                cursor?: string | null;
                limit?: number;
                priority?: string | null;
                status?: string | null;
                tenant_id?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["app__platform__schemas__Page_TicketOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_ticket_api_v1_platform_support_tickets_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TicketCreateOperator"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_ticket_api_v1_platform_support_tickets__ticket_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_ticket_api_v1_platform_support_tickets__ticket_id__patch: {
        parameters: {
            query?: never;
            header?: {
                "If-Match"?: string | null;
            };
            path: {
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TicketPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_ticket_message_api_v1_platform_support_tickets__ticket_id__messages_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TicketMessageIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_tenants_api_v1_platform_tenants_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                past_due?: boolean;
                plan?: string | null;
                q?: string | null;
                status?: string | null;
                tier?: string | null;
                trial_ending?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_TenantSummaryOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    provision_tenant_api_v1_platform_tenants_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProvisionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProvisionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_tenant_api_v1_platform_tenants__tenant_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantDetailOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    activate_tenant_api_v1_platform_tenants__tenant_id__activate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantDetailOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_billing_account_api_v1_platform_tenants__tenant_id__billing_account_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BillingAccountOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_billing_account_api_v1_platform_tenants__tenant_id__billing_account_put: {
        parameters: {
            query?: never;
            header?: {
                "If-Match"?: string | null;
            };
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BillingAccountIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BillingAccountOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_offboarding_api_v1_platform_tenants__tenant_id__offboarding_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OffboardRequestIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantDetailOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_offboarding_api_v1_platform_tenants__tenant_id__offboarding_approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantDetailOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resend_owner_invite_api_v1_platform_tenants__tenant_id__owner_invite_resend_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resume_provisioning_api_v1_platform_tenants__tenant_id__provisioning_resume_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProvisionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reactivate_tenant_api_v1_platform_tenants__tenant_id__reactivate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Reasoned"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantDetailOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    suspend_tenant_api_v1_platform_tenants__tenant_id__suspend_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Reasoned"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantDetailOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    tenant_usage_api_v1_platform_tenants__tenant_id__usage_get: {
        parameters: {
            query?: {
                from?: string | null;
                to?: string | null;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UsageDailyOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    all_usage_api_v1_platform_usage_get: {
        parameters: {
            query?: {
                from?: string | null;
                tenant_id?: string | null;
                to?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UsageDailyOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_roles_api_v1_roles_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_RoleOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_sections_api_v1_sections_get: {
        parameters: {
            query?: {
                academic_year_id?: string | null;
                class_id?: string | null;
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_SectionOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_section_api_v1_sections_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SectionCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SectionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_section_api_v1_sections__section_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                section_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SectionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_section_api_v1_sections__section_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                section_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SectionUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SectionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    search_students_api_v1_students_get: {
        parameters: {
            query?: {
                /**
                 * @deprecated
                 * @description Deprecated: names and admission numbers in the URL end up in proxy and load-balancer access logs. Send them in the body of POST /api/v1/students/search instead (SEC-008). Stops working after the Sunset date, Thu, 31 Dec 2026 23:59:59 GMT.
                 */
                admission_no?: string | null;
                class_id?: string | null;
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
                /**
                 * @deprecated
                 * @description Deprecated: names and admission numbers in the URL end up in proxy and load-balancer access logs. Send them in the body of POST /api/v1/students/search instead (SEC-008). Stops working after the Sunset date, Thu, 31 Dec 2026 23:59:59 GMT.
                 */
                query?: string | null;
                section_id?: string | null;
                status?: ("provisional" | "active" | "left" | "graduated") | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_StudentSummary_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_student_api_v1_students_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StudentCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StudentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_student_api_v1_students__student_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StudentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_student_api_v1_students__student_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StudentPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StudentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_enrollments_api_v1_students__student_id__enrollments_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EnrollmentOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    enrol_student_api_v1_students__student_id__enrollments_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EnrollmentIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EnrollmentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_enrollment_api_v1_students__student_id__enrollments__enrollment_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                enrollment_id: string;
                student_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EnrollmentPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EnrollmentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    end_enrollment_api_v1_students__student_id__enrollments__enrollment_id__end_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                enrollment_id: string;
                student_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["EnrollmentEnd"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EnrollmentOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_guardians_api_v1_students__student_id__guardians_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GuardianOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_guardian_api_v1_students__student_id__guardians_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GuardianCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GuardianOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remove_guardian_api_v1_students__student_id__guardians__guardian_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                guardian_id: string;
                student_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_guardian_api_v1_students__student_id__guardians__guardian_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                guardian_id: string;
                student_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GuardianPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GuardianOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reveal_sensitive_api_v1_students__student_id__sensitive_reveal_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RevealIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevealOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_values_api_v1_students__student_id__values_get: {
        parameters: {
            query?: {
                attribute?: string | null;
            };
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ValueOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    record_value_api_v1_students__student_id__values_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ValueIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ValueRecorded"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_value_api_v1_students__student_id__values__value_id__verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                student_id: string;
                value_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["VerifyIn"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ValueOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    search_students_by_body_api_v1_students_search_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StudentSearchIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_StudentSummary_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_tickets_api_v1_support_tickets_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["app__authz__http__Page_TicketOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_ticket_api_v1_support_tickets_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TicketCreateSchool"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_ticket_api_v1_support_tickets__ticket_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reply_to_ticket_api_v1_support_tickets__ticket_id__messages_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SchoolTicketMessageIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_tenant_api_v1_tenant_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantOut"];
                };
            };
        };
    };
    update_tenant_settings_api_v1_tenant_patch: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TenantSettingsPatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_billing_api_v1_tenant_billing_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantBillingOut"];
                };
            };
        };
    };
    list_billing_invoices_api_v1_tenant_billing_invoices_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_TenantInvoice_"];
                };
            };
        };
    };
    list_users_api_v1_users_get: {
        parameters: {
            query?: {
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_UserOut_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    invite_user_api_v1_users_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InviteIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_user_api_v1_users__user_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_user_status_api_v1_users__user_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MembershipStatusIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    replace_user_roles_api_v1_users__user_id__roles_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RolesIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    replace_user_scopes_api_v1_users__user_id__scopes_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScopesIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    healthz_healthz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthOut"];
                };
            };
        };
    };
    readyz_readyz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadyOut"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadyOut"];
                };
            };
        };
    };
}

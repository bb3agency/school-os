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
         *     The dedicated heartbeat key is returned only in the first response.
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
         * @description Go-live (provisioning -> active); refused by the database without a data key.
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
         * @description Find students by partial name in English or Telugu, admission number, class/section
         *     (``9b``, ``IX-B``) or parent name (permission ``student.read_basic``; class and subject
         *     teachers see only students in their sections/classes this year).
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
        get?: never;
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
        delete?: never;
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
        /** ExtendTrialIn */
        ExtendTrialIn: {
            /**
             * Trial Ends At
             * Format: date-time
             */
            trial_ends_at: string;
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
        /** Page[BreakGlassOut] */
        Page_BreakGlassOut_: {
            /** Data */
            data: components["schemas"]["BreakGlassOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
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
        /** Page[InvoiceOut] */
        Page_InvoiceOut_: {
            /** Data */
            data: components["schemas"]["InvoiceOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
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
        /** PriceOverrideIn */
        PriceOverrideIn: {
            /** Price Override Inr */
            price_override_inr: number | string;
            /** Reason */
            reason: string;
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
                admission_no?: string | null;
                class_id?: string | null;
                /** @description Opaque cursor from next_cursor. */
                cursor?: string | null;
                /** @description Page size (max 200). */
                limit?: number;
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

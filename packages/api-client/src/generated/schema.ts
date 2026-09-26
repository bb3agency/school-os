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
        /** ActiveTenantIn */
        ActiveTenantIn: {
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
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
        /** AuditVerifyOut */
        AuditVerifyOut: {
            /** Checked */
            checked: number;
            /** First Bad Seq */
            first_bad_seq: number | null;
            /** Ok */
            ok: boolean;
            /** Reason */
            reason: string | null;
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
        /** HealthOut */
        HealthOut: {
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
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
        /** MeOut */
        MeOut: {
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
        /** Page[AcademicYearOut] */
        Page_AcademicYearOut_: {
            /** Data */
            data: components["schemas"]["AcademicYearOut"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page[AuditEventOut] */
        Page_AuditEventOut_: {
            /** Data */
            data: components["schemas"]["AuditEventOut"][];
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
        /** Page[PermissionOut] */
        Page_PermissionOut_: {
            /** Data */
            data: components["schemas"]["PermissionOut"][];
            /** Next Cursor */
            next_cursor: string | null;
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
        /** Page[UserOut] */
        Page_UserOut_: {
            /** Data */
            data: components["schemas"]["UserOut"][];
            /** Next Cursor */
            next_cursor: string | null;
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
                    "application/json": components["schemas"]["AuditVerifyOut"];
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
                    "application/json": components["schemas"]["MeOut"];
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
                    "application/json": components["schemas"]["MeOut"];
                };
            };
            /** @description Validation Error */
            422: {
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

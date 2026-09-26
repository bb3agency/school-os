# ADR-0017: Platform admin panel (control plane) architecture

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-26 |
| Deciders | Founder (product owner approval of build proposals B1–B25) |
| Amends / supersedes | Replaces the v0.1 "operator console" in module `ops` (C13, FR-OPS-001) with capability C14 in module `platform` |

## Context

The platform team must provision schools (shared and dedicated), manage plans, subscriptions and invoices, watch the fleet, run feature flags and announcements, handle support tickets, and manage operator accounts, all **without standing access to school data** (BR-09). v0.1 described a small operator console inside `ops`. The commercial model (ADR-0015) and privilege separation (ADR-0013) make this a larger, security-sensitive surface that needs its own module, identity and audit.

## Decision

- **Same monolith, new module `platform`** (`apps/api/app/platform/`): operators, tenants (provisioning), plans, subscriptions, billing accounts, invoices, payments, usage, fleet/deployments, feature flags, announcements, support tickets, platform audit viewer. **Billing lives inside `platform`**; there is no separate billing service.
- **Routes** under `/api/v1/platform/*`, each protected by `require_platform("platform.<…>")` (with `step_up=True` for ᴿ permissions). The fleet heartbeat `POST /api/v1/fleet/heartbeat` is machine-authenticated by `require_fleet_signature()` (HMAC). The route-enumeration test accepts exactly `require()`, `require_platform()` and `require_fleet_signature()`; health checks remain the only unauthenticated routes.
- **Database access** only through `core.db.platform_session()` as role `sos_platform` (ADR-0013). Tenant-side changes happen only through the allowlisted definer functions (`core.provision_tenant`, `core.set_tenant_status`, `core.create_user_for_invite`, `core.list_tenant_ids`, `core.tenant_usage_summary`).
- **Other modules** reach `platform` only through `platform.service` (for example the school-side "Plan & billing" page uses `core.current_subscription()`; support-ticket creation from the school app calls `platform.service.open_ticket_from_tenant()`). `platform` never imports tenant modules' repositories or models.
- **Identity:** a separate OIDC client for operators (`SOS_PLATFORM_OIDC_*`, `PLATFORM_OIDC_CLIENT_*`), MFA mandatory, step-up ≤ 5 minutes for ᴿ permissions, two-person approval for offboarding and emergency break-glass (SEC-027, SEC-029).
- **Web:** Next.js route group `/[locale]/platform/*`, served in production on its own host `admin.<domain>`, with its own `__Host-` session cookie and BFF handlers. Same CSP, i18n (`en`, `te`) and accessibility rules as the school app.
- **Jobs:** platform jobs (invoice generation, usage collection, heartbeat staleness check, platform audit verification) run on the shared `beat`/`worker` and record progress in `platform.job_runs`.
- **Audit:** every control-plane action is recorded with `audit.service.record_platform()` in `platform.audit_events` (hash-chained) in the same transaction.
- **Deployment mode:** when `SOS_DEPLOYMENT_MODE=dedicated`, the platform routers and web route group are **not mounted** (requests get 404), and platform beat schedules are not registered.
- Full specification: [16-platform-admin-panel.md](../16-platform-admin-panel.md).

## Consequences

- Good: one codebase, one deploy; control plane reuses core infrastructure (logging, errors, telemetry) but not tenant data access.
- Good: privilege separation is enforced by the database, not only by code review.
- Bad: the shared API process holds two database credentials; a remote-code-execution bug in the API could use either. Mitigations: separate host and session for the panel, WAF rules on `admin.<domain>`, least-privilege roles, alerts on `permission denied` errors from `sos_platform` (11 §12).
- Bad: control-plane outages share the shared stack's fate; acceptable because schools do not depend on the control plane to work day to day.
- Follow-up: 02 C14 and US-1301..1310, 03 FR-PLT-001..030, 07 §6.5–6.6, 09 §4, 12 §4.12, 14 Task 11.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Separate control-plane service and database | More to deploy, secure and back up; provisioning would need cross-service calls; revisit only if the control plane grows large |
| Internal tools builder (Retool, Appsmith, etc.) with DB access | Needs database credentials in a third-party tool; weak audit; licence and data-location concerns |
| Keep operator console inside `ops` | `ops` also holds tenant tables (job runs, outbox, break-glass); mixing makes privilege separation unclear |
| Django admin or similar generic admin | Different stack (ADR-0004); generic CRUD encourages direct table edits without audit |

## Related requirements

FR-PLT-001..030, FR-OPS-004, SEC-003, SEC-026..029, BR-09; 02 §3 (C14); 04 §4, §16; 07 §6.5–6.6; 09 §4; 16.

# Audit 2026-11-08: web school console flows and permission gating

Web flows auditor of the 2026-11-08 frontend round, worktree from `main` at `813c3996`. Scope: the
school console screens under `apps/web/src/features` and `app/[locale]/(school)`: actions shown only
to permission holders (against `app/authz/permissions.yaml`, `roles.yaml` and the API's `require()`),
links from the sidebar, palette, notifications and breadcrumbs, maker-checker screens, step-up
(SEC-005), the suspended-school state and the Ask screen (D73 page chip, D74 task sources). Items
fixed in earlier rounds (`audit-2026-10-17-web.md` WEB-01..16) were not re-reported.

Each fixed finding has a test that failed before the fix.

## Findings

| ID    | Severity   | Area                                               | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Fix                                                                                                                                                                                                                                                                                                                                                                                          | Test                                                                                                                                                   | Status |
| ----- | ---------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------ |
| WF-01 | Low-Medium | Notifications (FR-NOT-001, FR-FEE-026, FR-RDY-006) | `features/notifications/data.ts` `notificationHref` had no entry for the resource types `fee_request` (templates `fee.approval_requested`, `fee.request_decided`) or `portal_calendar` (`portal_calendar.reminder`) in `apps/api/app/notifications/templates.yaml`. The bell and the notifications screen showed them as plain text, although their body tells the reader to open Fees > Approvals, the decided request or the portal calendar. The approver of a concession, cancellation or refund had no link to the inbox; the maker had no link to the decision and its reason. | `2b0db3b9`: `fee_request` opens `/fees/approvals`; for `fee.request_decided` it opens `/fees/accounts/{account_id}` (from the notification's params, UUID-checked), where the account's request cards show the decision and, after WF-03, its note; `portal_calendar` opens `/findings/calendar`. `notificationHref` now also reads `template_key` and `params` (both in `NotificationOut`). | `notifications.test.tsx`: the specific links, and a drift guard that reads the API's `templates.yaml` and fails when any `resource_type` has no screen | Fixed  |
| WF-02 | Low        | Ask cited page chip (D73)                          | `features/ask/ResolveSource.tsx` `CitedPageButton` awaited `GET /documents/{id}/pages/{n}` and only then called `window.open(url, "_blank", "noopener,noreferrer")`. Browsers allow a new tab only while the click's user activation lasts (about 5 s in Chrome and Firefox), so on a slow office link the tab was blocked silently: with `noopener` `window.open` returns `null`, so nothing fell back to the document screen either.                                                                                                                                               | `abd883b2`: the tab is opened during the click (`opener = null`), pointed at the presigned link when it arrives, and closed on an error before the document screen opens as before                                                                                                                                                                                                           | `cited-page.test.tsx` (two WF-02 tests: the tab exists before the API answers; it is closed on a 404)                                                  | Fixed  |
| WF-03 | Low-Medium | Fee maker-checker (FR-FEE-026, D17)                | `ConcessionOut`, `AdjustmentOut`, `CancellationOut` and `RefundOut` carry `decided_by_name` and `decision_note`, but no fee screen rendered them (`grep decision_note features/fees` was empty). The `fee.request_decided` notification tells the maker to open the request "to read the reason"; the account's concession and adjustment cards (`AccountScreen.tsx` `Requests`) and the receipt's cancellation and refund cards (`ReceiptDetailScreen.tsx`) showed only the status pill, so a rejected request's reason was visible nowhere.                                        | `b13d853e`: shared `DecisionNote` in `features/fees/parts.tsx` ("Note from {name}:" and the note) on all four cards, hidden while pending; i18n key `fees.decisionNote` added at the end of `fees` in en and te                                                                                                                                                                              | `fees.test.tsx` "decided requests show the checker's note" (account concession, receipt cancellation)                                                  | Fixed  |

## Verified clean (no finding)

- **Links**: a script matched every app-relative `href`, `router.push/replace` and route literal in
  `apps/web/src` (sidebar, palette quick actions, bottom bar, breadcrumbs, notification links,
  Ask `sourceHref`) against the 131 pages under `app/[locale]`; none points to a missing route.
- **Permission names**: every permission string the web app passes to `useStaffCan()` or a nav item
  exists in `permissions.yaml` (no misspelt key that would hide an action from everyone).
- **Maker-checker**: change requests hide approve/reject from the requester (`mine` from
  `requested_by` and `/me`, plus the server's `can_decide`); certificates and fee approvals render
  decide buttons only from the server's `can_approve` / `can_decide`; privacy decisions use
  `can_decide` and refuse the person who logged the request. Parent notices let the drafter
  approve by design (`circulars/service.py` `approve_notice`, audited as `self_approved`).
- **Step-up (SEC-005)**: every BFF call goes through `lib/bff/fetch.ts`, which answers a 428 with
  the step-up window and one retry (full-page re-auth if still 428); `StepUpCancelledError` is not
  shown as a generic error. The GET routes that can answer 428 (registers, readiness slips with
  sensitive fields, export/tenant-export download URLs, audit export, import sheet export) are
  fetched through the client first, never opened as a plain link; the certificate print link uses
  a non-step-up reader.
- **Suspended school**: 403 `tenant_suspended` raises the page event and the banner; `/me`
  failure falls back to the read-only state.
- **Ask**: D74 `sos://task/{id}` sources are not parsed statically; they go through
  `ResolveSourceLink` and `GET /knowledge/resolve`, which maps `task` to `/tasks` (one extra round
  trip, works). Search-only, budget and citation states are covered by the existing suites.
- **Fee approvals gating**: the screen's permission set matches `ApprovalsReader`; the account
  screen's request cards use the same `require_any` sets as `ConcessionReader` and
  `AdjustmentReader`.

## Outside my area

- None reported.

## Verification

- `npx tsc --noEmit -p .`, `npx eslint` on the touched features, `npx prettier --check` on touched
  files: clean.
- `npx vitest run src/features/notifications src/features/ask src/features/fees src/i18n`: green.
- Full `npx vitest run`: see the round summary.
- No Playwright spec asserts the notification links or the page chip's `window.open`.

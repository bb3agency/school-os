# ADR-0059: Legal holds suspend retention and erasure for a student or a record class

| Field | Value |
|---|---|
| Status | Accepted (lead, 2026-11-09; owner may revisit; docs/14 D84, PO to confirm) |
| Date | 2026-11-09 |
| Deciders | Lead engineer |
| Amends / supersedes | None. Fulfils the promise of docs/08 §7 ("legal holds that suspend deletion for specific records when the school instructs"); changes the behaviour of every retention job ([ADR-0029](ADR-0029-tenant-data-deletion-at-offboarding.md) purge roles unchanged) and of erasure ([ADR-0048](ADR-0048-dpdp-processor-pack-and-data-principal-requests.md) decision 8) |

## Context

docs/08 §7 promises legal holds, but nothing in `app/` knew about them (FC-12, `docs/research/2026-11-features/compliance-safety.md`). Retention jobs delete behaviour notes and closed early-warning flags after 365 days, Ask history after 180, exports after 7, raw imports after 90, closed breaches after 3 years, and so on; an approved erasure request deletes the erasable classes of a student. If a police inquiry, a court case, a POCSO matter or a dispute with a family touches a child, the school had no way to stop that. DPDP Rule 8 lets a fiduciary keep data where the law requires it.

The retention jobs live in modules that `app.privacy` itself depends on (documents, exports, notifications, students, imports, knowledge, insights, ...): `app.privacy.service` imports their services for access reports and erasure, so they cannot import `app.privacy.service` back without an import cycle.

## Decision

1. **Scope (lead decision D84).** A hold covers **one student** or **one record class for the whole school** (a key of `app/privacy/record_classes.yaml` that holds personal data). Staff members are left out until a staff register exists (FC-04).
2. **Record.** `ops.legal_holds` (migration `0083_legal_holds`; RLS, offboarding purge policy, composite FK to `sis.students`): scope, reason code (from `app/privacy/holds.yaml`, `verified: false`), an optional free-text reference (case, FIR or letter number) that is **C3**, encrypted with the school's DEK, shown only to holders of the permission and never logged, audited, notified or put in an access report or letter; a **required review date** at most 12 months ahead (config); status `pending` → `active` → `lift_pending` → `lifted`, or `pending` → `rejected`. A hold is never deleted (except by offboarding) and never lifted automatically.
3. **Maker-checker with step-up.** New permission `privacy.hold.manage` (critical, step-up), granted to the `owner` and `principal` system roles only. Placing and lifting each take two different people with the permission: one asks, another approves (403 `self_approval_forbidden`; database CHECKs `legal_holds_two_people_place` and `legal_holds_two_people_lift`). The asker may withdraw a pending placement; anyone with the permission may reject it or cancel a lift request. Audit `privacy.hold.requested`, `.placed`, `.rejected`, `.lift_requested`, `.lifted`, `.lift_cancelled` in the same transaction (ids, scope, record class, reason code; never the reference).
4. **Effect.** Holds that are `active` or `lift_pending` stop deletion. The one check is `app.privacy.service.hold_scope(session, record_class)`, which answers "the whole class is held" and "these students are held". Because the deleting modules sit below `app.privacy`, the check reaches them the way `app.core.retention` reaches them for school retention settings: the privacy service registers `hold_scope` with the neutral registry `app.core.holds` at import, and each job calls `holds.check(session, job=..., record_class=...)` in the school's `tenant_session` before it deletes. **Fail closed:** without a registered provider the check answers "everything held" (a missed night of retention is recoverable; a deleted record under a hold is not).
5. **Retention jobs.** Every job that deletes by retention is listed in `app/privacy/holds.yaml` `retention_jobs` with its record class and whether a held student keeps their own rows (`student`: behaviour notes, early-warning flags) or only a whole-class hold stops it (`class`: imports, exports, notifications, Ask history and memory, parent links, messages and inbound events, payment inboxes, UPI narrations, breach records). Jobs that delete bookkeeping, already-deleted objects or control-plane data are listed in `exempt_jobs` with a reason (record classes without personal data, abandoned uploads, orphan vectors, the offboarding purge). `tests/privacy/test_holds_jobs.py` enumerates the registered Celery tasks and the `enforced_by` jobs of `app/admin/retention.yaml`, fails on any deleting job that is in neither list, and proves each listed job calls the check with its class.
6. **Erasure.** The erasure plan marks every class that would be erased or deleted by retention as `legal_hold` ("Held for legal reasons") when the student or the class is held; executing it for such a class is refused (409 `class_not_erasable`); the erasure letter lists those classes as "held for legal reasons". The reason is never shown.
7. **Access report.** A held student's access report has a section "Legal hold: Held: yes" (record class `legal_holds`) and nothing else about it.
8. **Review.** A daily job (`privacy.hold_reviews`, 07:05 IST) sends an in-app notification `privacy.hold.review_due` to the holders of `privacy.hold.manage` 14 days before the review date and every 7 days while it is overdue (config), with ids and counts only.
9. **Web.** The privacy area gets a "Legal holds" page: the list, a place dialog and the approve, reject, lift and cancel actions (step-up).

## Consequences

- Good: the docs/08 promise is met; a school can show that relevant records were kept and why they were kept past their retention; holds are visible, reviewed and cannot be placed or lifted by one person.
- Good: one function answers for every job, and a new retention job without the check fails CI.
- Bad / costs: a whole-class hold stops that class's retention for every student of the school (on purpose: the school chose the class); class-scope jobs cannot keep only one student's rows where the rows are not about one student (raw import files, exports, notifications). A forgotten hold keeps data longer than needed; review reminders are the mitigation, not automatic expiry.
- Bad / costs: every retention run makes one extra small query per school and class.
- Follow-up work: show active holds in the offboarding checklist (offboarding is the school's own exit and is not stopped by a hold today); staff-member scope once a staff register exists; a "keep with a new review date" action (today: lift and place again); counsel to confirm the reason codes (`verified: false`).

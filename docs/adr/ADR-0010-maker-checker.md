# ADR-0010: Maker-checker for identity changes

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

A student's name, date of birth and parents' names flow into board registrations, certificates and government portals. The admission register is the legal anchor for these identity fields (BR-01). A single person changing them, by mistake or on purpose, can cause errors that take weeks to fix or hide misconduct. Schools already expect a second signature for such corrections on paper.

## Decision

- Changes to **identity attributes** (`attribute_definitions.is_identity = true`) are made only through **change requests**: old value, new value, reason and a mandatory **evidence document** (BR-04, FR-CR-001).
- The approver **must differ** from the requester. This is enforced in service code **and** by a database `CHECK (decided_by IS NULL OR decided_by <> requested_by)` on `sis.change_requests` (FR-CR-002, SEC-014).
- Approval requires `student.identity_change.approve` and step-up MFA within the last 5 minutes.
- Approval atomically records a new verified value, supersedes the old one (history kept), re-runs affected data-quality rules, and writes audit events in the same transaction (FR-CR-003).
- Rejection needs a reason; pending requests expire after 30 days (configurable) (FR-CR-004).
- The same two-person rule applies to waiving blocker findings and creating custom roles (07 §6.3).
- A printable correction memo is produced for the paper register (FR-CR-005).
- SchoolOS never auto-corrects official records; mismatches create findings (BR-03; CLAUDE.md §6 invariant 6).

## Consequences

- Good: errors and misuse need two people; every change has evidence and an audit trail.
- Good: matches how offices already work with paper.
- Bad: slower corrections when the approver is unavailable; mitigated by notifications and the 30-day window.
- Bad: small schools may have few approvers; owner and principal both hold the approve permission by default.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Single-user edits with audit only | Audit detects but does not prevent; violates BR-04 |
| Service-level check only | A bug or direct SQL could bypass it; the DB CHECK is defence in depth |
| Automatic correction from "trusted" sources | Violates BR-01/BR-03; the register is the anchor |

## Related requirements

BR-01, BR-03, BR-04, FR-CR-001..005, FR-DQ-020, SEC-014, T3 (07 §4); 05 §5; 07 §6.3; 12 §4.7.

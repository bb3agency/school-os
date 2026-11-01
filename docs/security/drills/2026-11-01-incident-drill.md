# Incident drill report: 2026-11-01 (automated run, CERT-In 6-hour flow)

| Field | Value |
|---|---|
| Date | 2026-11-01 |
| Script | [incident-drill.md](../incident-drill.md) v0.1 |
| Kind | **Automated rehearsal only.** The timeline ran end to end through the API on a pinned clock. The tabletop with real people has **not** been held yet (see actions) |
| Test | `apps/api/tests/platform/test_incident_drill.py::test_SEC_024_incident_drill_cert_in_6h_school_clocks_and_locked_close`, base `main` 2ffe0109; passed (3.6 s run, 12 s setup on testcontainers Postgres) |
| Requirements | SEC-024, FR-DPDP-030..035, PRV-008, PRV-017; ADR-0050, ADR-0054 (D48) |
| Result | **Pass: 16 of 16 steps.** No product bug found. Five gaps and four owner actions below |

> **Not legal advice.** Time limits are only those docs/08 records; the 24-hour Board default is **(to confirm)**.

## 1. What was simulated

All synthetic. Scenario of the script §3: a misconfigured export link exposed attendance and contact records of two schools for about three hours; P1, CERT-In reportable.

| Role | Simulated by |
|---|---|
| Incident lead | Synthetic operator with `platform_owner` (role 30 days old) |
| Second operator | A different synthetic operator with `platform_engineer` (role 30 days old, not granted by the lead) |
| School A (shared tier) | Provisioned through `POST /api/v1/platform/tenants`, with its own keys and a synthetic owner; acts as DPO/principal through the school API |
| School B (dedicated tier) | A deployment row only (no pull; the phone notice is recorded by hand) |
| School A's worker | `breach_sync.sync_school` and `breach_service.send_reminders`, the per-school body of `privacy.breach_sweep` |
| CERT-In point of contact, comms | Not simulated beyond recording the times (no person designated yet) |

The clock read by the incident service, the two-person rules and the breach register was pinned and moved forward step by step; T0 was set a week in the past so that "not in the future" checks see consistent times.

## 2. Timeline and results

T0 = noticed; C = second confirmation = T0 + 1 h; the school's T0 is C.

| # | Time | Step | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1 | T0 + 10 min | Lead opens the incident with `opened_at` = T0 | T0 kept as noticed | `opened_at` = T0 | Pass |
| 2 | T0 + 10 min | CERT-In clock shown to operators | T0 + 6 h on detail and list | `cert_in_due_at` = T0 + 6 h in both | Pass |
| 3 | T0 + 30 min | Both schools added (codes and counts) | Added; nothing reaches a school yet | Added; school A's pull found nothing | Pass |
| 4 | T0 + 45 min / T0 + 1 h | Two-operator confirmation | Lead twice refused; second operator completes; school notice due C + 24 h | 409 `same_operator`; `confirmed_at` = C; `confirm_by` = first + 72 h; both schools `notice_due_at` = C + 24 h | Pass |
| 5 | C + 1 min | School A's worker pulls | One breach record; rerun adds none; operator sees it notified | 1 then 0; `pulled_at` = `school_notified_at` = C + 1 min; `notice_due_at` cleared | Pass |
| 6 | T0 + 2 h | Dedicated school phoned and recorded; status contained | Both schools notified | `schools_notified` = 2; no notice due left | Pass |
| 7 | C + 2 h | School A sees its register | Board notice due C + 24 h (default rule), report C + 72 h, people without a clock; next duty Board notice | As expected; `hours_left` = 22; owner notified of the new notice | Pass |
| 8 | T0 + 5 h 30 min | CERT-In report recorded | ≤ T0 + 6 h; clock cleared | `cert_in_reported_at` = T0 + 5 h 30 min; `cert_in_due_at` null | Pass |
| 9 | C + 20 h | School records Board notice (C + 19 h) and families (C + 20 h) | Recorded, not overdue | As expected | Pass |
| 10 | C + 23 h / 24 h / 30 h / 60 h | Reminders | None at 23 h; `board_24h` at 24 h; nothing new at 30 h; `report_60h` at 60 h; rerun sends 0 | As expected; notifications doubled exactly at 60 h | Pass |
| 11 | C + 66 h | Detailed report (C + 65 h); school closes its breach; reminders at C + 80 h | Within 72 h; no next duty; no further reminder | As expected | Pass |
| 12 | T0 + 4 d | Incident resolved, then closed; PATCH, add school, confirm | 409 `incident_closed` for all three | As expected | Pass |
| 13 | T0 + 4 d 1 h / 2 h | Reopen asked by lead, confirmed by second operator | Request alone keeps it closed; self-confirm refused; second operator reopens; school list stays frozen | `reopen_confirm_by` = request + 72 h; 409 `same_operator`; status `open`; adding a school 409 `incident_confirmed` | Pass |
| 14 | T0 + 4 d 3 h | Closed again | Closed | Closed | Pass |
| 15 | End | Platform audit chain | opened 1, school_added 2, confirmed 2 (lead then second), school_notified 1 (`source: school_pull`), updated ≥ 5, reopen_requested 1, reopened 1 (by the second operator) | As expected | Pass |
| 16 | End | School A audit chain; no personal data on either chain | `notice_received` (actor platform, incident id), 3 `updated` with all three duties; no summary text, name, e-mail or 6+ digit run | As expected | Pass |

## 3. Gaps found

No step failed, so no product code was changed. These are gaps in the process or product, for the owner to decide:

1. **Closing is not gated.** An incident can be set to `closed` while CERT-In is required but not reported, or while a school is still not notified. The script makes this a human check (step 13). Option: refuse closing with 409 while `cert_in_due_at` or any `notice_due_at` is set, or require a close reason.
2. **No alert on the CERT-In or school-notice clocks.** The due times show in the console only; nothing pages the lead as T0 + 6 h or C + 24 h approaches. Option: a platform alert (docs/11 §6) at, say, T0 + 4 h and C + 20 h.
3. **A reopen is not passed on to schools.** Reopening changes nothing on the school side, and a school that already closed its breach cannot change it (409 `breach_closed`). Facts learned after a reopen reach the school only by phone and a breach it records by hand (origin `school`). Option: a follow-up notice from a follow-up incident (the product's message for a new school already says so).
4. **The school's clocks start at SchoolOS's confirmation (C), not when SchoolOS noticed (T0) nor when the school pulled the notice.** In the drill C was T0 + 1 h. This matches ADR-0050 as built; whether the school's Board clock should start at C or at the pull is **(to confirm)** with the legal review of docs/08 §3.
5. **People to play the roles do not exist yet.** No CERT-In point of contact is designated and the platform has one founder `platform_owner`; the two-operator confirmation needs a second eligible operator whose role is at least 7 days old and not granted by the first (docs/16 §5.19). With one person, steps 4-5, 13 and 15 cannot be done in a real incident.

**Follow-up (2026-11-01, wave R3-2G; lead decision D49, docs/14).** Gaps 1-3 are **closed** (migration `0069_incident_reopen_notice`):

- Gap 1: `PATCH` with `status: closed` is refused with 409 `incident_cert_in_unreported` while CERT-In is required and not reported, and 409 `incident_school_not_notified` while a school of the confirmed list is not notified; `close_blocked_by` shows why in the console.
- Gap 2: the beat task `platform.incident_clock_alerts` (every 5 minutes, shared tier) logs `platform.incident.clock_due_soon` (≤ 120 min left) and `platform.incident.clock_overdue` once per incident, clock, school and stage, with CloudWatch alarms on both (docs/11 R13) and audit `incident.clock_alert`. Still open: an alert tile in the console.
- Gap 3: a confirmed reopen is pulled by each shared-tier school (`reopen_count` / `reopen_pulled`); the school's breach records it and a closed breach opens again for review (audit `privacy.breach.incident_reopened`, `incident.school_reopen_pulled`). Still open: recording that a dedicated-host school was told of a reopen by phone.

The automated drill (`test_incident_drill.py`) now has two more steps: **5b** an early close is refused with 409 `incident_cert_in_unreported` (and `close_blocked_by` lists both codes before the dedicated school is phoned); **13b** after the two-operator reopen, school A's next pull reopens its closed breach once (`platform_reopens` = 1; a repeated pull adds nothing; school B's `reopen_pulled` stays 0), and the school closes it again. Gap 4 (school T0) stays **(to confirm)** with counsel; gap 5 is owner action 2. These steps were not part of the run recorded above.

## 4. Actions for the owner

| # | Action | Why | Due |
|---|---|---|---|
| 1 | **Designate the CERT-In point of contact** and register them with CERT-In; write the name into the script's role table and runbook template D | PRV-017; docs/14 §3 pilot gate | Before the pilot |
| 2 | **Name two platform owners** (or an owner and an engineer) with `platform.incidents.manage`, roles granted at least 7 days before go-live and not by each other | Two-operator confirmation and reopen (ADR-0050, D48) | Before the pilot |
| 3 | **Hold the tabletop with real people** using `docs/security/incident-drill.md`, then quarterly (docs/11 §10); write a report in this folder each time | docs/14 §3: "Incident runbook rehearsed" is not met by the automated run alone | Before the pilot, then quarterly |
| 4 | Decide on gaps 1-4 (close gate, clock alerts, reopen follow-up, school T0) | Process risk found by this drill | Done for gaps 1-3 (lead decision D49, built R3-2G 2026-11-01); gap 4 (to confirm) with counsel |

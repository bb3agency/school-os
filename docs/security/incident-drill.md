# Incident drill: tabletop script (CERT-In 6-hour flow)

| Field | Value |
|---|---|
| Version | 0.2 (2026-11-01): R3-2G as built (D49): the close gate (step 13b), clock alerts (§2) and the reopen reaching the school's breach (step 15b). 0.1 (2026-11-01) |
| Purpose | Rehearse a suspected personal-data breach from T0 to closure, including the CERT-In 6-hour report, the two-operator confirmation, each school's breach register and the locked close (docs/14 §3 pilot gate; SEC-024) |
| How often | Quarterly (docs/11 §10), and before the first pilot school goes live |
| Based on | [Breach runbook](../legal/breach-runbook.md), docs/11 §7 and R5/R7, docs/08 §3 (Rule 7, PRV-008) and §6 (PRV-017..019), ADR-0050, ADR-0054 (D48), D49 (docs/14; built R3-2G), docs/16 §5.15a, docs/11 R13 |
| Automated half | `apps/api/tests/platform/test_incident_drill.py` (the same timeline on a pinned clock; run it before the tabletop) |
| Reports | `docs/security/drills/<date>-incident-drill*.md`, one per drill |

> **Not legal advice.** Time limits are only those docs/08 records. Points marked **(to confirm)** are not settled there. Use synthetic schools and made-up facts only; never real student data (invariant 11).

## 1. Roles

| Role | Who plays it | Does |
|---|---|---|
| **Incident lead** (operator 1) | A `platform_owner` or `platform_engineer` | Declares, opens the record, adds schools, first confirmation, CERT-In decision, closes |
| **Second operator** (operator 2) | A different `platform_owner` or `platform_engineer` whose role is at least 7 days old and was not granted by operator 1 | Second confirmation; confirms a reopen |
| **School DPO / principal** | Someone playing the school's owner or principal (synthetic school) | Reads the notice, records the Board notice, family notice and detailed report, closes the school's breach |
| **CERT-In point of contact** | **To be designated by the owner** (docs/08 PRV-017; docs/14 §3) | Files the CERT-In report within 6 hours of T0 |
| **Comms** | Any team member | Phones the schools, keeps the timeline, drafts the school notice (runbook template A) |
| **Facilitator / timekeeper** | Not one of the above | Reads the injects, calls the time, records evidence and pass/fail |

## 2. Clocks

| Clock | Starts | Due | Shown where | Source |
|---|---|---|---|---|
| CERT-In report (SchoolOS) | T0 (noticed) | **T0 + 6 h** | Operator console, `cert_in_due_at` on the incident while CERT-In is required and not reported | docs/08 PRV-017 |
| SchoolOS → school notice | C (second confirmation) | **Target C + 24 h** | Operator console, `notice_due_at` per school until the school is notified | docs/08 PRV-008 |
| Board initial notice (school) | The school's T0 = C for a SchoolOS notice | Without delay; shown overdue after **24 h** (a SchoolOS default, **to confirm**) | School breach register, duty `board_notice` | docs/08 §3 Rule 7 |
| Board detailed report (school) | The school's T0 | **72 h** | Duty `board_report` | docs/08 §3 Rule 7 |
| Affected people (school) | The school's T0 | **Without delay** (no fixed clock) | Duty `people_notified` | docs/08 §3 Rule 7 |
| Reminders to owner and principal | The school's T0 | 24 h and 60 h, while the detailed report is not recorded | In-app notification | FR-DPDP-035 |
| Two-person window | First confirmation or reopen request | 72 h (`two_person_request_ttl_hours`) | `confirm_by`, `reopen_confirm_by` | docs/16 §5.19 |

**Alerts on the SchoolOS clocks (D49, built R3-2G).** The CERT-In clock and each unnotified school's notice clock alert the on-call once when 120 minutes or less are left (`platform.incident.clock_due_soon`, alarm `<prefix>-incident-clock-due-soon`) and once when overdue (`platform.incident.clock_overdue`, alarm `<prefix>-incident-clock-overdue`, P1); each alert is in the platform audit as `incident.clock_alert` (docs/11 R13). Shared deployment only. There is no alert tile in the console yet.

All times in the product are IST; logs are UTC with synchronised clocks (PRV-019). Whether a school must also report its own incidents to CERT-In is **(to confirm)**.

## 3. Scenario (inject at T0)

> 10:00 IST. A platform engineer sees in the access logs that a misconfigured export link let anyone with the link read attendance and contact records of two schools for about three hours. School A is on the shared tier, school B on a dedicated host. About 120 people at A and 45 at B.

Use the synthetic data of `make seed-synthetic` (or the drill test's schools). Every text you type is categories and counts only: no names, numbers, e-mail addresses or record text.

## 4. Timed steps

Operator screens are in the operator console at `/{locale}/platform/incidents` and `/{locale}/platform/incidents/{incidentId}`; school screens at `/{locale}/privacy/breaches` and `/{locale}/privacy/breaches/{breachId}`. API routes are those of `apps/api/openapi.json`; every operator write needs a fresh sign-in, POSTs carry an `Idempotency-Key`, and PATCH needs `If-Match`.

| # | When | Who | What | Screen / route | Evidence to keep | Pass when |
|---|---|---|---|---|---|---|
| 1 | T0 + 0-15 min | Incident lead | Declare P1 (docs/11 §7.1). Open the record with T0 = when it was noticed, `cert_in_required` true if it may be reportable, summary in categories and counts | Incidents → New; `POST /api/v1/platform/incidents` | Incident id; screenshot of the record | Record exists with T0 as noticed (not as opened); the CERT-In clock reads **T0 + 6 h** |
| 2 | T0 + 0-60 min | Incident lead, CERT-In POC | Decide whether CERT-In reporting applies (runbook §2 step 3); if not, set `cert_in_required` false with a reason in the timeline | `PATCH /api/v1/platform/incidents/{id}` | Decision and who made it, timestamped | Decision taken within the first hour |
| 3 | T0 + 0-2 h | Incident lead, engineer | Contain (docs/11 R5/R7: revoke the link, rotate keys, switch off the flag); preserve evidence (docs/11 §7.3). Set status `contained` | `PATCH .../{id}` with `status` | Log exports, snapshots, timeline | Containment action and time recorded |
| 4 | T0 + 0-1 h | Incident lead | Add each affected school: categories, approximate people, window | Incident page → Add school; `POST .../{id}/schools` | List of schools with codes and counts | Every affected school listed; a summary with a phone number, e-mail or 6+ digit run is refused |
| 5 | T0 + ≤ 1 h | Incident lead, then second operator | Two-operator confirmation of the list. Try confirming twice as the lead (must fail) | Incident page → Confirm; `POST .../{id}/confirm` (twice, two people) | Both confirmers and times (audit `incident.confirmed` 1 and 2) | Same operator refused 409 `same_operator`; second operator completes it; C recorded; each school shows notice due **C + 24 h** |
| 6 | C + ~1 min | School A's worker (automatic) | Shared tier: the school's breach register pulls the notice (`privacy.breach_sweep`, every 60 s) | Operator incident page shows "notified"/"pulled"; school: Privacy → Breach register | Pulled time on the operator page; the school's new breach record | A's register holds one `processor_notice` record; a second sweep adds nothing; owner and principal got an urgent in-app notification |
| 7 | C + ≤ 24 h | Comms | Phone the owner or principal of every school (template A of the runbook). Dedicated host B: phone and e-mail, then record it | `PATCH .../{id}` with `notified_tenant_ids` | Call log (who, when), the e-mail sent | Every school shows notified; no `notice_due_at` left |
| 8 | T0 + ≤ 6 h | CERT-In POC | File CERT-In's prescribed form (runbook template D), then record the time | `PATCH .../{id}` with `cert_in_reported_at` | Copy of the submitted form and acknowledgement | `cert_in_reported_at` ≤ T0 + 6 h; the CERT-In clock disappears |
| 9 | School T0 + ≤ 2 h | School DPO / principal | Open the notice; read the facts and clocks; draft the family notice (template B) and the Board outline (template C) | Breach page; `GET /api/v1/privacy/breaches/{id}`, `GET .../drafts/family_notice`, `GET .../drafts/board_outline`, `POST .../notice-draft` | Screenshot of duties and due times | Board notice due school T0 + 24 h (marked as a SchoolOS default), detailed report due + 72 h, people "without delay" |
| 10 | School T0 + ≤ 24 h | School DPO / principal | Notify the Board (initial) and affected families (the parent notice goes through normal notice approval); record both | `PATCH /api/v1/privacy/breaches/{id}` with `board_notice`, `people_notified` | Board acknowledgement; approved notice | Both duties recorded with time and method; not overdue |
| 11 | School T0 + 24 h, + 60 h | System | Reminders to owner and principal while the detailed report is not recorded | In-app notifications | Notification times | One reminder at 24 h, one at 60 h, never twice |
| 12 | School T0 + ≤ 72 h | School DPO / principal | Send the detailed report to the Board; record it; close the school's breach | `PATCH .../{id}` with `board_report`, then `status: closed` | Report and acknowledgement | Recorded within 72 h; no reminder after it |
| 13 | After recovery | Incident lead | Eradicate and recover (docs/11 §7.2 step 5), remediation report to each school, postmortem within 5 working days; status `resolved`, then `closed` | `PATCH .../{id}` | Postmortem (`docs/templates/incident-postmortem.md`) | Closed only after CERT-In is reported (or not required) and every school is notified (enforced since R3-2G, step 13b) |
| 13b | Before step 8 (any time CERT-In is still unreported) | Incident lead | Try to close the incident early | Incident page shows why it cannot close (`close_blocked_by`); `PATCH .../{id}` with `status: closed` | The 409 response | Refused 409 `incident_cert_in_unreported` (and `incident_school_not_notified` while a confirmed school is not notified); once the facts are recorded (they may be sent in the same PATCH as the close) the close passes (D49) |
| 14 | After close | Incident lead | Try to edit the closed incident, add a school, confirm | Same routes | The 409 responses | All refused 409 `incident_closed` (D48) |
| 15 | After close | Incident lead, then second operator | Reopen: one asks with a reason (categories and counts only), a different operator confirms within 72 h. Try confirming your own request (must fail); a waiting request can be withdrawn | Reopen card; `POST .../{id}/reopen`, `POST .../{id}/reopen:confirm`, `POST .../{id}/reopen:withdraw` | Audit `incident.reopen_requested`, `incident.reopened` | Own confirmation refused 409 `same_operator`; status back to `open` only after the second operator; the list of schools stays frozen (409 `incident_confirmed`): a newly affected school needs a follow-up incident |
| 15b | Reopen + ~1 min, then later | School A's worker, then School DPO / principal | Shared tier: the next pull brings the reopen; the school's closed breach opens again for review. The school reviews it and closes it again | School: Breach page (reopen note); operator incident page shows the school's `reopen_pulled`; `PATCH /api/v1/privacy/breaches/{id}` with `status: closed` | Breach status before and after; audit `privacy.breach.incident_reopened` (school) and `incident.school_reopen_pulled` (platform) | A's breach is `open` with `platform_reopens` = 1 and `closed_at` cleared; a second pull adds nothing; owner and principal got the `privacy.breach.incident_reopened` notification; after the school closes it again a further pull leaves it closed. Dedicated host B: told by phone (no record of that in the product yet) |
| 16 | End | Facilitator | Check the audit trails: platform audit log filtered on the incident; the school's audit for its breach; verify the chains | Platform audit log (`platform.audit.read`), school audit | CSV export of both | Every step present, by the right person; no names, numbers, e-mail addresses or summary text in any audit summary |

## 5. Overall pass criteria

- CERT-In report recorded within 6 hours of T0, by the designated point of contact.
- Two different eligible operators confirmed the list; every school notified within 24 hours of confirmation (shared: pulled automatically; dedicated: recorded by hand).
- The school recorded the Board notice within 24 hours (SchoolOS default, **to confirm**), families without delay, and the detailed report within 72 hours of its T0.
- Reminders fired once each at 24 h and 60 h.
- Closing was refused while CERT-In or a school was still owed a notice (D49); the closed incident refused every change; reopening took two operators and reached school A's breach register.
- Both audit chains hold every step with codes and counts only.

## 6. After the drill

Write the report in `docs/security/drills/` (date, who played which role, the timeline with actual times, pass/fail per step, gaps, actions with owners). Fix or record every gap before the next drill.

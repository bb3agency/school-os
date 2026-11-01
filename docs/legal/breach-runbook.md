# Breach runbook and notification templates

| Field | Value |
|---|---|
| template_version | 0.1 (2026-10-21) |
| Decision | [ADR-0050](../adr/ADR-0050-breach-handling-processor-incidents-and-school-breach-register.md) |
| Based on | docs/08 §3 (Rule 7, PRV-008), §6 (CERT-In, PRV-017..019), §9 clause 5; docs/11 §7 (incident response), R5, R7 |
| Rehearsal | Quarterly, with the timed tabletop script [docs/security/incident-drill.md](../security/incident-drill.md) (incl. the CERT-In 6-hour flow) |
| Placeholders | `{{school_short_name}}`, `{{school_legal_name}}`, `{{privacy_contact_name}}`, `{{privacy_contact_phone}}`, `{{privacy_contact_email}}`, `{{noticed_at}}`, `{{incident_window}}`, `{{what_happened}}`, `{{data_categories}}`, `{{approx_people}}`, `{{likely_consequences}}`, `{{what_we_did}}`, `{{what_you_can_do}}`, `{{reference}}`, `{{schoolos_contact_email}}` |

> **Not legal advice.** Time limits are only those docs/08 records. Points marked **(to confirm)** are not settled there.

## 1. Clocks at a glance

| Who | Duty | Clock | Source |
|---|---|---|---|
| SchoolOS | Report a reportable cyber incident to CERT-In | **6 hours** from noticing (T0) | docs/08 PRV-017 |
| SchoolOS | Notify each affected school | Without undue delay; **target 24 hours** from confirmation | docs/08 PRV-008, §9 clause 5 |
| School | Notify each affected data principal (parents for children; staff) | Without delay | docs/08 §3 Rule 7 |
| School | Notify the Data Protection Board | Without delay; SchoolOS shows it overdue after **24 hours** (a SchoolOS default, **to confirm**) | docs/08 §3 Rule 7 |
| School | Detailed report to the Board | **72 hours** | docs/08 §3 Rule 7 |
| School | CERT-In for the school's own incidents | **(to confirm)** whether and when it applies to the school | — |

All times in IST in the product; logs in UTC with synchronised clocks (PRV-019).

## 2. SchoolOS operator steps (processor)

1. **Declare** (docs/11 §7.2): open the incident in the operator console (`platform.incidents`), T0 = time noticed, severity P1 for any suspected personal-data breach. The summary holds **categories and counts only, never names, numbers or record text**.
2. **Contain** (docs/11 R5/R7): revoke sessions or keys, switch off the feature flag, block at the WAF, pause workers, isolate the school if needed. Preserve evidence (docs/11 §7.3).
3. **CERT-In clock**: decide within the first hour whether the incident is reportable; if so, report within 6 hours of T0 using the prepared format and record `cert_in_reported_at`.
4. **Assess impact** per school: data categories, approximate number of people, time window. Two operators confirm the list of affected schools.
5. **Notify each school** (target 24 hours from confirmation): on the shared tier the school's breach register pulls the notice automatically once the second operator has confirmed the list (within about a minute; the school's owner and principal get an urgent in-app notification), and the console records when each school received it; also phone the owner or principal. Dedicated hosts: phone and e-mail plus a critical announcement; the school records it by hand. Use template A.
6. **Support the school** with facts for its own notices; answer its questions within the day.
7. **Eradicate, recover, report**: fix, restore, rotate, verify the audit chain; a remediation report to each affected school; postmortem within 5 working days.
8. **Close**: once an incident is `closed` its fields are locked (the console refuses edits with 409). Reopening it is a recorded action: one operator asks with a reason (categories and counts only), a different operator confirms within the two-person window, and each step is written to the platform audit (lead decision D48, 2026-11-01; ADR-0054; built in R3-2F).

## 3. School steps (fiduciary)

1. Record the breach in SchoolOS (Privacy → Breach register): when it was noticed, what happened, what kinds of data, roughly how many children, parents and staff. A SchoolOS notice arrives there by itself on the shared tier; on a dedicated host record it by hand.
2. Contain it (take the lost device's account offline, revoke parent links, ask SchoolOS for help).
3. Notify the Data Protection Board without delay (template C outline) and record when.
4. Notify affected parents and staff without delay (template B), by the channel you normally use; record when and how.
5. Send the detailed report to the Board within 72 hours of noticing and record when.
6. Keep the record; review what to change.

SchoolOS shows each duty's clock and reminds the owner and principal 24 and 60 hours after noticing while the detailed report is not recorded. It drafts the parents' notice (template B) as a notice for approval and fills the Board outline (template C); it never sends a breach notice by itself.

## 4. Templates

### A. SchoolOS to the school (processor notice)

> Subject: Security incident affecting {{school_short_name}}'s data in SchoolOS ({{reference}})
>
> On {{noticed_at}} SchoolOS became aware of {{what_happened}}. It affected {{data_categories}} of about {{approx_people}} people at your school during {{incident_window}}. Likely consequences: {{likely_consequences}}. What we have done: {{what_we_did}}. What we recommend you and families do: {{what_you_can_do}}.
>
> As the data fiduciary you may need to notify affected families and the Data Protection Board; the clocks and drafts are in your SchoolOS breach register. Our contact for this incident: {{schoolos_contact_email}}. We will send a remediation report.

### B. School to parents or staff (affected data principals)

> **{{school_short_name}}: information about your child's records**
>
> On {{noticed_at}} we learned that {{what_happened}}. The information involved: {{data_categories}}. What this may mean for you: {{likely_consequences}}. What we have done: {{what_we_did}}. What you can do: {{what_you_can_do}}.
>
> We are sorry. If you have questions, contact {{privacy_contact_name}} on {{privacy_contact_phone}} or {{privacy_contact_email}}.

Keep it short and plain; do not name other children. A parent notice made from this template goes through the normal notice approval (M4) before anyone sends it.

### C. School to the Data Protection Board (outline)

Initial notice: who you are ({{school_legal_name}}, contact), what happened and when it was noticed, data categories and approximate numbers, likely consequences, immediate steps. Detailed report (within 72 hours): the same facts in full, cause as known, measures taken and planned, how affected people were told and when, and the processor's report if one applies. **(to confirm: the Board's prescribed form and channel.)**

### D. CERT-In report (SchoolOS)

Use CERT-In's prescribed incident form; include time of noticing, systems affected, type of incident, indicators, actions taken; no personal data beyond what the form requires. Point of contact: the designated CERT-In contact (docs/14 §3).

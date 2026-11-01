# Data Protection Impact Assessment (template for the school)

| Field | Value |
|---|---|
| template_version | 0.2 (2026-11-01) |
| Placeholders | `{{school_legal_name}}`, `{{assessor_name}}`, `{{assessment_date}}`, `{{features_in_use}}`, `{{next_review_date}}` |
| Based on | docs/08 §10 (DPIA outline and M5 inputs), §4, §5, §8, §15.3; docs/07 (controls); `app/privacy/record_classes.yaml` (ADR-0048) |

> **Not legal advice.** Drafted by SchoolOS (D30). Whether the school must carry out a DPIA, and in what form, is **(to confirm)**: docs/08 records it as good practice and a pilot-ready gate item (docs/14 §3), not as a quoted legal duty. Refresh it when the school turns on a new feature or SchoolOS adds a sub-processor (docs/08 §10).

---

**School:** {{school_legal_name}} · **Assessed by:** {{assessor_name}} · **Date:** {{assessment_date}} · **Features in use:** {{features_in_use}} · **Next review:** {{next_review_date}}

## 1. Describe the processing

What data, about whom, for which purposes, where it is stored and who receives it. Start from the record classes (DPA Annex A and D) and the sub-processor list (DPA Annex C). Mark features the school does not use.

## 2. Necessity and proportionality

For each purpose: why it is needed, the lawful basis the school chose (legal obligation, legitimate use for its educational activities and child safety, or consent), and why less data would not do. The education exemption covers educational activities and child safety only (docs/08 §4).

## 3. Risks to children and families

| Risk | Where it could happen | Likelihood / severity (school's view) |
|---|---|---|
| Exposure of a child's record to someone who should not see it | Shared office PCs, staff phones, forwarded parent links, exports | |
| Misidentification (wrong child, wrong certificate) | Imports, duplicates, transfers | |
| Over-monitoring of children | Early-warning indicators (M5) | |
| AI error (wrong or unsupported answer) | Ask the school | |
| Sensitive status revealed (caste, RTE admission, health) | Lists, prints, messages | |
| Loss of records | Device loss, deletion, outage | |
| Forwarded WhatsApp links | Parent pages | |

## 4. Controls (what SchoolOS does; school to confirm)

Tenant isolation and role and class-based access; step-up sign-in for exports and approvals; no full Aadhaar numbers; Aadhaar masking in every text pipeline; maker-checker for identity changes with evidence; restricted fields encrypted with the school's key; audit of every change and export; no personal data in logs; AI answers cite sources and see only what the asking user may see; insights visible only to the class teacher and principal, rules not AI; parent links expire and can be revoked; access reports released only by a second staff member with step-up sign-in and downloadable for 24 hours; erasure refused for official records and accounts, with the reason given; consent decisions kept with the form version the parent signed; backups in India; breach process with clocks (docs/07, docs/08, ADR-0048, ADR-0049, ADR-0050).

## 5. Controls the school must run

Staff training and confidentiality; who holds which role; how parents are told (the notice and its version); how requests and grievances are answered within 7 days; device rules for office PCs; keeping signed consent forms; when to use break-glass approval.

## 6. Residual risk and sign-off

| Residual risk | Accepted by | Date |
|---|---|---|
| | | |

Feature-specific inputs already written by SchoolOS: student insights (docs/08 §10 table), fees, WhatsApp, parent pages and certificate verification (docs/08 §15.3), the disclosure page (ADR-0051: no student data), the RTE register (ADR-0052, when built).

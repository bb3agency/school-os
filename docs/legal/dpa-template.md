# Data Processing Agreement (template)

| Field | Value |
|---|---|
| template_version | 0.2 (2026-11-01) |
| Placeholders | `{{school_legal_name}}`, `{{school_address}}`, `{{schoolos_legal_name}}`, `{{schoolos_address}}`, `{{effective_date}}`, `{{tier}}` (shared or dedicated), `{{school_signatory}}`, `{{schoolos_signatory}}`, `{{privacy_contact_name}}`, `{{privacy_contact_email}}`, `{{schoolos_contact_email}}`, `{{governing_law_place}}`, `{{objection_days}}`, `{{subprocessor_table}}`, `{{retention_table}}` |
| Based on | docs/08 §1 (roles, sub-processors), §3 (obligations), §6 (CERT-In), §7 (retention, offboarding), §9 (key clauses), §14-§15 |

> **Not legal advice.** Drafted by SchoolOS (owner decision D30) from docs/08. Have it reviewed professionally before the first paid school signs. Clauses marked **(to confirm)** are not settled by docs/08.

---

**Between** {{school_legal_name}}, {{school_address}} (the **School**, the data fiduciary) **and** {{schoolos_legal_name}}, {{schoolos_address}} (**SchoolOS**, the data processor), effective {{effective_date}}.

## 1. Purpose and roles

1.1 The School decides why and how personal data of its students, their parents and guardians, and its staff is processed. SchoolOS processes that data only to provide the SchoolOS service described in the subscription and only on the School's documented instructions (this agreement, the School's settings and actions in SchoolOS, and written instructions from the School's authorised contact).

1.2 Under the Digital Personal Data Protection Act, 2023 and the DPDP Rules, 2025, the School is the Data Fiduciary and SchoolOS a Data Processor (docs/08 §1).

## 2. Data and purposes (Annex A)

2.1 Categories of data principals, categories of data and purposes are listed in Annex A. SchoolOS does not process the data for any other purpose: no advertising, selling, marketing, profiling or cross-school analytics on identifiable data (docs/08 PRV-003, invariant 14).

2.2 SchoolOS never stores full Aadhaar numbers; only the last four digits and the name, date of birth and gender as printed are kept to detect mismatches (docs/08 §5).

## 3. Confidentiality and SchoolOS personnel

3.1 SchoolOS personnel are bound by confidentiality. SchoolOS staff have **no standing access** to the School's data. Support access needs the School's approval in SchoolOS, is time-limited, read-only, recorded in the School's audit log and revocable by the School at any time (docs/07 §6.4). Emergency access without approval exists only for a legal obligation or an active security incident, needs two SchoolOS operators, and is reported to the School within 24 hours.

## 4. Security safeguards (Annex B)

4.1 SchoolOS implements the safeguards in Annex B, including encryption, access control by role and class, tenant isolation, logging and monitoring, backups and their testing, and keeps security logs for at least one year in India (docs/08 §3 Rule 6, §6 PRV-018).

## 5. Sub-processors and providers the School contracts directly (Annex C)

5.1 The School authorises the sub-processors in Annex C. SchoolOS gives the School advance notice of any new or replaced sub-processor; the School may object on reasonable grounds within {{objection_days}} days **(to confirm: period)**. SchoolOS flows down obligations equivalent to this agreement.

5.2 Providers the School contracts directly (its own payment aggregator account, its own WhatsApp Business Account) are not SchoolOS sub-processors; SchoolOS sends them only what each payment link or message needs, on the School's instruction (docs/08 §1, §15).

## 6. Personal data breaches

6.1 SchoolOS notifies the School **without undue delay after confirming a breach affecting the School's data, with a target of 24 hours** (docs/08 §3 PRV-008, §9 clause 5); on the shared tier the notice appears in the School's breach register in SchoolOS, and SchoolOS also contacts the School directly, and gives the facts the School needs for its own notices to affected people and to the Data Protection Board (nature, extent, timing, likely consequences, mitigation, what affected people can do, contact).

6.2 SchoolOS reports cyber incidents it is required to report to CERT-In within six hours of noticing them (docs/08 §6 PRV-017).

6.3 SchoolOS cooperates with the School's investigation and gives a written remediation report. The process is in `docs/legal/breach-runbook.md` (ADR-0050).

## 7. Assistance

7.1 SchoolOS provides tools for the School to answer data principals' requests for access, correction, erasure, grievance, nomination and withdrawal of consent, aiming for the School to answer within 7 days (NFR-PRV-004; ADR-0048); access reports are released by a second School staff member and handed over by the School, and SchoolOS sends them to no one. It also keeps the School's consent records and breach register (ADR-0049, ADR-0050), and assists with the School's data protection impact assessment (docs/08 §10; `docs/legal/dpia-template.md`).

## 8. Data location and transfers

8.1 Data is stored in India for both tiers (AWS ap-south-1, Mumbai; backups ap-south-2, Hyderabad). AI processing uses Google Cloud Vertex AI in asia-south1 (Mumbai). Any processing outside India is disclosed in Annex C before it starts and is checked against any country the Central Government restricts (docs/08 §3 PRV-012).

## 9. Retention, return and deletion

9.1 SchoolOS keeps data for the periods in Annex D (from docs/05 §13) or as the School configures within them, and supports legal holds the School asks for.

9.2 At the end of the subscription the School takes its full data export; SchoolOS then deletes the School's data within 30 days, destroys the School's encryption keys and issues a certificate of deletion. Backups expire on their normal schedule (stated in the certificate). The School's audit log (identifiers, codes and counts only) is kept 366 days after the certificate and then deleted; signed audit archives expire after 3 years (docs/08 §7, ADR-0029).

## 10. Audit

10.1 SchoolOS provides on request a summary of its security controls, the latest penetration-test summary and answers to reasonable questionnaires; a reasonable on-site review may be agreed with notice (docs/08 §9 clause 9).

## 11. Liability, term and law

11.1 Liability, indemnities, term and termination follow the subscription terms (`docs/legal/terms-of-service-template.md`). **(to confirm: liability caps and indemnities with a professional reviewer.)**

11.2 This agreement is governed by the laws of India; courts at {{governing_law_place}} **(to confirm)**.

Signed for the School: {{school_signatory}} · Signed for SchoolOS: {{schoolos_signatory}} · Date: {{effective_date}}

---

## Annex A: Data principals, data and purposes

| Data principals | Data | Purpose |
|---|---|---|
| Students | Identity and enrolment (name, date of birth, gender, admission number, class, UDISE+ PEN, APAAR ID), Aadhaar last four digits and as-printed fields, documents, attendance and marks, certificates, fee records, consents | School administration, statutory and board submissions, education and child safety (docs/08 §11) |
| Parents and guardians | Name, relationship, phone, address, consents and WhatsApp opt-ins, fee payments | Contacting families, fees, consents |
| Staff | Name, e-mail, phone, role, sign-in and audit records, Ask questions and memory | Running the school's office, security and accountability |

## Annex B: Security safeguards

Summary of docs/07: tenant isolation with row-level security; role and class-based access with step-up sign-in for sensitive actions; encryption in transit and at rest, with the School's own keys for restricted fields; Aadhaar masking in every text pipeline; hash-chained audit log; no personal data in logs; backups in India with restore drills; incident response with the CERT-In 6-hour process.

## Annex C: Sub-processors (current list: docs/08 §1)

{{subprocessor_table}} (copied from docs/08 §1 on the effective date, with the providers the School contracts directly listed separately).

## Annex D: Retention

{{retention_table}} (the record classes of `app/privacy/record_classes.yaml`, ADR-0048, which match docs/05 §13).

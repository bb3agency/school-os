# 08 · Privacy & Compliance

| Field | Value |
|---|---|
| Version | 0.2 · 2026-09-26 |
| Laws/regimes | DPDP Act 2023 + DPDP Rules 2025 · IT Act 2000 / CERT-In Directions (Apr 2022) · UIDAI Aadhaar rules |
| Related | 05-Data model §8–13, 07-Security, 11-Operations §7 (incident response), 16-Platform admin panel, ADR-0015, ADR-0016 |
| Changes | 0.2: data location for shared and dedicated tiers; payments provider listed as proposed (not active); platform, billing and support data (§14); records of processing and DPA clause updated. 0.1: baseline |

> **Not legal advice.** This document records engineering and product commitments based on public sources checked in September 2026. Have a qualified lawyer review the DPA, notices and incident process before handling real data.

---

## 1. Roles

| Party | DPDP role | Responsibility |
|---|---|---|
| School | **Data Fiduciary** | Decides purposes and means; issues notices; handles rights requests and breach notifications to parents and the Data Protection Board |
| SchoolOS | **Data Processor** | Processes only on the school's documented instructions; provides security safeguards, tooling, and assistance |
| Sub-processors | Processors engaged by SchoolOS | Bound by equivalent obligations; disclosed to schools |

**Sub-processor register (initial; keep current and notify schools of changes)**

| Sub-processor | Purpose | Data | Location |
|---|---|---|---|
| Amazon Web Services | Hosting (shared tier and dedicated-tier hosts), storage, backups, KMS, identity (Cognito), email (SES) | All platform data (encrypted) | India: ap-south-1 (Mumbai), backups ap-south-2 (Hyderabad), for both tiers |
| Anthropic (Claude API) | Answer generation, extraction, metadata | Minimized question context; record fields needed for the answer; document excerpts | Outside India; commercial API terms; Zero Data Retention requested |
| Embeddings provider (e.g., Voyage AI) or self-hosted model | Vector embeddings | Document chunk text, queries | Provider-dependent; self-hosted option in India |
| OCR provider (chosen by evaluation) | Text extraction from scans/photos | Page images (after Aadhaar-region redaction where detectable) | Provider-dependent |
| Payment provider (Razorpay candidate) | **Proposed, not active** (ADR-0016). Online collection of SchoolOS subscription payments | School billing contact and invoice amounts only; never student data | To confirm in the privacy review before activation |

The register above covers processing of school data. A payment provider would process SchoolOS's own billing data (§14); it is listed so schools see every third party that could receive data about them. It is added to the active register, and schools are notified per the DPA, only if ADR-0016 is accepted after a privacy review.

## 2. DPDP timeline

The DPDP Rules were notified in November 2025 with phased commencement. The substantive day-to-day obligations (notices, security safeguards, breach notification, erasure, children's data) apply from **mid-May 2027** (sources cite 13 or 14 May 2027; verify the exact date). SchoolOS is built compliant-by-design from M0 so schools are ready before that date.

## 3. Obligation mapping

| Obligation (DPDP) | School (fiduciary) must | SchoolOS provides | Control |
|---|---|---|---|
| **Notice** (standalone, clear, itemized data and purposes, how to withdraw/complain) | Issue notices to parents/staff | Bilingual (EN/TE) notice templates; record of notice versions issued | PRV-001 |
| **Lawful basis** (consent or legitimate use) | Decide basis per purpose | Purpose registry per data category; consent records where consent is used | PRV-002 |
| **Children's data**: verifiable parental consent; no tracking/behavioural monitoring/targeted ads | Obtain consent where required; rely on exemptions only within their conditions | Purpose-bound features; no ads or marketing; consent capture tooling; insights limited per §4 | PRV-003..006 |
| **Security safeguards** (Rule 6): encryption/obfuscation/masking/virtual tokens, access control, logs + monitoring + review, backups, retain logs and personal data 1 year for breach detection/investigation, processor contract terms, organizational measures | Ensure processors implement safeguards | 07-Security controls; log retention ≥ 1 year; contract clauses (§9) | PRV-007 |
| **Breach notification** (Rule 7): notify each affected Data Principal without delay (nature, extent, timing, consequences, mitigation, what they can do, contact); notify the Board without delay, then a detailed report within 72 hours | Send notices and Board reports | Processor notice to school without undue delay (target: initial notice within 24 h of confirmation, sooner where possible); facts package; bilingual parent-notice templates | PRV-008 |
| **Retention & erasure** (Rule 8): erase when purpose is served unless law requires otherwise; minimum 1-year retention of personal data, traffic data and logs for specified purposes | Set retention per category | Retention settings, purge jobs, deletion certificates, legal holds | PRV-009 |
| **Data Principal rights**: access, correction/completion/updating, erasure, grievance, nomination | Respond to requests | Admin tools: per-student data report, correction via change requests, erasure workflow, grievance log | PRV-010 |
| **Contact details** of the person handling data queries | Publish | Displayed in notices and app footer (school-configured) | PRV-011 |
| **Cross-border transfer**: permitted except to countries restricted by Central Government notification | Accept processor locations | Data stored in India; foreign sub-processors disclosed; minimization; check restricted-country list before adding any provider | PRV-012 |

## 4. Children's data: design rules

The DPDP Rules' Fourth Schedule (Part A) exempts **educational institutions** from the parental-consent requirement and the tracking/behavioural-monitoring prohibition **only** where processing is restricted to (a) the institution's educational activities, or (b) the safety of enrolled children, and subject to conditions such as necessity, proportionality and data minimization. SchoolOS therefore:

| ID | Rule |
|---|---|
| PRV-003 | Processes student data only for school administration, education and safety purposes configured by the school. No advertising, no selling, no marketing use, no cross-tenant analytics on identifiable data. |
| PRV-004 | Student insights (M5: attendance/marks trends, early warnings) are visible only to staff with an educational role for that student (class teacher, principal, counsellor role if added) and are used to plan support, never for punishment or profiling beyond education. |
| PRV-005 | No automated decisions with significant effects on a child; every flag requires a human to act. |
| PRV-006 | Where the school chooses consent (e.g., optional features), SchoolOS records who consented, when, for what, and supports withdrawal. |

**Exports (ADR-0021).** Board and portal pre-checks and student lists copy children's personal data out of the system in bulk, so creating any export needs a fresh MFA sign-in. Restricted values such as the UDISE+ social `category` are included only when a member allowed to see them explicitly asks (`include_sensitive`), and the audit log records which restricted columns were included (never the values). Only the requester, and members the school has given `export.download_any` (the owner by default; always with a fresh MFA sign-in), can download an export's files; every download is audited, including whether it was someone else's export. Files are deleted 7 days after they are ready (05 §13).

## 5. Aadhaar handling

UIDAI circulars have directed organisations storing Aadhaar numbers in databases to keep them in a separate encrypted "Aadhaar Data Vault", and which organisations must comply has become unclear over time. SchoolOS avoids the question by design (ADR-0007):

| ID | Rule |
|---|---|
| PRV-013 | Full Aadhaar numbers are never stored, displayed, logged, exported, embedded or sent to any AI provider. |
| PRV-014 | Only `aadhaar_last4` and the as-printed name, date of birth and gender are stored (C3, encrypted) to detect mismatches. Display format: `XXXX XXXX 1234`. |
| PRV-015 | Every text pipeline (OCR, extraction, document ingestion, imports, logs, prompts) masks 12-digit sequences that pass the Verhoeff checksum. |
| PRV-016 | If an uploaded image appears to contain a full Aadhaar number (detected via OCR + Verhoeff), the stored image is **redacted** (region blacked out using OCR bounding boxes) and the original discarded. The upload UI warns: "Don't upload Aadhaar card images. Enter only the last 4 digits." |

## 6. CERT-In directions and logging

The April 2022 CERT-In directions apply to service providers and body corporates in India. SchoolOS commits to:

| ID | Requirement | Implementation |
|---|---|---|
| PRV-017 | Report specified cyber incidents to CERT-In within **6 hours** of noticing | Incident runbook with a 6-hour clock and pre-drafted report format (11 §7); designated Point of Contact registered with CERT-In |
| PRV-018 | Maintain ICT system logs for a rolling **180 days within India** | Security/access/application logs retained **13 months** in ap-south-1 (also covers DPDP's 1-year requirement) |
| PRV-019 | Synchronize clocks to NIC/NPL NTP servers or traceable sources | Self-managed hosts use NIC/NPL NTP; managed services' time sync documented; all logs in UTC with synchronized clocks |

## 7. Retention schedule (defaults)

See 05-Data model §13. Principles: keep official school records per the school's legal obligations; delete working data (imports, exports, AI query logs) quickly; keep security and audit logs at least 1 year (in India); support **legal holds** that suspend deletion for specific records when the school instructs.

**Retention settings (FR-ADM-002, as built):** the owner or principal may shorten the retention of working data (raw import files 7–90 days, export files 1–7, read notifications 30–90) on the Data retention screen; SchoolOS never keeps working data longer than the defaults, and audit/security logs (≥ 13 months) and the full-export archive (24 hours) are fixed. Every change is audited (`admin.retention.updated`). Official student records and uploaded documents are never deleted automatically. The minimums are engineering choices pending legal review.

**Full data export (FR-ADM-001, as built):** the owner (`tenant.export_all`, fresh MFA sign-in) exports every record table as CSV and JSON, every document that passed the virus scan and the audit log as CSV in one archive, downloadable for 24 hours. Restricted (C3) values are masked unless the owner explicitly includes them (needs `student.read_sensitive`; the audit event lists the restricted fields included); the Aadhaar-as-printed name, date of birth and gender are never exported and full Aadhaar numbers are never stored. This is the export the school takes before offboarding, and it stays available while a school is suspended (16 §5.5).

Offboarding: school exports data → SchoolOS deletes tenant data within 30 days → destroys tenant keys (crypto-shredding, which also makes backup copies unreadable) → issues a certificate of deletion. Backups age out on their normal schedule.

## 8. AI-specific transparency

- Staff are told that answers are AI-generated from school records and documents, with sources shown; answers without valid sources are flagged or withheld.
- Prompts and outputs are not used to train models (commercial API terms; ZDR requested for the production organization).
- Only the fields needed for a question are sent; C3 fields only when the user is permitted and asked for them; never Aadhaar data.
- Parents' notices (template) mention that the school uses a software provider, including AI-assisted search, under a data processing agreement.

## 9. Data Processing Agreement (key clauses)

1. Processing only on documented instructions; purposes and data categories listed.
2. Confidentiality obligations for SchoolOS personnel; no standing access; break-glass with school approval.
3. Security safeguards per 07-Security, including encryption, access control, logging (≥ 1 year), backups, testing.
4. Sub-processors: current list, advance notice of changes, right to object, equivalent obligations flowed down.
5. Breach: notice to the school without undue delay (target ≤ 24 h from confirmation), cooperation, facts package, remediation report.
6. Assistance with Data Principal requests and DPIAs.
7. Data location: India for storage in both tiers (shared platform and dedicated hosts in ap-south-1, backups in ap-south-2); disclosed exceptions for AI sub-processors.
8. Return and deletion at termination; certificate of deletion; crypto-shredding.
9. Audit rights: summary security reports, pen test summaries, reasonable on-site review.
10. Liability, term, governing law (lawyer to draft).

## 10. DPIA outline (for the school, supported by SchoolOS)

Describe processing → necessity and proportionality → risks to children and parents (exposure, misidentification, over-monitoring, AI error) → controls (07/08) → residual risk and sign-off. Required refresh when adding M5 insights or any new sub-processor.

## 11. Records of processing (summary)

| Activity | Data categories | Purpose | Basis (school decides) | Retention |
|---|---|---|---|---|
| Student records | Identity, enrolment, guardians (C2/C3) | School administration, statutory/board submissions | Legal obligation / legitimate use / consent where required | School policy |
| Data-quality checks | Identity fields per source | Accuracy of official records | As above | With records |
| Documents & knowledge base | Circulars, minutes, scans | Administration, institutional memory | As above | School policy |
| Ask the school | Questions, retrieved excerpts | Staff productivity | As above | Encrypted Q/A 180 days |
| Audit & security logs | User actions, IP hashes | Security, accountability | Legal obligation (DPDP/CERT-In) | ≥ 13 months online |
| Support tickets (school-opened) | Staff user ID, ticket text (student data not allowed; redacted) | Support | Legitimate use (contract) | 1 year after closing |
| Fleet heartbeat (dedicated tier) | Versions, health, aggregate counts; no personal data | Operating the service | Not personal data | Last payload + daily counts |

## 12. Compliance calendar

| When | Task |
|---|---|
| Before first real data | DPA signed; school notice issued; DPIA done; sub-processor list shared; ZDR request submitted |
| Before first dedicated-tier school | Confirm DPA annex describes the dedicated host (location, backups, custom domain); restore test done |
| Before activating any payment provider | Privacy review, sub-processor register update, advance notice to schools (ADR-0016) |
| Quarterly | Access reviews per tenant; restore drill; incident drill; sub-processor review |
| Annually | Penetration test; policy review; DPA review; key rotation review |
| Before mid-May 2027 | Full DPDP readiness review with counsel; update notices/templates to final rules |

## 13. References

- DPDP Rules 2025, Rule 6 (security safeguards): https://www.dpdpa.com/dpdparules/rule6.html
- DPDP Rules 2025, Rule 12 and Fourth Schedule (children's data exemptions): https://dpdpa.com/dpdparules/rule12.html · https://dpdp.myndsolution.com/wiki/rules/schedule-4-classes-of-data-fiduciaries-in-respect-of-whom-provisions-of/
- DPDP Rules explained (Rules 6–8 incl. 72-hour Board report, 1-year logs): https://dpdp.myndsolution.com/wiki/guides/the-dpdp-rules-2025-explained/
- CERT-In Directions 2022 overview (6-hour reporting, 180-day logs in India): https://www.mondaq.com/india/security/1191962/an-overview-on-the-cert-in-cyber-security-directions-2022
- Anthropic API and data retention (ZDR): https://platform.claude.com/docs/en/manage-claude/api-and-data-retention

## 14. Platform, billing and support data

SchoolOS's own business data lives in the `platform` schema (16 §7), separate from school data and unreachable by the school app except through narrow functions.

| Data | Examples | Role of SchoolOS | Class | Retention |
|---|---|---|---|---|
| Billing accounts | School legal name, GSTIN, PAN, billing address, billing email and phone, billing contact name | **Data Fiduciary** for the contact person's data (its own customer relationship) | C1 (school) / C2 (contact person) | 8 years after the financial year (tax records; confirm with a CA) |
| Invoices and payments | Amounts, GST, UTR/UPI references, TDS | Own records | C1 | 8 years after the financial year |
| Operator accounts | SchoolOS staff name, email, roles | Employer / own records | C2 | Life of account + 1 year |
| Usage aggregates | Counts per school per day (users, students, storage, AI tokens) | Own operations | C1 (no personal data) | 3 years |
| Support tickets | Subject and messages from school staff | Processor for any school data that slips in; own records otherwise | C2 at most; student data not allowed | 1 year after closing |
| Deployment records and heartbeats | Versions, health, backup state, counts | Own operations | C1 | Last payload; history in metrics |

Rules:
- **Billing data is school business data, never student data.** Invoices name the school, not pupils; per-student plans use counts only.
- **Support tickets must not contain student data.** The form warns in English and Telugu; Aadhaar-like and phone numbers are masked before storage; operators flag and redact anything that slips through; tickets are deleted 1 year after closing. Investigating a school's records uses break-glass, not tickets.
- **Heartbeats carry no personal data** (16 §12.3); the schema has no free-text fields.
- **Dedicated tier location:** the school's host runs in ap-south-1; its bucket and backups (encrypted with its own KMS key) are in ap-south-1 and ap-south-2. The control plane receives only heartbeat data from it. A custom domain changes the web address, not where data is stored.
- **After offboarding**, school data is deleted and keys destroyed (§7); billing records remain for their legal retention period because they contain no student data.

# ADR-0039: APAAR consent register, the 11-digit PEN and the transfer-in duplicate guard

| Field | Value |
|---|---|
| Status | Accepted (owner decisions D1, D3 and D9 of 2026-10-10, docs/18 §8; built on `wip/r1-apaar-udise`) |
| Date | 2026-10-10 |
| Deciders | Product owner (R1 scope, D3 public sources, D9 parent language); engineering (design) |
| Amends / supersedes | Amends ADR-0037 (decision 6 "no consent tracker"; the PEN format "to confirm"); relies on ADR-0036 with the D9 exception for one parent-facing form |

## Context

R1 of the 2026-27 plan (docs/18 §3) leads with board and portal readiness. Three gaps sit next to what ADR-0037 built:

1. **Consent.** APAAR IDs are generated in UDISE+ only after a parent consents. The Supreme Court's order of 20 July 2026 in *Abhishek Baxi & Ors. v. Union of India* (WP(C) 832/2026) reportedly directs that the model consent form let parents **expressly refuse**, and that APAAR data handling follow the DPDP Act 2023. *Source: a secondary summary (educationforallinindia.com), not the order itself; docs/research/2026-10-market/notes/regulation_portals.md §2.* CBSE nevertheless asks for APAAR at registration from 2026-27. Schools today track consent on paper or not at all, and nothing stops software from "nagging" a refused child to get an APAAR ID. ADR-0037 decision 6 left a consent tracker for a later ADR.
2. **PEN.** The UDISE+ Permanent Education Number was stored as 1-20 letters or digits "until the format is confirmed". Public guides consistently describe an 11-digit number generated once per child for life.
3. **Duplicates across schools.** On a transfer the new school should import the child into UDISE+ **by PEN**; creating a fresh record leaves the child "active in two schools", a known UDISE+ pain point. SchoolOS had no check that a PEN or APAAR ID was already on another record.

## Decision

1. **A consent register, append-only (FR-APC-001..003).** New module `app.apaar` and table `sis.apaar_consents` (migration `0051_apaar_consent_pen`). One row per decision a parent made on the form: `given`, `refused`, `pending` (form sent, awaited) or `withdrawn`; who decided (relationship, and the guardian when on the record), the date on the form, the form's language, the signed form as an evidence document (an ordinary `documents` upload, linked by composite FK; **required for `given`**), an optional short note, who recorded it and when. The highest `seq` per student is the current state; no row means `pending`. The app role has SELECT and INSERT only: nothing is ever overwritten (history kept; DB grants enforce it). `withdrawn` follows only `given`; once a parent decided, the state never returns to `pending`. A guardian record later deleted for data minimisation only clears the link (`ON DELETE SET NULL (guardian_id)`); the decision and its relationship stay.
2. **Refusal is a first-class answer (FR-APC-004, FR-APC-006).** The printed form offers "I give consent" and "I do not give consent" side by side and says admission and studies do not depend on the answer. Students whose parents refused or withdrew are **never pushed to an APAAR action**: `apaar.refused_student_ids()` makes the APAAR readiness rules DQ-009 and DQ-022 skip them (a refusal re-checks the student at once); lists show "refused", never "missing". The other engineer's APAAR failure list uses the same function.
3. **Printable forms (FR-APC-004).** A4 HTML print pages (one per student, or a whole section, optionally only `pending`), served like certificate print views (no scripts, style allowed by hash in the CSP), audited (`apaar.consent_form.printed`). They print only what the office holds (name, admission number, class, date of birth, PEN), never an Aadhaar number (invariant 4; values pass the Aadhaar mask too). The text is versioned configuration (`app/apaar/config.yaml`, `form_version`) in English and Telugu.
4. **Parent language per school (owner decision D9).** `sis.apaar_consent_settings.form_language` (`en` or `te`, `tenant.settings.manage`, step-up) chooses the form's language; a print can override it. This is independent of `SOS_TELUGU_ENABLED`, which keeps the staff UI English (ADR-0036). When the planned school-wide parent-language setting (ADR-0036 amendment, R2) lands, this setting should read it.
5. **Summary and follow-up (FR-APC-005).** Counts per section (given / refused / pending / withdrawn) and the list of students with their state; `status=pending` is the follow-up list.
6. **Permissions.** `apaar.consent.read` (owner, principal, office admin, office staff, exam coordinator, auditor; class teachers scoped to their sections) and `apaar.consent.record` (principal, office admin, office staff). Both `sensitive`, no step-up. Existing schools need the post-migration system-role sync (ADR-0022).
7. **PEN is 11 digits (FR-STU-017).** `udise_pen` validation: spaces and hyphens removed, then exactly 11 digits (`digits11_required`); sources `udise_plus`, `tc_incoming` (the PEN printed on the previous school's TC) and `manual_entry`. Values recorded before keep their form.
8. **Transfer-in guard (FR-STU-018, FR-IMP-010).** A PEN or APAAR ID already held (current, not rejected) by another **active or provisional** student of the school is refused on create, on a new value and in imports (422 / row error `national_id_in_use`, naming the existing record's id when the caller may read it). Writes of one number take a transaction advisory lock so two clerks cannot both win. `admission_kind: transfer_in` on a new student requires a PEN. `POST /students/national-id-check` tells the clerk before admitting whether the number is on a current or former record and what to do in UDISE+ (`open_existing_record`, `readmit_existing_record`, `import_by_pen`, `new_udise_record`). Left and graduated records do not block (a re-admission reuses the old record; DQ-021 still flags any duplicate that arises).
9. **Search by PEN (FR-STU-019)** exactly like the APAAR ID: `udise_pen` in the `POST /students/search` body, current non-rejected values, same scope.
10. **UDISE+ 2026-27 profile (FR-EXP-006).** `udise-plus.yaml` lists the Student Module fields (general profile, enrolment profile, identifiers) with where SchoolOS holds each (`portal_fields`), `source:` URLs and `verified: false` (owner decision D3); the export layout (version 3) follows the portal order for every field SchoolOS holds. Fields SchoolOS does not hold (Aadhaar number, BPL, blood group, medium, ...) are listed for the clerk, never invented. Exports stay check sheets: no automated portal submission (BRD non-goal).

## Consequences

- Good: the office can show who consented, who refused and who is still to answer, with the signed form, before the CBSE/UDISE+ windows; refused children are respected everywhere; a transfer-in cannot silently become a second UDISE+ record; the PEN is validated and searchable.
- Bad / costs: one more module and two tables to purge and export; the consent text and the court order are from secondary sources and must be checked against the official revised form; the Telugu form is an engineering translation awaiting a native reviewer.
- DPDP: the register itself is personal data of a child (C2), part of the school's record; it serves the school's own compliance (data fiduciary). It is the seed of the R3 consent register for DPDP.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| A consent status attribute in the student catalogue | Attribute values are per source with canonical resolution; consent needs its own history, evidence rule and transitions |
| Overwrite the current state in place | Loses the history an inspector or parent may ask for; append-only is simpler to trust |
| Block APAAR duplicates only, not PEN | The PEN is the UDISE+ key; it is the transfer-in failure that creates "active in two schools" |
| Hard-block left students' numbers too | Prevents re-admission of a returning child; the check endpoint points to the old record instead |
| Server-rendered PDF of the forms | Chromium runs in workers only; the browser prints the A4 page directly, as the registers do |

## Related requirements

FR-APC-001..006 (new, docs/03 §3.17), FR-STU-017..019, FR-IMP-010, FR-EXP-006, PRV-021 (new); US-1901..US-1905 (docs/02 C19). Existing: FR-STU-013..016, FR-DQ-021/022, DQ-009, PRV-013/014/020, invariants 1, 4, 5, 7.

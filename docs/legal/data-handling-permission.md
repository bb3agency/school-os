# Permission to import lists and photograph registers (template letter / DPA annex)

| Field | Value |
|---|---|
| template_version | 0.1 (2026-11-08) |
| Placeholders | `{{school_legal_name}}`, `{{school_address}}`, `{{schoolos_legal_name}}`, `{{schoolos_address}}`, `{{effective_date}}`, `{{dpa_date}}`, `{{tier}}` (shared or dedicated), `{{school_signatory}}`, `{{schoolos_signatory}}`, `{{privacy_contact_name}}`, `{{privacy_contact_email}}`, `{{schoolos_contact_email}}`, `{{onboarding_people}}`, `{{registers_list}}`, `{{raw_file_retention_days}}` |
| Based on | BRD assumption A1 (docs/01 §12); docs/08 §1 (roles, sub-processors), §5 (Aadhaar), §7 (retention, erasure chain); docs/05 §13 (retention defaults); docs/07 §6.4 (support access); [dpa-template.md](dpa-template.md) |
| Decision | Lead decision D81 (pilot-readiness gate 9a); ADR-0048 (templates as versioned Markdown) |

> **Not legal advice.** Drafted by SchoolOS (owner decision D30) from docs/08 and from the product as built on 2026-11-08 (each retention value below names the configuration it comes from). Have it reviewed professionally before the first paid school signs. Points marked **(to confirm)** are not settled by the docs or the product.

**How to use it.** Sign it together with the DPA, as Annex E of the DPA or as a separate letter that refers to it. It does not replace the DPA: everything not said here (confidentiality, security, breaches, sub-processor changes, offboarding) is governed by the DPA. When the product changes a value quoted here, the template version moves and schools that signed an earlier version are told.

---

**From** {{school_legal_name}}, {{school_address}} (the **School**, the data fiduciary)
**To** {{schoolos_legal_name}}, {{schoolos_address}} (**SchoolOS**, the data processor)
**Date** {{effective_date}} · Refers to the Data Processing Agreement dated {{dpa_date}} ({{tier}} tier)

## 1. What the School permits

1.1 The School permits SchoolOS, as its data processor and only on the School's instructions, to:

a) **import the School's lists** into SchoolOS: spreadsheets (XLSX or CSV, up to 10 MB and 5,000 rows per file) that the School's office exports or prepares from its admission register, UDISE+, board registration lists, Aadhaar-as-printed details and transfer certificates received; and

b) **receive photographs of the School's registers** ({{registers_list}}, for example the admission register) as JPEG or PNG page images, so that SchoolOS can read the rows for the School's staff to check and confirm.

1.2 The files are uploaded by the School's own staff signed in to SchoolOS under their own accounts, with the permission `import.run` (upload, map, review) and the permission `import.commit` (add the rows to the School's records, revert, confirm photographed rows). Nothing read from a file or a photograph becomes a record of the School until one of the School's staff confirms it.

1.3 **Staff lists.** SchoolOS has no import for staff lists as built: staff are added one by one by invitation in the School's settings. If the School gives SchoolOS a staff list during onboarding, it is used only to send those invitations and is handled as in §4-§5 **(to confirm: whether a staff-list import is needed for the pilot)**.

## 2. Purposes

2.1 The data is used only to:

- set up the School's student records in SchoolOS (onboarding);
- compare the same details from different sources (admission register, Aadhaar as printed, UDISE+, board registration) and show the School the mismatches before it submits to a portal; SchoolOS never corrects an official record by itself;
- keep each photographed page as the evidence for the values confirmed from it;
- the other purposes of the DPA, Annex A.

2.2 Not for any other purpose: no advertising, selling, marketing, profiling, model training or analytics across schools on identifiable data (DPA §2.1).

## 3. Categories of data

| Data principals | Data in the lists and photographs |
|---|---|
| Students | Admission number and date, name, date of birth, gender, class and section, roll number, father's and mother's names, mother tongue, nationality, UDISE+ PEN, APAAR ID, board registration details; where the School's list contains them, the more sensitive columns its import may carry (for example caste or religion), which are encrypted |
| Parents and guardians | Names and relationship as written in the register or list; contact details where the list has them |
| Staff (only if §1.3 applies) | Name, e-mail, phone, role |

**From a register photograph** SchoolOS keeps only the columns listed in `fields` of `apps/api/app/extraction/config.yaml` (admission number and date, name, gender, date of birth, father's and mother's names, mother tongue, nationality); anything else read from the page (for example caste, religion, address or health) is dropped and never stored from the page.

**No Aadhaar numbers.** SchoolOS never keeps a full Aadhaar number (invariant 4; docs/08 §5): only the last four digits and the name, date of birth and gender as printed.

- In a spreadsheet, a full Aadhaar number in any cell makes that row an error (`aadhaar_full_number_rejected`) that names only the column; the number is stored nowhere and the School is asked to remove it from the file.
- In a photograph, every 12-digit number that passes the Aadhaar check is masked in what was read before anything is stored or logged; the text of the page is not stored. A page that showed a full number is kept only as a copy with the number blacked out and read again to check it; if that is not possible, the original image is discarded and the rows of that page cannot be confirmed.

## 4. Who at SchoolOS handles it

4.1 SchoolOS staff have **no standing access** to the School's data in SchoolOS (DPA §3.1). Support access needs the School's approval in SchoolOS, is time-limited, read-only, recorded in the School's audit log and revocable by the School (docs/07 §6.4).

4.2 During onboarding, SchoolOS may send {{onboarding_people}} to help the School's staff on site (BRD assumption A5). They sit with the School's staff, who upload and confirm under their own accounts; they do not take copies of the lists or photographs away, do not keep them on their own devices, and do not sign in as the School's staff **(to confirm: whether SchoolOS onboarding staff ever receive files by e-mail or messaging, and if so how those copies are deleted)**.

4.3 **Photographs** are taken on a device the School chooses. Copies left on that device after upload are the School's to delete; SchoolOS recommends deleting them once the batch is confirmed **(to confirm: whether SchoolOS supplies a device or app for photographing)**.

## 5. Where it is stored

5.1 Uploaded spreadsheets and photographs are stored as the School's documents in a private storage bucket with server-side encryption (AWS S3, ap-south-1, Mumbai), backups in ap-south-2 (Hyderabad), for both tiers (DPA §8; docs/08 §1). What is read from them is stored in the School's part of the SchoolOS database in the same region, under row-level security; the sensitive columns are encrypted with the School's own keys.

5.2 Files are checked for viruses before they are read.

## 6. Sub-processors

6.1 The sub-processors are those of DPA Annex C (docs/08 §1 on the effective date). For this letter:

- **Amazon Web Services** stores the files and the database (India).
- **Register-row extraction provider: not chosen yet.** As built, reading of register photographs runs only with a synthetic provider in development and testing (`SOS_EXTRACTION_PROVIDER`: `fake`, refused in staging and production; `not-configured`). A real provider (an OCR service or a vision model through Google Cloud Vertex AI in asia-south1) is chosen by evaluation and named in Annex C, with the notice period of DPA §5.1, **before** any of the School's photographs are sent to it. It receives page images only, after Aadhaar redaction where detectable, and never full Aadhaar numbers **(to confirm: the provider, and whether until then the School's photographs are only stored as evidence and typed in by its staff)**.
- Spreadsheet imports are read by SchoolOS's own workers; no other provider receives the files.

## 7. Retention and deletion (as built)

| What | How long | How it is deleted |
|---|---|---|
| **Revert of an import** | For `revert_window_hours` (24, `apps/api/app/imports/config.yaml`) after the School adds an import, a person with `import.commit` can undo it: students the import created are removed and values it added are withdrawn (history kept). Refused if other records now depend on it (`import_has_dependents`) | By the School, in SchoolOS |
| **Uploaded spreadsheet (raw file)** | `raw_file_retention_days` (90, `apps/api/app/imports/config.yaml`) after it was added (or after upload if it was never added). The School may shorten this to between 7 and 90 days on the Data retention screen (`import_raw_files`, `apps/api/app/admin/retention.yaml`); it can never be longer. The School's current setting: {{raw_file_retention_days}} days | Daily job `imports.purge_raw_files`; the file is removed from storage after the delete commits and its copies expire within 1 day (`discarded-1d` bucket rule). The values of any cells edited before adding the import are erased at the same time; who edited which cell, and when, stays |
| **Parsed rows of an import** | Kept with the import record (row status, warnings, the student a row created or updated); they never hold the sensitive (C3) values, which are read from the file only when it is added | Deleted at offboarding **(to confirm: whether parsed rows should also go when the raw file is deleted)** |
| **Register photographs** | For the life of the values confirmed from them (docs/05 §13: "Register photos used as evidence"), because each confirmed value cites its page as evidence | Not deleted automatically. A photograph still linked as evidence for a record cannot be deleted (`document_in_use`); others can be deleted by a person with `document.manage_acl` (copies kept 90 days for recovery of a mistaken delete) **(to confirm: whether photographs that were rejected or never confirmed should get a deletion period)** |
| **Values added to the School's records** | They are the School's records and follow its legal obligations; never deleted automatically (docs/08 §7) | Corrected through the School's normal correction steps |
| **Everything above, at the end of the subscription** | | Deleted within 30 days of the School's final export, the School's keys destroyed, certificate of deletion issued; backups expire on their normal schedule (DPA §9.2) |

Every import, revert, confirmation and deletion is recorded in the School's audit log (identifiers, codes and counts only).

## 8. The School's right to withdraw

8.1 The School may withdraw this permission, wholly or for one kind of data, at any time by writing to {{schoolos_contact_email}}. Withdrawal does not affect what was done before it.

8.2 From the day SchoolOS receives it, SchoolOS stops helping with further imports and photographs, and the School's staff stop uploading them **(to confirm: whether SchoolOS should also switch off the import and photograph screens for the School; there is no such switch as built)**.

8.3 Files already uploaded are then deleted as fast as the product allows: the School shortens the raw-file period to 7 days (the shortest allowed) so the daily job deletes the spreadsheets, and deletes the photographs that are not evidence for a record; photographs that are evidence stay until the values they support are corrected or the subscription ends. Records the School has confirmed remain the School's records; withdrawing this permission does not delete them **(to confirm: whether a withdrawal should allow deleting a spreadsheet earlier than 7 days after it was added)**.

## 9. Contacts

School: {{privacy_contact_name}}, {{privacy_contact_email}} · SchoolOS: {{schoolos_contact_email}}

Signed for the School: {{school_signatory}} · Signed for SchoolOS: {{schoolos_signatory}} · Date: {{effective_date}}

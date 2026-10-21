# Legal templates (DPDP processor pack, R3)

> **Not legal advice.** SchoolOS has no lawyer (owner decision D30, 2026-10-14). These templates were drafted by SchoolOS from the DPDP Act 2023 and the DPDP Rules 2025 **as summarised in [docs/08](../08-privacy-and-compliance.md)**, which itself rests on public sources checked in September 2026. They may contain errors or gaps a lawyer would catch. A one-time professional review is recommended before the first paid school signs; it does not block pilots on synthetic data. Anything marked **(to confirm)** is a point docs/08 does not settle.

Decision: [ADR-0048](../adr/ADR-0048-dpdp-processor-pack-and-data-principal-requests.md) (templates as versioned Markdown with placeholders; the school-facing notice and letter templates are also shipped in the app, same text and version, checked by a test once wave R3-1A builds them).

## Files

| File | Who uses it | Version |
|---|---|---|
| [dpa-template.md](dpa-template.md) | SchoolOS and the school sign it before any real data (pilot-ready gate, docs/14 §3) | 0.1 |
| [privacy-notice-parents-template.md](privacy-notice-parents-template.md) | The school issues it to parents and guardians (PRV-001) | 0.1 |
| [privacy-notice-staff-template.md](privacy-notice-staff-template.md) | The school issues it to its staff (PRV-001) | 0.1 |
| [terms-of-service-template.md](terms-of-service-template.md) | SchoolOS's subscription terms with the school (business terms; ADR-0015, ADR-0038) | 0.1 |
| [dpia-template.md](dpia-template.md) | The school completes it with SchoolOS's help (docs/08 §10) | 0.1 |
| [breach-runbook.md](breach-runbook.md) | SchoolOS operators and the school (ADR-0050; docs/11 §7) | 0.1 |
| [data-principal-response-templates.md](data-principal-response-templates.md) | The school's office answering requests and grievances (ADR-0048) | 0.1 |

## Conventions

- **Placeholders** are written `{{name}}`. The common ones: `{{school_legal_name}}`, `{{school_short_name}}`, `{{school_address}}`, `{{school_affiliation}}`, `{{privacy_contact_name}}` (a name or a designation such as "The Principal"), `{{privacy_contact_phone}}`, `{{privacy_contact_email}}`, `{{schoolos_legal_name}}`, `{{schoolos_address}}`, `{{schoolos_contact_email}}`, `{{effective_date}}`, `{{template_version}}`. In the app they are filled from school settings (`privacy_contact`, letterhead) and never from student records.
- **Versions.** Each file has a `template_version`. A change to wording moves the version; a school's notice record (`ops.privacy_notices`, FR-DPDP-003) stores the version it issued.
- **Sources.** Statements about the law cite docs/08 sections. Nothing here should claim more than docs/08 says; if a reviewer finds a gap, fix docs/08 first, then the template.
- **Language.** English. A Telugu version of the parents' notice may be added for schools whose parent language is Telugu (ADR-0044) only after a fluent reviewer signs it off (D23).
- **No personal data** is ever filled into these files in the repository; completed copies belong to the school.

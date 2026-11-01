# Legal templates (DPDP processor pack, R3)

> **Not legal advice.** SchoolOS has no lawyer (owner decision D30, 2026-10-14). These templates were drafted by SchoolOS from the DPDP Act 2023 and the DPDP Rules 2025 **as summarised in [docs/08](../08-privacy-and-compliance.md)**, which itself rests on public sources checked in September 2026. They may contain errors or gaps a lawyer would catch. A one-time professional review is recommended before the first paid school signs; it does not block pilots on synthetic data. Anything marked **(to confirm)** is a point docs/08 does not settle.

Decision: [ADR-0048](../adr/ADR-0048-dpdp-processor-pack-and-data-principal-requests.md) (templates as versioned Markdown with placeholders).

**As built (R3-1A, R3-1B, 2026-10-21; checked 2026-11-01 in R3-2F).** The school-facing templates also ship in the app with the same text and version, and tests fail when the two drift apart:

| Docs file | App copy | Test |
|---|---|---|
| privacy-notice-parents-template.md | `apps/api/app/privacy/templates/notice_parents.yaml` | `tests/privacy/test_registries.py` (`test_FR_DPDP_001_notice_templates_match_docs_legal`) |
| privacy-notice-staff-template.md | `apps/api/app/privacy/templates/notice_staff.yaml` | same |
| data-principal-response-templates.md | `apps/api/app/privacy/templates/letters.yaml` | `tests/privacy/test_registries.py` (`test_FR_DPDP_001_letter_templates_match_docs_legal`) |
| breach-runbook.md §4 templates B and C | `apps/api/app/privacy/breach_templates.yaml` | `tests/privacy/test_config_and_templates.py` (`test_FR_DPDP_036_breach_templates_match_the_runbook`) |

Change the docs file first, then the app copy, and move both versions together. The DPA, DPIA and terms are not shipped in the app.

## Files

| File | Who uses it | Version |
|---|---|---|
| [dpa-template.md](dpa-template.md) | SchoolOS and the school sign it before any real data (pilot-ready gate, docs/14 §3) | 0.2 |
| [privacy-notice-parents-template.md](privacy-notice-parents-template.md) | The school issues it to parents and guardians (PRV-001) | 0.1 |
| [privacy-notice-staff-template.md](privacy-notice-staff-template.md) | The school issues it to its staff (PRV-001) | 0.1 |
| [terms-of-service-template.md](terms-of-service-template.md) | SchoolOS's subscription terms with the school (business terms; ADR-0015, ADR-0038) | 0.1 |
| [dpia-template.md](dpia-template.md) | The school completes it with SchoolOS's help (docs/08 §10) | 0.2 |
| [breach-runbook.md](breach-runbook.md) | SchoolOS operators and the school (ADR-0050; docs/11 §7) | 0.1 |
| [data-principal-response-templates.md](data-principal-response-templates.md) | The school's office answering requests and grievances (ADR-0048) | 0.1 |

## Conventions

- **Placeholders** are written `{{name}}`. The common ones: `{{school_legal_name}}`, `{{school_short_name}}`, `{{school_address}}`, `{{school_affiliation}}`, `{{privacy_contact_name}}` (a name or a designation such as "The Principal"), `{{privacy_contact_phone}}`, `{{privacy_contact_email}}`, `{{schoolos_legal_name}}`, `{{schoolos_address}}`, `{{schoolos_contact_email}}`, `{{effective_date}}`, `{{template_version}}`. In the app they are filled from school settings (`privacy_contact`, letterhead) and never from student records.
- **Versions.** Each file has a `template_version`. A change to wording moves the version; a school's notice record (`ops.privacy_notices`, FR-DPDP-003) stores the version it issued. In the breach runbook the version covers the templates in §4 (the app copy carries it); the operator and school steps in §2-§3 describe the product and are kept current without moving it.
- **Sources.** Statements about the law cite docs/08 sections. Nothing here should claim more than docs/08 says; if a reviewer finds a gap, fix docs/08 first, then the template.
- **Language.** English (ADR-0036). A Telugu version of the parents' notice may be added for schools whose parent language is Telugu (ADR-0044) only after a fluent reviewer signs it off (D23), and is used only while `SOS_TELUGU_ENABLED` is on. The same holds for consent forms: they and their PDFs are printed in English while the switch is off, whatever the school's parent language (lead decision D47, 2026-11-01; being implemented in R3-2F).
- **No personal data** is ever filled into these files in the repository; completed copies belong to the school.

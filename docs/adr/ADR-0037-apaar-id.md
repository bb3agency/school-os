# ADR-0037: Store the APAAR ID (and UDISE+ PEN) as student attributes with provenance

| Field | Value |
|---|---|
| Status | Accepted · Amended by ADR-0039 (consent register; PEN is 11 digits; duplicate guard) |
| Date | 2026-10-01 |
| Deciders | Product owner (accepted 2026-10-01, with option (a) of decision 3); engineering (proposal) |
| Amends / supersedes | none (adds to the attribute catalogue of docs/05 §5; keeps ADR-0007 unchanged) |

## Context

APAAR ("One Nation One Student ID") is a 12-digit lifelong academic ID. For school students the **school generates it in the UDISE+ portal**, after a parent signs a consent form (parents may refuse), and only for a student already in UDISE+ with a **PEN** (Permanent Education Number, the UDISE+ student ID). Generation authenticates the student against Aadhaar, so the name, date of birth and gender in UDISE+ must match Aadhaar as printed. Facts checked on 2026-10-01 against public guidance (apaar.education.gov.in and school and parent guides); re-check before building.

What SchoolOS has today:

- docs/01 §2 P2 names "failed APAAR generation" as a pain the product solves, and docs/02 §9 lists DQ-009 ("Aadhaar details needed to generate APAAR"). `app/dq/config/engine.yaml` `apaar_attributes` checks that `aadhaar_last4` and the three Aadhaar-as-printed fields exist, and DQ-002 and the related rules compare them with the register. So SchoolOS checks that a student is **ready** for APAAR.
- The catalogue (`app/students/attributes.yaml`) has no APAAR ID and no PEN. Nothing records whether an ID was generated, so DQ-009 keeps raising a finding for students who already have one, and the office cannot see who still needs one. The UDISE+ pre-check profile (`app/exports/config.yaml` `udise-plus`) has no APAAR or PEN column.
- Attribute values are stored per source with provenance (FR-STU-002), and there is a `udise_plus` source already.
- Invariant 4 / PRV-013: `core.redaction` masks every 12-digit sequence that passes the Verhoeff check. **An APAAR ID is also 12 digits.** If APAAR IDs carry a Verhoeff check digit, or by chance (a random 12-digit number passes about 1 time in 10), redaction would mask them in exports, extracted text and prompts. It is not known on 2026-10-01 whether APAAR IDs use Verhoeff.

## Decision

1. **Two new global attributes** in `app/students/attributes.yaml` (re-seeded by a migration, backward compatible: rows only):
   - `apaar_id`: `data_type: digits12` (new type: exactly 12 ASCII digits), **C2**, `is_identity: false`; sources `udise_plus` (the value shown in the portal after generation), `parent_form` (the parent's APAAR card or DigiLocker copy) and `manual_entry`; canonical precedence `udise_plus, parent_form, manual_entry`; `require_verified: true` (a person checks it against the portal or the card before it counts).
   - `udise_pen`: the UDISE+ PEN, `data_type: text` with the PEN pattern (to confirm from the portal), **C2**, source `udise_plus` (and `manual_entry`), same verification rule.
2. **Classification C2, not C3.** They are identifiers of a child (personal data), not sensitive categories: shown on the student page, searchable by exact value, included in exports that list them, never sent to an AI provider unless the question needs that one field (NFR-PRV-003), never logged (invariant 5). They are **not** Aadhaar data and do not reveal it. Import mapping and forms MUST keep them apart from the Aadhaar fields: a 12-digit value is never accepted into an Aadhaar field, and an `apaar_id` input that passes the Verhoeff check is still stored only as `apaar_id` (it is never treated as, or matched against, an Aadhaar number).
3. **Redaction: option (a), chosen by the product owner on 2026-10-01.** The free-text Aadhaar mask (invariant 4) stays exactly as it is for free text, OCR output, extracted text, logs, prompts, AI answers and free-text exports. Only the typed `apaar_id` field is exempt: its value (exactly 12 ASCII digits, from a field whose type and source are known) is shown unmasked to users who may read the student, and is written unmasked into the typed `apaar_id` column of the UDISE+ pre-check and the student list export. Everything else in those files still passes the mask. Tests prove that a Verhoeff-valid APAAR value in free text is still masked, that the typed field round-trips, and that APAAR values never reach logs. It was not established on 2026-10-01 whether APAAR IDs carry a Verhoeff check digit; with (a) the answer no longer changes the design. The security review's sign-off on the typed-field exemption is to be recorded against this ADR.
   As built: the request-body Aadhaar guard and the value validator skip only the `value` of a typed `digits12` attribute (`apaar_id`); every other field, including free-text search, still refuses a Verhoeff-valid 12-digit number.
   **Search by APAAR ID (owner, 2026-10-03; FR-STU-016, as built).** Exact match only, through a dedicated field: `apaar_id` in the `POST /students/search` body (never a URL parameter; `GET /students` has no such parameter). The value must be exactly 12 digits (spaces or hyphens between groups removed; else `422 digits12_required`) and matches only current, not rejected (verified or recorded) values of the typed `apaar_id` attribute, never another field. Same permission (`student.read_basic`) and scope as every search: class and subject teachers find only students in their sections (BOLA), and RLS keeps it inside the school. The search route's Aadhaar guard skips only that top-level field when it is exactly 12 digits; `query`, `admission_no` and every other field still refuse a full Aadhaar number, so the free-text guard is unchanged. The value is never logged (PRV-020) and, like other student searches, not audited. Tests: `apps/api/tests/students/test_apaar_search.py`. The web students list offers it as a separate "APAAR ID" search option.
4. **DQ changes.** DQ-009 is raised only for students **without** a verified `apaar_id` (ready-for-APAAR check). New rules: DQ-021 `apaar_id` format and duplicate within the school (two students, one APAAR ID: blocker); DQ-022 UDISE+ name, DOB or gender differs from Aadhaar as printed for a student without an APAAR ID ("APAAR generation will fail until these match"), which reuses the existing matchers.
5. **UDISE+ pre-check export** gains `udise_pen` and `apaar_id` columns (profile `layout_version` 2), empty when unknown; the findings sheet lists DQ-009/021/022.
6. **Out of scope:** SchoolOS does not generate APAAR IDs, call the APAAR, UDISE+ or DigiLocker APIs, store the consent form's contents beyond what the school uploads as a document, or record consent status as a field (a later ADR if the PO wants a consent tracker; consent is a DPDP question of its own).

## Consequences

- Good: the office sees who has an APAAR ID, who is ready and who is blocked; DQ-009 stops nagging for done students; the UDISE+ sheet is complete; duplicate IDs are caught.
- Bad / costs: one more identifier of a child to protect (C2, audited exports); the 12-digit overlap with Aadhaar needs care so the Aadhaar mask never loosens; a new `digits12` data type touches validation, imports and the canonical view.
- Effort (estimate): catalogue + migration + `digits12` type and validators 1-2 days; DQ-009 change and DQ-021/022 with tests 2 days; UDISE+ export columns, import mapping and UI labels (en) 1-2 days; redaction interplay tests and docs (05, 02, 03, 08) 1 day. About **1-1.5 weeks** for one engineer, plus the PO decisions below.
- Follow-up: confirm PEN and APAAR formats and the Verhoeff question; update docs/05 §5 and §8 classification table, docs/02 §9 rules, docs/08 §4 (children's data) and the sub-processor note (none: no new processor).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep only the readiness check (today) | DQ-009 cannot tell done from not done; the UDISE+ sheet stays incomplete; the office tracks APAAR in a separate spreadsheet |
| Store a boolean "APAAR generated" only | Loses the value the office re-types into boards and portals; cannot catch duplicates or typos |
| Classify `apaar_id` as C3 | Would hide it from the clerks who need it daily and from search; it is not a sensitive category under docs/08 §4 |
| Relax the Verhoeff mask for any 12-digit value near an APAAR label | Weakens invariant 4 (an Aadhaar number could slip through); rejected |
| Call the APAAR/UDISE+ APIs | No documented school-side API; would add a processor and credentials; out of scope |

## Related requirements

Added on acceptance: **FR-STU-013** (`apaar_id` attribute, sources, verification), **FR-STU-014** (`udise_pen` attribute), **FR-STU-015** (`digits12` data type and cross-field rejection with Aadhaar inputs), **FR-DQ-021** (APAAR ID format and duplicate), **FR-DQ-022** (UDISE+ vs Aadhaar-as-printed for students without an APAAR ID), **FR-EXP-005** (UDISE+ pre-check carries PEN and APAAR ID), **PRV-020** (APAAR ID and PEN are C2 identifiers: never logged, never in prompts unless the question needs them, the Aadhaar mask is never relaxed for them). Existing: FR-STU-002, FR-STU-012, FR-EXP-001, FR-EXP-003, PRV-013, PRV-014, NFR-PRV-003; DQ-009; BR-01. Docs affected: 01 §2, 02 §9, 03, 05 §5/§8, 08 §4-§5, `app/students/attributes.yaml`, `app/dq/config/engine.yaml`, `app/exports/config.yaml`.

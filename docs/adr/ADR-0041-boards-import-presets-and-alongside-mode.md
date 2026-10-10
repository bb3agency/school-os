# ADR-0041: Boards per school, CBSE/CISCE profiles from public sources, import presets and the "alongside your current ERP" mode

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-10-10 |
| Deciders | Product owner (decisions D3 and D10 of 2026-10-10, docs/18 §8); engineering |
| Amends / supersedes | none |

## Context

The 2026-27 plan (docs/18 §3 R1) leads with board and portal readiness. Owner decision **D10** puts the AP State Board (BSEAP), CBSE registration and CISCE (ICSE/ISC) schools in scope this year; **D3** says to build formats from public sources and mark each as unverified until a school confirms it. R1.5 adds the onboarding concierge: an import template library (roadmap M7 item pulled forward) and a way to run SchoolOS *alongside* an existing ERP, because schools do not rip out fees and the parent app on day one (docs/research/2026-10-market notes, competitors Q3).

Facts checked on 2026-10-10 (secondary sources; official circulars were not obtained):

- CBSE registers Classes IX and XI on Pariksha Sangam and takes the List of Candidates (LOC) for Classes X and XII. The APAAR ID is reported as a mandatory registration/LOC field from 2026-27 (circular of 15 Sep 2025 as reported; governing body decision reported 19 Feb 2026). Where a parent refuses APAAR consent, schools are reported to enter `REFUSED` (and `NOGEN` where generation failed). The Supreme Court (20 Jul 2026, as reported) requires the consent form to offer refusal. CBSE asks schools to give parents a verification slip.
- CISCE runs registration (Classes IX/XI, two years ahead) and entry confirmation (Classes X/XII) on its CAREERS portal; corrections need supporting documents. No public source says CISCE requires APAAR IDs.
- No official column list was found for the UDISE+ Student Module "Download Excel" file, nor for MyClassboard or Entab exports. Fedena publishes its admission import fields, but splits the name into first/middle/last name, which the import cannot join into one `full_name`.
- `core.tenants.boards` (text[]) already exists (0003) and was written only at provisioning; `sos_app` could not update it.

## Decision

1. **Boards per school and per class (FR-TEN-020, FR-TEN-021).** A school chooses its boards from `BSEAP`, `CBSE`, `CISCE` in its settings (`PATCH /tenant`, `tenant.settings.manage` with step-up, audited with the codes). The choice is stored in `core.tenants.boards`; migration `0051_boards_import_presets` grants `sos_app` UPDATE on that column only. A school with more than one board maps class codes to boards in `settings.class_boards`. Board profiles are listed for the declared boards (portal profiles always; all profiles while none is declared). The control plane's own copy of a school's boards (provisioning, protected billing windows) is **not** updated by this; see follow-up.
2. **Profiles from public sources (FR-DQ-030..032, FR-EXP-006).** A DQ profile MAY declare `board`, `classes`, `source` (https URLs), `verified` (default false), `supersedes`, `parent_verification_slip` and `board_fields` (the board form's columns, with the SchoolOS attribute that fills each or none). R1 adds `cbse-registration-2027`, `cbse-loc-2027` and `cisce-registration-2027`, each `verified: false`. A newer cycle is a **new file**; the older profile is never edited (findings and exports store its key) and is listed as superseded, taking its board from its successor. CBSE profiles check APAAR readiness (DQ-009) but do not make the APAAR ID a DQ-005 blocker, because refusal is lawful.
3. **No new student attributes in R1.** Subjects, minority status, single girl child, guardian name and photographs are listed in `board_fields` with no attribute ("enter on the portal"). Adding them needs a catalog migration and their own classification review.
4. **Import template library (FR-IMP-030..032).** Presets are versioned YAML in `app/imports/templates/` (blank SchoolOS template, admission register in Excel, UDISE+ student list, generic ERP export), listed to `import.run` holders, downloadable as header-only XLSX (no school data, no audit), and applied to an import's columns as a **read-only** suggestion; the clerk saves the mapping through the existing audited route. Formats owned by others are `verified: false`. No vendor-specific ERP preset ships without a public column list.
5. **Alongside mode (FR-TEN-022, FR-IMP-033, FR-DQ-033).** `settings.operating_mode` is `full` or `alongside` (with an optional `current_erp_name`). `app/tenancy/operating_modes.yaml` lists the modules a mode hides (today `tally`; fee screens join when they exist). Hidden modules are reported in `TenantOut.modules_hidden`, hidden in the web app and cannot be newly set up (a new Tally enrolment code answers 409 `module_hidden`); recorded data and enrolled agents are untouched. The "refresh from your ERP export" path imports with source **`manual_entry`** (office records), never `admission_register`; a new rule **DQ-030** (`cross_source`, admission register vs `manual_entry`, medium; routes `ROUTE-ERP`, `ROUTE-SCHOOL-CR`) turns every difference into a finding (invariant 6, BR-01).

## Consequences

- Good: CBSE and ICSE schools get board pre-checks and "ready to enter" sheets now, honestly marked as unconfirmed; a school sees only its boards' checks.
- Good: a school can start with SchoolOS next to its ERP, with a first import that maps itself and refreshes that never overwrite the register.
- Bad / costs: profile and preset formats may be wrong until a school confirms them; each correction is a new profile version.
- Bad / costs: DQ-030 also compares values typed in SchoolOS by the office (source `manual_entry`) with the register. That is intended (the register is the anchor) but may add findings for schools that typed values before entering the register.
- Bad / costs: using `manual_entry` for ERP data means SchoolOS cannot tell an ERP refresh from a hand-typed value except through the import batch. A dedicated `school_erp` source would need CHECK changes on three tables and the attribute catalog; deferred.
- Follow-up work: confirm each format with a pilot school and set `verified: true`; sync the school's boards to the control plane (fleet heartbeat or a lifecycle call) so protected windows follow them; build the per-student parent verification slip with the readiness check (R1.1); add subject/minority attributes if pilots need them pre-checked; consider a `school_erp` source.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Store boards in `settings` JSON instead of `core.tenants.boards` | Two places for the same fact; provisioning already writes the column |
| A new `school_erp` import source | Changes CHECK constraints on three tables and the attribute catalog; collides with parallel R1 work; `manual_entry` already ranks after the register |
| Edit `cisce-registration-2026` in place | Breaks the record of what earlier findings and exports were checked against |
| Make the APAAR ID a CBSE DQ-005 blocker | A lawful refusal would block the student; the board accepts `REFUSED` |
| Apply a preset by writing the mapping server-side | Skips the clerk's review; the existing PUT already validates and audits |
| Ship MyClassboard/Entab/Fedena presets from guesses | No public column lists (Fedena splits names); a wrong preset is worse than the generic one |

## Related requirements

FR-TEN-020, FR-TEN-021, FR-TEN-022, FR-DQ-030..033, FR-EXP-006, FR-IMP-030..033; US-203, US-204, US-403, US-503. Docs: 02-PRD §4-5, 03-TRD, 05-data-model §4, 09-api-specification, 14-roadmap R1.

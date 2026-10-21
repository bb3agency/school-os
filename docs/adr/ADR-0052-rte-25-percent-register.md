# ADR-0052: RTE 25% register (Section 12(1)(c)): admissions, documents and reimbursement claims per academic year

| Field | Value |
|---|---|
| Status | **Accepted (lead, 2026-10-21; owner may revisit) as a design. Not scheduled for build:** owner decision D10 (2026-10-10) left the RTE register out of this year's scope and it stands until the owner reopens it (docs/18 §8 D43, D44) |
| Date | 2026-10-21 |
| Deciders | Lead engineer (for the product owner, who may revisit) |
| Amends / supersedes | none |

> **Not legal advice; rules unverified.** What follows about the RTE Act 2009 is limited to its well-known outline: Section 12(1)(c) requires private unaided schools to admit, in the entry class, at least 25% of its strength from weaker sections and disadvantaged groups, free of charge, and the state reimburses the school per child. **Every state-specific rule** (Andhra Pradesh's entry classes, categories and sub-categories, the online lottery, document list, reimbursement amount, claim periods and attendance conditions) is **`verified: false`** in a versioned file until a school confirms it (D3 pattern). Whether a school is exempt (for example a minority institution, docs/02 §11 question 6) is the school's determination, recorded as a setting, **(to confirm)**.

## Context

- docs/18 §3 R3 item 3 lists an RTE register; owner decision D10 removed it from this year's scope. The lead was asked to design it so that it can be built when the owner wants it.
- Schools admitting under 12(1)(c) must keep the allotment, the child's eligibility documents and the reimbursement claims to the state for every year the child studies (to Class VIII), and must not charge those children fees.
- The fee module already has a concession type `rte` (docs/05 §15.1) and maker-checker approvals (ADR-0042, D17).
- Being admitted under RTE reveals that a family is economically weaker or from a disadvantaged group: that is sensitive information about a child.

## Decision

1. **A versioned state file.** `app/rte/config/ap.yaml` (`verified: false`, `source` URLs): entry classes, category codes and labels (weaker section, disadvantaged group and the state's sub-categories), the required document types per category, reimbursement periods (for example per term or per year), and claim fields. The school says in settings whether 12(1)(c) applies to it (`rte_applicable`, `tenant.settings.manage`ᴿ; hidden when false).
2. **Admissions register.** `sis.rte_admissions` (C3): per student and academic year of entry, the lottery or allotment reference (the state portal's application or allotment number), allotment date, category code, entry class, status (`allotted`, `admitted`, `declined`, `left`), admitted-on date and the admission it created. Documents are links to existing documents (`sis.rte_documents`: document id, document type from the state file, `verified_by`/`verified_at`), never copies; the register never holds an Aadhaar number (invariant 4) or an income figure, only that the document was seen.
3. **Claims per academic year.** `sis.rte_claims` (C2): academic year, period, status (`draft`, `submitted`, `part_received`, `received`, `rejected`), portal reference, submitted on/by, amounts claimed and received, notes; `sis.rte_claim_lines`: one line per RTE child in the claim with the amount claimed, eligibility facts the state file names (still enrolled; attendance percentage from `academics.service` when the state file asks for it) and the amount received. A claim is prepared from the register (children admitted and still enrolled in the period) and frozen when marked submitted; receipts of money are recorded by the school (the money goes to the school's bank, never through SchoolOS).
4. **Fees stay correct without automation.** An RTE admission offers "Request RTE concession", which creates a **pending** concession of type `rte` through `fees.service` for a person to approve (maker-checker, D17). SchoolOS never approves it automatically and never blocks a fee receipt because of RTE status.
5. **Restricted visibility.** RTE status is **C3**: visible only to holders of `rte.read` (owner, principal, office admin, accountant, read-only auditor; never class teachers or teachers), never printed on certificates or registers, never in messages or parent pages, never in Ask answers or retrieval (the `knowledge` module must not import `rte`; import-linter contract), never in exports except the school's own full data export (masked unless `include_sensitive`). No cross-school aggregate.
6. **Permissions** `rte.read`, `rte.manage`, `rte.claim.manage` (docs/07 §6.8). Tables in `sis` (no new schema). Retention: the register and claims follow financial records for claims (8 years after the financial year, D21) and the student record for admissions.

## Consequences

- Good: when the owner turns it on, a school can prove every RTE admission and claim, year by year, without a separate spreadsheet; the fee concession stays under maker-checker.
- Bad / costs: AP's real rules are unknown until a school confirms them; the first school will change the state file.
- Follow-up: the owner decides whether to reopen D10; a pilot school confirms `ap.yaml`.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| A flag on the student plus a note | No allotment reference, no documents, no claims; the sensitive flag would spread to every screen that shows the student |
| Store RTE in the fee module | Admissions and claims are not fee-ledger entries; schools without fees on still need the register |
| Approve the RTE concession automatically | Money changes need a second person (D17) and the admission may be wrong |

## Related requirements

FR-RTE-001..012, SEC-051, PRV-038; docs/02 C30; docs/03 §3.29; docs/05 §16.5; docs/07 §6.8; docs/08 §16; docs/17 §5.10.

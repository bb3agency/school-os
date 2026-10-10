# ADR-0040: Board and portal readiness: exact cross-source diff with fix owners

| Field | Value |
|---|---|
| Status | Proposed (built on `wip/r1-ssc-readiness`; owner to accept with R1) |
| Date | 2026-10-10 |
| Deciders | Product owner (plan 2026-27 decisions D1, D3, D10); engineering (proposal) |
| Amends / supersedes | none (adds to the DQ engine of docs/02 §5-6 and ADR-0037; keeps BR-01 and invariant 6) |

## Context

For the March 2026 SSC exams the AP State Board (BSEAP) took the Class 10 nominal roll from UDISE+ and accepted corrections only where they matched Aadhaar; 3,497 schools asked to correct 9,986 students in a three-day window (docs/research/2026-10-market/notes/regulation_portals.md §3, secondary sources). UDISE+ validates name, gender and date of birth against Aadhaar **exactly, down to spelling and spacing**, and APAAR generation fails on the same mismatches. The next SSC window is likely February 2027; the owner chose readiness as the wedge (D1) and asked that formats built from public sources be marked unverified (D3).

SchoolOS already keeps every value per source (FR-STU-002) and compares them with name **match classes** (docs/02 §6), which deliberately forgive spacing, initials and spelling variants to find the same person (DQ-001, DQ-010, DQ-011, DQ-022). That is the right question for "is this the same child?", and the wrong one for "will the portal accept this record?".

## Decision

1. **A second, exact comparison.** `app/dq/readiness.py` (pure) compares a profile's identity fields character by character after NFC. Capital letters, spaces and dots count. Each difference is named as kinds in plain words (`spacing`, `initials`, `transposed`, `day_month_swapped` …) and, for people allowed to see both values, as a character-level diff (`difflib`) and short sentences ("space missing after “SAI”"). Match classes are reused only to name a letter-level difference (`variant`, `initials`, `order`); they never make two values "equal".
2. **Every difference has a fix owner, decided by configuration** (`app/dq/config/readiness.yaml`, invariant 13). The admission register holds the right value (BR-01; also BSEAP's own instruction "if school records and Aadhaar differ, update Aadhaar") unless it is contradicted: no corroborating record (birth certificate, incoming TC) agrees with it and exactly one value a corroborating record holds is also held by another compared record. A record that differs from the right value is fixed by its owner: Aadhaar by the parent at an Aadhaar centre (`parent_aadhaar`), UDISE+ and the board's copy by the school through the MEO/MIS coordinator (`school_udise`), the register by a change request with evidence (`school_register`, maker-checker, never auto-corrected: invariant 6). No register value, or a register disputed without agreement, is `unknown`: a person decides. Owner → student status: `needs_parent`, `needs_school`, `blocked`; none → `ready`.
3. **Readiness profiles are DQ profiles.** `app/dq/config/profiles/<key>.yaml` gains optional `source` (https URLs), `verified` (default false) and `readiness` (classes, fields and sources compared, required sources, advisory kinds, "skip when this attribute is verified"). `bseap-ssc-2027` (Classes IX, X) and `apaar` (UDISE+ vs Aadhaar, the register as referee; a verified APAAR ID means ready, ADR-0037) ship with `verified: false` and their sources. The same key also runs the profile's DQ-005 required fields, and DQ-009 for APAAR.
4. **Findings, not a new table.** Rule **DQ-030** (`readiness_diff`, requires a profile) stores one finding per difference of a readiness profile (masked values, owner, kinds) through the existing engine, so idempotency, reopen, waive, A-01 `needs_confirmation`, change-request links and incremental re-checks after writes all apply unchanged. Severity follows the owner: mismatch and undecided `blocker`, missing `high`, advisory (capital letters only, per profile) `info`. Profile findings never block certificates (`open_blockers` reads base rules only).
5. **Reads are live, findings overlay them.** Summary, section and student views compute readiness from the current values on every read (bulk reads, no per-student queries) and lay the stored DQ-030 findings over it by fingerprint: a waived difference with the same conflict does not count; a finding waiting for a confirmation keeps the student `blocked`. So the dashboard is right before anyone runs a check, and a waiver is respected without a re-run.
6. **Two permissions** (07 §6.2): `dq.readiness.read` (owner, principal, office roles, exam coordinator, auditor; class teachers scoped) and `dq.readiness.manage` (principal, office admin, office staff, exam coordinator: runs that store findings; the principal holds it so that it can still assign the office roles, which may only grant permissions the assigner holds). Migration `0051_readiness` adds them; existing schools get the grants from the post-migration system-role sync (ADR-0022).
7. **Parent verification slip as a print view.** An A4 HTML page per student (or section) like the correction memo and certificate print views: own hashed-style CSP, no scripts, every value escaped and Aadhaar-masked; Aadhaar-as-printed values only for `student.read_sensitive` holders in scope; never an Aadhaar number or its last four digits. The browser prints it; the same HTML prints to PDF through `app.core.pdf` if a worker job is added later. Printing and C3 views are audited (`dq.readiness.slips_printed`, `dq.readiness.student_viewed`).

## Consequences

- Schools see who must act months before a window, and parents can be asked to fix Aadhaar while there is time. The exact diff and the match-class rules coexist: DQ-001/010/011/022 still say "same child?", DQ-030 says "will the portal accept it?".
- Character-level diffs show values, so they are produced only on the student view and the slip, for people allowed to see both values; findings, logs and audit rows keep masked values and codes only.
- Every readiness format is unverified until a pilot school confirms it (D3). The BSEAP field list (parents' names, caste, medium, languages, photo) is not confirmed; parents' names are compared where the records hold them.
- Live reads decrypt the C3 Aadhaar-as-printed values of the scope in memory on each request (bounded: a profile's sections, at most a school).

## Alternatives considered

- **Tighten DQ-001/010/011 thresholds.** Would turn every spelling variant into a "different person" signal and break duplicate detection. Rejected.
- **A readiness table with a status per student.** Duplicates the findings lifecycle and goes stale between runs. Rejected for the live overlay.
- **PDF slips through the worker now.** Needs a job, storage and download links for a page the browser prints well; kept as a follow-up.

## Follow-up work

- Confirm the BSEAP nominal-roll format, the UDISE+ correction route and the APAAR failure messages with a pilot school, then set `verified: true` with the document reference.
- APAAR consent register (separate story) and the CBSE registration profile reuse the same engine.
- Portal-calendar reminders (R3) can read the readiness counts.

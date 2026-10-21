# ADR-0050: Breach handling: SchoolOS incident records in the control plane, a breach register for each school, clocks and templates

| Field | Value |
|---|---|
| Status | **Accepted (lead, 2026-10-21; owner may revisit)** (docs/18 §8 D41) |
| Date | 2026-10-21 |
| Deciders | Lead engineer (for the product owner, who may revisit) |
| Amends / supersedes | none. Uses the pull pattern of break-glass (docs/07 §6.4, ADR-0020) and the control-plane boundaries of ADR-0013/ADR-0020 unchanged |

> **Not legal advice.** The time limits below are those docs/08 records: CERT-In reporting within **6 hours** of noticing a reportable incident (docs/08 §6, PRV-017); DPDP Rule 7: notify each affected data principal **without delay** and the Data Protection Board **without delay, then a detailed report within 72 hours** (docs/08 §3, PRV-008); SchoolOS's notice to the school **without undue delay, target within 24 hours of confirmation** (docs/08 §3, §9 clause 5; docs/11 §7.2). Who must report to CERT-In for a school's own incident is **not decided in docs/08 (to confirm)**.

## Context

- docs/11 §7 has the incident process and R5 "suspected security incident" runbook with a 6-hour CERT-In clock and notice to schools within 24 hours, but no record in the product: clocks, who was told when, and what each school was told live in e-mail.
- The school, as fiduciary, must notify parents and the Board. It also has breaches SchoolOS never sees (a lost admission register, a teacher's phone with a class list, a mis-sent WhatsApp group message). Today nothing helps the school record them or meet the 72-hour report.
- The control plane must never hold student data (invariants 1, CLAUDE.md §11; ADR-0013) and must never write tenant tables (ADR-0020).

## Decision

1. **SchoolOS's own incident record (control plane).** `platform.incidents`: `opened_at` (T0 = time noticed), severity (docs/11 §7.1), status (`open`, `contained`, `resolved`, `closed`), `cert_in_required` and `cert_in_reported_at` (clock **T0 + 6 h**), a summary **without personal data** (categories and counts only; validated like heartbeat fields: no free text containing digits runs, e-mail addresses or names is accepted, the form says why), the lead operator, and `platform.incident_schools` (one row per affected school: data categories as codes, approximate count of individuals, time window, `school_notified_at`; clock **confirmation + 24 h**). Two operators confirm that an incident affects schools (`platform.incidents.manage`ᴿ, 2P like emergency break-glass) before any school is notified. Audited in the platform chain; no student or staff data ever enters these tables.
2. **The school's breach register (tenant).** `ops.breach_records` per school, from two origins: `processor_notice` (pulled from `platform.incident_schools` by the school-side worker every minute, exactly like break-glass requests; the owner and principal get an in-app notification marked urgent) and `school` (recorded by the school for its own incidents). Fields: noticed-at (T0), what happened (encrypted free text, never logged or audited), data categories (codes), approximate number of children, guardians and staff affected, status, containment steps, and three dated duties with their clocks: **Board initial notice** (due "without delay": shown as overdue from T0 + 24 h, a SchoolOS default, marked as such), **Board detailed report** (T0 + 72 h), **affected people notified** (without delay). Each duty records when it was done and how. Optional list of affected students and guardians (`ops.breach_affected`, ids only) to prepare individual notices.
3. **Templates, not sending.** The register fills, from the record and the school's privacy contact (ADR-0048), the templates in `docs/legal/breach-runbook.md`: the parents' notice (nature, extent, timing, likely consequences, what the school did, what families can do, contact; docs/08 PRV-008), the Board report outline, and the school's internal log. A parents' notice can be turned into an M4 parent notice draft (approved by a person, sent through the existing notice and WhatsApp flows if the school uses them); SchoolOS never sends a breach notice automatically.
4. **Dedicated tier.** There is no control plane on a dedicated host: SchoolOS notifies the school directly (phone and e-mail, and a `critical` announcement through the heartbeat answer) and the school records it in its own register with origin `processor_notice` by hand.
5. **Clocks in one place.** The register's list shows each open breach's next duty and how long is left, in IST; the operator console shows each incident's CERT-In and school-notice clocks. Reminders to the owner and principal at 24 h and 60 h after T0 while the Board report is not recorded.
6. **Permissions** `privacy.breach.read` and `privacy.breach.manage`ᴿ (owner, principal; docs/07 §6.8), `platform.incidents.read` / `platform.incidents.manage`ᴿ 2P (docs/07 §6.5 when built).

## Consequences

- Good: each clock is visible to the person who owns it; SchoolOS can prove when each school was told; schools have a register for their own incidents, which a DPIA and an inspector will ask for.
- Bad / costs: a second pull channel from the control plane to schools (same shape and tests as break-glass); operators must keep incident summaries free of personal data (validation plus review).
- Follow-up: confirm with a professional reviewer whether "without delay" for the Board's initial notice has a fixed limit in the notified Rules and who reports a school's own incident to CERT-In; the 24-hour default for the Board's initial notice is SchoolOS's choice until then.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep incidents in e-mail and a spreadsheet | No proof of when each school was told; clocks missed under pressure |
| Write breach rows straight into each school's tables from the control plane | Breaks ADR-0020 (the control plane never writes tenant data) |
| Send parents' breach notices automatically on WhatsApp | The school is the fiduciary and decides wording and channel; an automatic message to every parent could itself cause harm |
| Hold affected children's names in the incident record | Puts student data in the control plane (invariant, ADR-0013) |

## Follow-up as built (R3-1B, 2026-10-21)

Built in `0067_consents_breach`: `platform.incidents` / `incident_schools` with `app/platform/incidents.py` (two-operator confirmation through `app.platform.two_person`; a school added after the first confirmation clears it), the school-side pull in `app/privacy/breach_sync.py` (the only privacy file importing the control plane; import-linter `tenant-side-without-control-plane` lists every other privacy module; `tests/platform/test_boundaries.py` pins that `incidents.py` imports nothing tenant-side), the register with encrypted description and containment, duties and clocks, reminders (`ops.breach_reminders`), drafts from `app/privacy/breach_templates.yaml` (tested against `docs/legal/breach-runbook.md`) and the M4 notice draft without the description. The dedicated-tier `critical` announcement remains a manual operator step.

## Related requirements

FR-DPDP-030..038, PRV-008, PRV-017, PRV-036, SEC-052; docs/02 C28; docs/03 §3.27; docs/05 §16.3; docs/07 §6.8; docs/08 §16; docs/11 §7; docs/16 (incidents page, when built); `docs/legal/breach-runbook.md`.

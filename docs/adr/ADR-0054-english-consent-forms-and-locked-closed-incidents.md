# ADR-0054: English consent forms while Telugu is off; closed SchoolOS incidents are locked and reopen needs two operators

| Field | Value |
|---|---|
| Status | **Accepted (owner, 2026-11-01, on the lead's recommendation)** (docs/14 D47, D48) |
| Date | 2026-11-01 |
| Deciders | Product owner, on the lead engineer's recommendation |
| Amends / supersedes | Amends [ADR-0049](ADR-0049-consent-register-for-optional-purposes.md) (language of consent forms) and [ADR-0050](ADR-0050-breach-handling-processor-incidents-and-school-breach-register.md) (what may happen to a closed incident). The rest of both stands |

## Context

Two behaviours of the R3 privacy pack, found in the 2026-10-27 bug sweep, were not settled by the accepted ADRs:

1. **Consent form language.** The consent register (ADR-0049) printed forms in Telugu when the school's parent language (ADR-0044) was Telugu, or when `language=te` was asked, even with `SOS_TELUGU_ENABLED` off. ADR-0036 (English first, product owner 2026-09-30) says nothing Telugu is shown or printed while the switch is off; ADR-0044 describes the parent language as independent of the switch. The two read differently for printed forms.
2. **Closed incidents.** ADR-0050 has two operators confirm a SchoolOS incident before schools are told, but did not say what may happen after it is closed. As built, a closed incident could still be edited or reopened by one operator, so the record schools pulled could change after the fact without a second person.

## Decision

1. **Consent forms (D47).** While `SOS_TELUGU_ENABLED` is off, consent forms and their PDFs MUST be English, whatever the school's parent language or the `language` asked for. When the switch is on, the asked language is used, else the school's parent language. The language is read only through `app.core.languages` (CLAUDE.md §1).
2. **Closed incidents (D48).** Once a SchoolOS incident is `closed`:
   - it MUST NOT be changed: `PATCH`, adding schools and confirming answer 409 `incident_closed`;
   - it MAY be reopened only by a recorded two-person action: one operator with `platform.incidents.manage` requests it with a reason (the summary rule applies: categories and counts only, no long numbers, e-mail addresses or Aadhaar numbers), a **different** eligible operator confirms it within the two-person window, and the requester may withdraw it before then;
   - each step is written to the platform audit chain (`incident.reopen_requested`, `incident.reopened`, `incident.reopen_withdrawn`), without the reason text;
   - the database enforces that a pending reopen exists only on a closed incident and that its reason follows the summary rule (`0068_incident_reopen`).

## Consequences

- Good: printed forms follow the English-first rule with no exception to explain to schools; a closed incident that schools have pulled cannot change without a second operator and an audit trail.
- Bad / costs: a school that wants Telugu consent forms waits for the Telugu switch (ADR-0036); reopening takes two people, which is slower in an emergency.
- Follow-up work: none beyond the R3-2F wave that built both (main, 2026-11-01). APAAR consent forms keep the language rule of ADR-0039/D9 inside exports; align them when the Telugu switch is next reviewed.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep Telugu consent forms for Telugu-language schools while the switch is off | Contradicts ADR-0036's English-first rule for printed output; the Telugu text has not had its review (open item) |
| Freeze only some fields of a closed incident | Harder to explain and to check; any change after closing is rare and deserves a second person |
| Let one operator reopen, with an audit entry | The incident's own confirmation already needs two people (ADR-0050); reopening changes what schools were told, so it needs the same |

## Related requirements

FR-DPDP-023, FR-DPDP-030, FR-DPDP-031, PRV-035, PRV-036; ADR-0036, ADR-0044; docs/03 §3.26, docs/05 §16, docs/08 §16, docs/09 §4, docs/16 §5.15a.

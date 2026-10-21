# ADR-0049: Consent register for optional purposes, beside the APAAR register and WhatsApp opt-ins

| Field | Value |
|---|---|
| Status | **Accepted (lead, 2026-10-21; owner may revisit)** (docs/18 §8 D40) |
| Date | 2026-10-21 |
| Deciders | Lead engineer (for the product owner, who may revisit) |
| Amends / supersedes | none. Reuses the pattern of ADR-0039 (append-only decisions with the signed form as evidence); does not change ADR-0039 or ADR-0043 |

> **Not legal advice.** The children's-data rules here are those of docs/08 §4 (DPDP Rules, Fourth Schedule Part A, as summarised there).

## Context

- docs/08 §4: an educational institution is exempt from verifiable parental consent only where processing is restricted to its **educational activities** or the **safety of enrolled children**. Anything outside those (for example a child's photo on the school's website or social media, a child's name in a public results list, sharing with a third party for a trip or a competition) needs the school to choose a lawful basis, often consent. PRV-006 already promises: "Where the school chooses consent, SchoolOS records who consented, when, for what, and supports withdrawal."
- Two consent-like registers already exist with their own rules: APAAR consent (`sis.apaar_consents`, ADR-0039: one purpose, refusal printed next to consent, refused children never pushed to APAAR actions) and WhatsApp opt-ins (`msg.optins`, ADR-0043: per guardian and category, STOP within 60 s, Meta policy). Folding them into a generic table would weaken those rules or need a large migration of live data.
- docs/18 R3 item 1 asks for a "consent register (shared with APAAR consent)".

## Decision

1. **A purpose list per school.** `sis.consent_purposes` (C1): `key`, `label_en` (and `label_te` while parent forms may be Telugu, ADR-0044), `description` (what, why, who receives it, for how long), `form_version`, `active`. SchoolOS ships suggested purposes in `app/privacy/consent_purposes.yaml` (photos and video on the school's own website and social media; name and photo in public results or prize lists; sharing with a named outside organiser; others the school adds); the school enables, edits or adds its own (`tenant.settings.manage`ᴿ). A purpose's text is frozen once a decision refers to its version; changing it makes a new version.
2. **Append-only decisions with evidence.** `sis.consents` (C2): per student and purpose, `given`, `refused`, `withdrawn` or `pending`, with who decided (relationship, guardian when on record), the date on the form, the purpose version, the signed form as evidence (**required for `given`**), recorded by whom and when; `seq` per student and purpose; the app role has SELECT and INSERT only (same rules as `sis.apaar_consents`: no return to `pending` after a decision, `withdrawn` only after `given`, `If-Match` on the count read). No decision means **not given**: modules treat a missing or pending consent exactly like a refusal.
3. **Printable forms with refusal.** Forms per student or section, in the parent language, offering "I agree" and "I do not agree" side by side and saying that admission and studies do not depend on the answer (the ADR-0039 pattern), one purpose per tick box; never an Aadhaar number.
4. **One read-only consent view.** The "Consent register" screen and the access report (ADR-0048) show, per student, the optional purposes, the APAAR decision (through `apaar.service`) and the guardians' WhatsApp opt-ins (through `messaging.service`), each with its own source and rules. APAAR and opt-ins stay recorded in their own screens and tables.
5. **Other modules ask, never assume.** `privacy.service.consent_state(student_id, purpose_key) -> given | not_given` is the only way a module checks a purpose; R3 itself has no automatic use (no photo publishing exists). A withdrawal is a new entry and takes effect for future use; what the school already published elsewhere is the school's to take down (the screen says so).
6. **Withdrawal requests.** A withdrawal asked through the data-principal register (ADR-0048, kind `consent_withdrawal`) is recorded here as `withdrawn`, linked to the request.
7. Permissions `consent.read` and `consent.record` (docs/07 §6.8), shaped like `apaar.consent.read` / `apaar.consent.record`.

## Consequences

- Good: PRV-006 becomes real for any optional purpose without touching the two registers that already work; one view answers "what has this family agreed to?".
- Bad / costs: three stores of consent-like facts; the view and the access report must call three services (covered by the registry test of ADR-0048).
- Follow-up: if a later feature publishes photos or names (for example on the disclosure page), it must call `consent_state` first; the R3 disclosure page shows no student data at all (ADR-0051).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Generalise `sis.apaar_consents` with a `purpose` column | Changes a live append-only register and its DQ skip rules; APAAR has court-driven wording that must not drift |
| Fold WhatsApp opt-ins in | Opt-ins are per guardian and category with STOP semantics and Meta policy; different subject and lifecycle |
| Free-text "consents" note on the student | Not evidence; no versions; cannot answer "who agreed to what, when" |

## Related requirements

FR-DPDP-020..026, PRV-006, PRV-035; docs/02 C27; docs/03 §3.27; docs/05 §16.2; docs/07 §6.8; docs/08 §16; docs/17 §5.10.

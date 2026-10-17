# ADR-0044: Parent-facing language per school (amends ADR-0036 and ADR-0039)

| Field | Value |
|---|---|
| Status | **Accepted** 2026-10-14: decisions 1-3 are the owner's (D9 of 2026-10-10); decisions marked **(P)** were accepted by the owner on 2026-10-14 (D23, docs/18 §8: per-guardian override yes; a fluent Telugu reviewer signs off before any Telugu school goes live) |
| Date | 2026-10-14 |
| Deciders | Product owner (D9: "English staff UI; parent-facing WhatsApp templates and parent pages may be Telugu or English per school"); engineering (design) |
| Amends / supersedes | **Amends [ADR-0036](ADR-0036-english-first-telugu-hidden.md)** decision 2 ("with the switch off nobody sees Telugu") for parent-facing output only. **Amends [ADR-0039](ADR-0039-apaar-consent-register-and-pen.md)** decision 4 (the APAAR form language becomes an override of the school's parent language; decision 7 below). ADR-0036 stays in force for everything staff see |

## Context

ADR-0036 hid Telugu everywhere behind `SOS_TELUGU_ENABLED` (off). R1 made one exception for the printed APAAR consent form (ADR-0039 decision 4, `sis.apaar_consent_settings.form_language`). R2 adds three parent-facing surfaces: WhatsApp templates (ADR-0043), parent link pages (ADR-0046) and parent verification slips (R1, ADR-0040). Owner decision **D9**: the staff UI stays English; **parent-facing** templates, pages and forms may be Telugu or English **per school**, with Telugu-speaking support. Research found no evidence on whether AP parents read English notices (docs/18 §5); per-guardian choice is a plausible need. Meta approves each language of a template separately.

## Decision

1. **One school setting, separate from the staff switch (owner D9).** `core.tenants.settings.parent_language` = `en` (default) or `te`, changed by `tenant.settings.manage` (step-up), audited. It decides the language of everything a parent receives: WhatsApp templates, parent link pages, printed parent slips and forms. It is read only through `app.core.languages.parent_language(settings, guardian=None)` (API, worker) and its web counterpart for the public pages. It does **not** depend on `SOS_TELUGU_ENABLED`: with the switch off, staff screens, staff PDFs, notifications and AI answers stay English exactly as ADR-0036 says.
2. **What "parent-facing" covers (owner D9).** Exactly: WhatsApp template text and buttons; the public parent pages under the parent origin (notice, receipt, dues, opt-in and opt-out confirmations) and the certificate verification page's labels; printed forms and slips handed to parents (APAAR consent form, readiness verification slip, opt-in slip). Not covered (always English while the switch is off): staff screens, receipts and certificates as documents (the school's records), registers, exports, invoices, e-mails to staff, AI answers.
3. **Telugu font only where Telugu is shown (owner D9 with ADR-0036 decision 2).** The parent pages and parent PDFs load the self-hosted Noto Sans Telugu only when their language is `te`; staff pages never load it while the switch is off.
4. **(P) Per-guardian override.** `sis.guardians.preferred_language` (`en`, `te`, or null = the school's choice), set by staff on the guardian record, or by the parent on an opt-in page ("Read messages in: English / తెలుగు"). Messages and pages for that guardian use it. A notice broadcast to a section therefore goes out in up to two languages.
5. **(P) Content in both languages.** A parent-facing text exists in both languages before Telugu can be chosen: templates carry `en` and `te` bodies in `templates.yaml`; M4 notices already have `title_te`/`body_te` columns (dormant): when the school's parent language is `te`, approving a notice that will be sent requires the Telugu text (the AI draft may propose it, the person confirms; invariant 9). If a Telugu variant is missing or its Meta template is not approved, the English variant is sent and the delivery report says so (never a silent fallback).
6. **(P) Telugu text is reviewed before use.** Each Telugu template and page catalogue entry carries `reviewed: false` until a fluent reviewer signs it off; the school setting shows "Telugu messages have not been reviewed yet" while any entry it would use is unreviewed. Meta approval of a Telugu template does not count as review.
7. **(P) Reconciling ADR-0039.** `sis.apaar_consent_settings.form_language` becomes nullable: null (the new default after migration, existing explicit values kept) follows `parent_language`; a value overrides it for that form only. The per-print override stays.
8. **(P) Inbound keywords** in Telugu (`ఆపు` for stop, `చేరండి` for join) are accepted whatever the school's language; English keywords always work.

## Consequences

- Good: a school whose parents read Telugu can send Telugu notices without turning Telugu on for staff; staff screens stay simple and English.
- Bad / costs: two template languages per school to approve with Meta; a native reviewer is needed before Telugu goes live; ADR-0036's "no Telugu anywhere" test must be narrowed to staff surfaces, and a new test pins that parent surfaces follow `parent_language` and nothing else.
- Follow-up: update `docs/17 §5.4` (parent origin language rules), the bring-Telugu-back checklist in docs/14 (parent surfaces are already done), the eval suites (no AI writes parent text in R2 except the M4 notice draft, which a person confirms).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Turn `SOS_TELUGU_ENABLED` on | Brings Telugu back to every staff screen and AI answer; the owner decided English staff UI |
| Per-guardian only, no school setting | Most schools want one default; the per-guardian choice is an override |
| Bilingual messages (English and Telugu in one template) | Longer messages, separate Meta approval anyway, harder to read on a phone |
| A separate setting per surface (pages, templates, forms) | More settings for the same decision; the APAAR form keeps its override for compatibility only |

## Related requirements

FR-LANG-001..006 (docs/03 §3.23), FR-APC-004 (amended), FR-MSG-006, FR-PUB-008; US-2301..US-2303 (docs/02 C23). Docs: 02 §1 principle 6, 05 §1 (Telugu columns), 09 §2 Language, 17 §5.4, §5.8.

## Follow-up (2026-10-17, wave R2-0 built)

`app.core.languages.parent_language(school, guardian)` (guardian, then school, then English) is built, with the school setting `parent_language` (audited with its value), `sis.guardians.preferred_language` (via the existing guardian PATCH) and the APAAR form language nullable (null follows the school; existing explicit values kept). FR-LANG-005's list of unreviewed Telugu texts comes from `app/core/parent_texts.yaml` (sources: the APAAR consent form, the readiness slip, the public page shell; later waves add templates and the parent catalogue). The staff "no Telugu with the switch off" test is narrowed to allow the word "Telugu" in the parent-language settings card; no Telugu script reaches a staff screen. The public pages load the self-hosted Telugu font from `/p/fonts/telugu/*` only when the page is Telugu.

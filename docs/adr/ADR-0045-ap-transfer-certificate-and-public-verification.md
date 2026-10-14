# ADR-0045: AP transfer certificate format and public certificate verification

| Field | Value |
|---|---|
| Status | **Accepted** 2026-10-14: the R2 scope is the owner's (2026-10-10; D3 public sources); decisions marked **(P)** and decision 3 were settled by the owner on 2026-10-14 (D15, D29, docs/18 §8) |
| Date | 2026-10-14 |
| Deciders | Product owner (scope, D3); engineering (design) |
| Amends / supersedes | Extends the M3 certificate design (docs/05 §5.7, FR-CERT-001..014). Relies on ADR-0047 (public routing). Does not change FR-CERT-009 for C3 fields without the owner (decision 3) |

## Context

M3 issues TCs and other certificates from the checked record, with gap-free serials, maker-checker for TCs, frozen content and PDFs (docs/05 §5.7). Fields of the official AP TC that SchoolOS cannot fill print as labelled blanks (`official_format_todo`). The research found only an unofficial template of the AP TC (pupil and parent names, nationality, religion, caste status "as specified in AP Educational Rules", date of birth as in the admission register in figures and words, class at leaving, languages studied, and similar) and no notification text (regulation notes §3). UDISE+ asks the new school to import a transferring child by PEN; a PEN printed on the TC helps (ADR-0039, source `tc_incoming`).

Receiving schools, employers and boards cannot check a paper TC today except by calling the issuing school. Forged and altered TCs are a known nuisance. A public check must not become a way to enumerate a school's students or to learn which schools use SchoolOS.

## Decision

1. **AP TC as a versioned format (owner scope, D3).** `app/certificates/formats/ap-tc-2027.yaml` lists the AP TC fields in order with, for each, the SchoolOS attribute or TC input that fills it, `source` URLs and `verified: false` until a pilot school confirms it. A school chooses its TC format in certificate settings (`generic-v1` today, `ap-tc-2027`, later CBSE/CISCE formats); an issued certificate stores its format and template version, so later format changes never alter it. New TC inputs (for example identification marks, languages studied, medium, whether qualified for promotion, date of application) are typed at request time and frozen with the content; new student attributes need their own catalogue migration and classification review.
2. **PEN and APAAR on the TC (P).** The TC prints the UDISE+ PEN when the school holds a verified value, and the APAAR ID only when the parent consented and the ID is verified (never for a refused or withdrawn consent, ADR-0039).
3. **C3 fields stay blank unless the school asks (owner decision D15, 2026-10-14).** The AP template asks for religion and caste status. FR-CERT-009 forbids printing C3 attributes. The format lists them with `restricted: true`; they print as labelled blanks the office fills by hand, unless the school turns on the certificate setting `print_restricted_tc_fields` (principal or owner, `tenant.settings.manage` with step-up, audited with the field keys), an exception to FR-CERT-009 for the TC only.
4. **Every issued certificate gets a verification code and a QR (owner scope).** At issue (and for each duplicate) SchoolOS creates a **verification code** of 16 characters from a 32-character alphabet without look-alike letters (80 bits, printed in groups, for example `7K4M-Q2XP-9HD3-RT6W`) and a public handle whose key is the code's SHA-256 (ADR-0047). The PDF prints the code, the QR of `{SOS_VERIFY_BASE_URL}/v/{code}` and the line "Check this certificate at <host>/v". The code is stored with the certificate (`sis.certificate_verifications`), never derived from the serial, the student or the school. Certificates issued before R2 get a code when a duplicate is issued or on the principal's request ("add verification code", audited); the original paper then cannot show it, so the duplicate is the verifiable copy.
5. **The public page shows only what the certificate shows (owner scope; full printed content confirmed by the owner, D15).** For a valid code: the school's name and place as printed, the certificate type, serial, date of issue and the **frozen printed content** of that certificate (exactly `sis.certificates.content`), with "Valid: issued by <school> on <date>". For a duplicate: the same plus "Duplicate copy n of serial …". For a cancelled certificate: school, type, serial, "Cancelled on <date>", and **no printed content**. Nothing from the live record is shown (no current class, no later changes, no photo, no other certificates, no contact data), and nothing at all for an unknown, mistyped or withdrawn code: the same "We could not find a certificate with this code. Check the code or contact the school that issued it." with HTTP 404.
6. **(P) Manual check by code.** `/v` offers a box for the code (dashes and case ignored). There is no lookup by serial, name or date of birth, so the sequential serial is useless for enumeration.
7. **(P) Enumeration and abuse controls.** 80-bit codes; per-IP limits (20 lookups per 10 minutes, then 429; failed lookups count double), a global failed-lookup alarm, constant-shape 404 answers, no school identifier in the URL, no `robots` indexing (`noindex`, `X-Robots-Tag`), no referrer leakage (`Referrer-Policy: no-referrer`), no caching by shared caches (`Cache-Control: private, no-store`). Each successful lookup is audited in the school's chain (`certificate.verified_publicly`: certificate id, status shown, hashed client address) and counted on the certificate so the school can see it was checked.
8. **(P) Revocation.** Cancelling a certificate keeps the handle (so the page can say "Cancelled"); the principal can **withdraw** a code (for example a code posted online by mistake) with a reason, after which the page says "not found" and a duplicate can carry a new code.
9. **(P) Where the page lives.** Shared tier: a separate verification host `SOS_VERIFY_BASE_URL` (for example `https://verify.schoolos.in`; owner decision D29), distinct from the staff app and from the parent origin (ADR-0046). Dedicated tier: the school's own host (`https://<public_host>/v/…`), so a dedicated school's certificates never point at shared infrastructure.

## Consequences

- Good: anyone holding the paper can check it in seconds; forged or altered TCs are caught; the school sees that its certificates are being checked.
- Good: the page reveals nothing the holder of the paper does not already have.
- Bad / costs: the AP format is unverified until a school confirms it; schools' paper stock layouts vary; C3 fields remain blank pending the owner.
- Bad / costs: anyone who photographs a certificate can see its verified content online; that is inherent in the design and stated on the page ("Anyone with this code can see what the certificate shows").

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Verify by serial plus date of birth | Serials are sequential; date of birth is guessable within a class; both are personal data typed into a public form |
| A signed QR (offline check) only | Cannot show a later cancellation; needs a verifier app |
| Show only "valid / not valid" without content | Does not catch an altered field on a genuine certificate |
| Put the school code in the URL | Lets anyone list which schools use SchoolOS and probe each |
| DigiLocker issuance | Requires issuer onboarding by the board or state; a later option for boards, not schools |

## Related requirements

FR-CERT-015..024 (docs/03 §3.18), FR-PUB-001..004, SEC-036, SEC-039, PRV-029; US-2001..US-2006 (docs/02 C20). Docs: 05 §15.4, 07 §11.2, 09 §4 "Public", 17 §5.8.

# ADR-0007: Never store Aadhaar numbers

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

Schools need to compare the student's name, date of birth and gender as printed on the Aadhaar card with the admission register, because mismatches break APAAR generation and board registration (01-BRD P2). They do not need the Aadhaar number itself to do this. UIDAI circulars have directed organisations that store Aadhaar numbers to keep them in a separate encrypted "Aadhaar Data Vault", and which organisations must comply has become unclear over time (08 §5). Aadhaar numbers also appear by accident in scans, photos, spreadsheets and typed text.

## Decision

- SchoolOS **never stores, displays, logs, exports, embeds or sends to an AI provider a full Aadhaar number** (BR-02, PRV-013).
- Only `aadhaar_last4` and the as-printed name, date of birth and gender are stored, as C3 data encrypted with the tenant DEK (PRV-014). Display format: `XXXX XXXX 1234`.
- Input fields reject 12-digit Aadhaar-like input with an explanation (FR-STU-012, US-303).
- `core.redaction` masks any 12-digit sequence (allowing spaces and hyphens) that passes the **Verhoeff checksum** in OCR output, extracted text, imports, document ingestion, logs, prompts and exports (FR-IMP-022, PRV-015; CLAUDE.md §6 invariant 4).
- Images that appear to contain a full Aadhaar number are stored only after the number region is blacked out; the original is discarded (PRV-016).

## Consequences

- Good: removes the Aadhaar Data Vault question and a large class of breach impact.
- Good: mismatch detection still works on as-printed fields.
- Bad: the redaction pipeline must be applied everywhere text flows; enforced by tests (12 §4.6).
- Bad: Verhoeff-based detection can mask other 12-digit numbers by chance; acceptable because masking is safe.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Store full numbers in an encrypted vault | Unnecessary for the product's purpose; adds legal and security burden |
| Store a hash of the number | 12-digit space is small; hashes are brute-forceable; still sensitive |
| Rely on users not to enter numbers | Numbers appear in scans and spreadsheets regardless |

## Related requirements

BR-02, FR-STU-012, FR-IMP-022, NFR-PRV-003, PRV-013..016, SEC-013; 05 §8; 06 §4.3; 08 §5; 12 §4.6.

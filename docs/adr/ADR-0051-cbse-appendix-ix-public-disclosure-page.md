# ADR-0051: CBSE Appendix IX disclosure: a published, frozen page on the public origin and a file for the school's website (amends ADR-0047 decision 1)

| Field | Value |
|---|---|
| Status | **Accepted (lead, 2026-10-21; owner may revisit)** (docs/18 §8 D42). Adds one public handle kind; no new `SECURITY DEFINER` function and no new `definer_access` table |
| Date | 2026-10-21 |
| Deciders | Lead engineer (for the product owner, who may revisit) |
| Amends / supersedes | Amends [ADR-0047](ADR-0047-routing-public-requests-and-webhooks-to-a-school.md) decision 1 (adds the kind `school_disclosure` to `core.public_handles.kind`); decision 2 (the resolver) is unchanged |

## Context

- Owner decision D10 (2026-10-10) puts the **CBSE Appendix IX page** in scope; docs/18 §3 R3 item 2: "generated from data SchoolOS already holds (no student PII)".
- CBSE's affiliation bye-laws require affiliated schools to publish a "mandatory public disclosure" on their website in the format of Appendix IX. **The field list below comes from public secondary summaries of the Affiliation Bye-Laws 2018 Appendix IX, not from a checked official copy; it is `verified: false` (D3 pattern) until a CBSE school confirms it**: general information (school name, affiliation number, school code, address, principal's name and qualification, e-mail, phone); documents (affiliation letter, trust or society registration, NOC, recognition certificate under the RTE Act, building safety, fire safety, DEO self-certification, water, health and sanitation certificates); results and academics (fee structure, annual academic calendar, school management committee, parent-teacher association, board results of the last three years for Classes X and XII); staff (principal, number of PGT, TGT and PRT teachers, teacher-section ratio, special educator, counsellor and wellness teacher); infrastructure (campus area, classrooms and labs with sizes, internet, toilets for girls and boys, a link to a video of the school's infrastructure).
- SchoolOS already holds some of it: the school's name, letterhead address and affiliation line (school settings), the principal (a member with the principal role), classes and sections (structure), the fee structure (R2 fee plans, when fees are on), staff counts by role, the academic year's dates. The rest (qualifications, certificates as documents, areas, results, committees) must be typed or uploaded by the school.
- Public pages exist (ADR-0046, ADR-0047): a separate origin, a handle table with one resolver, a uniform 404, no school identifier in URLs.

## Decision

1. **A versioned format file.** `app/disclosure/formats/cbse-appendix-ix.yaml` lists the sections and items in CBSE's order; each item says how it is filled: `school` (from school settings), `derived` (computed from SchoolOS data: section count, teacher-section ratio, staff counts by role, the active fee plans as a table of heads and annual amounts per class), `input` (typed by staff, with type and limits), `document` (an uploaded PDF), `link` (an https URL, for the video). It carries `source` URLs and `verified: false`. Only CBSE schools (`boards` contains `CBSE`, FR-TEN-020) see the feature.
2. **Draft, then publish a frozen snapshot.** Staff edit a draft (inputs and chosen documents; derived values shown as they are now). **Publishing** (`disclosure.publish`ᴿ) freezes everything into an append-only `ops.disclosure_snapshots` row: the full content as JSON (derived values copied, not live), `content_sha256`, the document version ids, the format version, who published and when. The public page always shows the latest published snapshot; nothing becomes public until a person publishes it, and publishing again replaces it. A snapshot can be withdrawn (the page then answers the uniform 404) with a reason.
3. **No student personal data, by construction.** The format allows only school-level facts and aggregates; it has no item that names or counts fewer than 10 children (board results are given as numbers appeared and passed and pass percentage per class and year, never names; a count under 10 is shown as "fewer than 10"). Inputs refuse Aadhaar-like and phone-like numbers except in the two contact fields; documents are chosen from the school's documents and the publisher confirms "These documents contain no student's personal data" (the check is the person's; the documents' own Aadhaar masking still applies). Names published are adults in public roles (principal, committee members) that the school types and is responsible for.
4. **Served through a public handle on the public origin.** A new handle kind `school_disclosure` (one live handle per school; no expiry; revocable, which changes the link) maps an opaque 22-character key to the school; the page is `{SOS_PUBLIC_BASE_URL}/d/{key}` on the shared tier and the same path on a dedicated host's public host. The route `GET /api/v1/public/disclosure/{key}` declares `require_public_handle("school_disclosure")`, is listed in `app/public/public_routes.yaml`, and returns the snapshot content as an explicit allowlist; documents open through `GET /api/v1/public/disclosure/{key}/documents/{n}` (presigned ≤ 5 minutes, only document versions in the snapshot). The resolver is unchanged: like other parent kinds, the handle resolves only while the school is `active` (a suspended school's page is the uniform 404; its downloaded file still works). Rate limits `public_ip` and `public_lookup_fail` apply; no view counts beyond a per-day total.
5. **A file for the school's own website.** Each snapshot can be downloaded as a self-contained HTML file (no scripts, inline styles only, links to the hosted documents) and as an A4 PDF, so the school can publish on its own site, which is where CBSE expects it. The hosted page and the file show "Published by <school> on <date>" and "Prepared with SchoolOS".
6. **Indexing.** The page sends `X-Robots-Tag: noindex` like other public pages (the school links to it from its website; the link is not a secret but is not advertised by SchoolOS). Headers otherwise as FR-PUB-010 (no cookies needed: the page is read-only and needs no exchange-and-redirect).
7. Permissions `disclosure.edit` and `disclosure.publish`ᴿ (docs/07 §6.8). Tables in `ops` (no new schema).

## Consequences

- Good: one place to keep the disclosure current; derived numbers never drift from the school's records at the moment of publishing; no student data can appear on it.
- Good: the static file means a school without a website builder can still comply by uploading one file.
- Bad / costs: a new public route and handle kind to keep in the public-surface security suite and the penetration-test scope; the format is unverified until a CBSE school confirms it (D3).
- Follow-up: a CBSE school confirms the format; state-board disclosure duties, if any, are out of scope.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| A live page computed on every request | Numbers change without anyone deciding to publish; a bug could expose live data; snapshots are what an inspector expects |
| School code or affiliation number in the URL | Enumerates SchoolOS schools (ADR-0047 context); an opaque key costs nothing |
| Only a downloadable file, no hosted page | Many small schools have no website or cannot update it; the hosted link is the fallback |
| Indexable page | Advertises which schools use SchoolOS; the school's own site is the indexed copy |

## Related requirements

FR-DISC-001..012, FR-PUB-003, FR-PUB-011, SEC-036, SEC-050, PRV-037; docs/02 C29; docs/03 §3.28; docs/05 §16.4; docs/07 §6.8, §11.3; docs/09 "R3 planned endpoints"; docs/17 §5.10.

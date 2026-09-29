# ADR-0032: Tally edge agent, device credentials and the `get_fee_dues` tool

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-09-29 |
| Deciders | Product owner (pending); security review (pending) |
| Amends / supersedes | Would amend [ADR-0008](ADR-0008-tools-not-text-to-sql.md) (adds `get_fee_dues` to the tool whitelist) and CLAUDE.md invariant 2 (adds the edge-agent guard to the allowed route guards) once accepted |

## Context

M6 (docs/14): "edge agent (Windows service) reading TallyPrime via XML over HTTP on localhost ·
configured ledgers only · sync to SchoolOS · `get_fee_dues` tool for accountant/management.
Exit: fee-due questions answered from synced Tally data; accountant confirms figures match
Tally." docs/03 §5 fixes the direction: "TallyPrime XML over HTTP on the office PC → agent →
HTTPS to SchoolOS; Tally is not internet-reachable; agent initiates outbound only". docs/10 §13
asks for a small signed Windows service, a per-device credential revocable from the admin
console, only configured data, offline queueing and local logs without personal data.

Why this needs a decision:

1. **A new trust boundary (TB9).** Until now every caller of a tenant route was a person with an
   OIDC session behind the BFF (TB2), and the only machine caller was a dedicated host's
   heartbeat to the control plane (TB7, `require_fleet_signature()`, docs/16 §12). The agent is a
   program on a shared office PC that SchoolOS does not manage, writing school data into a
   school's tables. CLAUDE.md invariant 2 lists the route guards the route-enumeration test
   accepts; a new one needs an ADR.
2. **New personal data.** Tally party ledgers are usually named after the student or the parent
   ("K. Ravi Kumar 9B") and carry what the family owes. That is personal financial data about a
   child's family (docs/05 §8).
3. **A new AI tool.** ADR-0008 fixes the record-tool whitelist; `get_fee_dues` extends it.
4. **Facts checked on 2026-09-29** (TallyPrime help and the XML interface as documented by Tally
   Solutions; to be re-checked on the design partner's PC, PO question 1):
   - TallyPrime (and Tally.ERP 9) can act as an HTTP server on the PC ("Connectivity →
     Client/Server configuration → TallyPrime acts as Server", default port 9000). A client POSTs
     an XML `ENVELOPE`; `TALLYREQUEST` `Export` reads data, `Import` writes vouchers or masters.
   - Collections (`TYPE` Group / Ledger, `CHILDOF`, `BELONGSTO`, `FETCH`/`NATIVEMETHOD` Name,
     Parent, ClosingBalance, GUID) are exported with inline TDL; `SVCURRENTCOMPANY` picks the
     company, `SVTODATE` the date.
   - In XML exports a debit amount is written as a negative number (a debtor who owes 5,000 shows
     `-5000.00`); some reports write `5000.00 Dr`. Responses can contain character references
     that are not valid XML 1.0 (for example `&#4;`) and can be UTF-16 when so configured.
   - The server has no authentication of its own: anything that can reach the port can read
     and **write** the company's books. It MUST stay bound to the PC (firewall), which is also why
     SchoolOS never connects to it from outside.

## Decision

### 1. Shape

- A small **edge agent** (Python 3.12 package `sos_edge_agent`, `apps/edge-agent/`) runs as a
  Windows service on the PC that runs TallyPrime. It talks to Tally only at `127.0.0.1` /
  `localhost` (any other host is refused at start-up) and to SchoolOS only over HTTPS (TLS
  verification on, system trust store; plain `http://` only to `localhost` for development).
- **Outbound only.** SchoolOS never connects to the agent; the agent polls for its configuration
  and pushes snapshots. No inbound port is opened on the PC.
- **Read-only towards Tally.** The agent MUST send only `TALLYREQUEST` `Export` envelopes built
  by its own request builder; the builder refuses anything else and a unit test pins that no
  `Import` envelope can be produced. Tally's own port stays local (install guide: Windows
  firewall rule blocking 9000 from other machines).
- **XML parsing** uses `defusedxml` (no DTDs, no entities, no external references: XXE-safe),
  after removing character references that are invalid in XML 1.0 and decoding UTF-8/UTF-16 by
  BOM. Amounts are parsed as `Decimal` (never floats); `Dr`/`Cr` suffixes and the negative-debit
  convention are normalised to "amount the party owes the school" (positive = due, negative =
  advance/credit).

### 2. Enrolment and the device credential

- Only the **owner** (new permission `tally.device.manage`, critical, **step-up MFA**) creates a
  **one-time enrolment code** on the Tally connector screen. The code (12 characters from an
  unambiguous 31-letter alphabet, about 59 bits, shown once, grouped `XXXX-XXXX-XXXX`) is valid
  for **30 minutes**, for one enrolment, and is stored only as its SHA-256 hash
  (`ops.tally_enrolment_codes`). Creating it is audited (`tally.enrolment_code.created`).
- On the PC the installer (or IT person) runs `sos-tally-agent enrol --server <url> --school
  <school id> --code <code>` **as the service account**. The agent calls
  `POST /api/v1/edge/tally/enrol` (guard `require_edge_agent_enrolment()`): school id header,
  timestamp within ±5 minutes, a fresh nonce, the code in the TLS-protected body. A wrong,
  expired or used code is a plain 401 (no hint which), and at most **10 attempts per school per
  hour** are accepted (429 after that).
- A valid code creates a **device** (`ops.tally_devices`) and returns, **once**, a device id, a
  key id and a 256-bit random **device secret**. The code is marked used in the same transaction;
  the enrolment is audited (`tally.device.enrolled`, actor `system`, device id only). A school may
  have at most **2 active devices** (replacing a PC without a gap).
- **Server-side storage of the secret.** Request signing is HMAC (below), and an HMAC verifier
  needs the secret itself, so the secret cannot be stored as a one-way hash. It is stored
  **envelope-encrypted with the school's key wrapper** (KMS in staging/prod, encryption context
  bound to the school, exactly like the fleet heartbeat keys, `app/platform/fleet.py`), never in
  plaintext, never logged, never returned again. Enrolment codes, which only need comparing,
  *are* stored hashed. (Security question 3 asks whether to move to an Ed25519 device key pair,
  which would leave nothing secret on the server.)
- **Rotation.** The agent rotates its secret every 90 days (and on demand): a signed
  `POST /api/v1/edge/tally/key-rotation` returns a new key id + secret once; both keys are
  accepted until the first request signed with the new key (which retires the old one) or for at
  most 7 days. Audited (`tally.device.key_rotated`).
- **Revocation.** The owner (step-up) revokes a device on the connector screen
  (`tally.device.revoked`); its keys are erased at once and every later request is refused with
  401. Revoking does not delete synced data (the accountant still sees the last snapshot, marked
  with its date). Offboarding deletes everything (ADR-0029).

### 3. Request signing and the new guard

- Every agent request after enrolment carries `X-SOS-Tenant`, `X-SOS-Device`, `X-SOS-Key-Id`,
  `X-SOS-Timestamp` (Unix seconds), `X-SOS-Nonce` (16–64 URL-safe characters) and
  `X-SOS-Signature: v1=<hex HMAC-SHA256(secret, canonical)>` where

  ```
  canonical = "SOS-EDGE-HMAC-SHA256\n" + METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n"
              + NONCE + "\n" + hex(SHA-256(raw body))
  ```

  (PATH without query string; empty body hashes the empty string).
- `require_edge_agent_signature()` (FastAPI dependency in `app/tally/agent_auth.py`) checks, in
  this order, storing nothing on failure: feature flag on for the school (else 404) → device and
  key known, active and not revoked, school active (else 401) → timestamp within ±300 s (401) →
  body ≤ 1 MB (the API's request limit; 413) → signature in constant time (401) → nonce unseen for 10 minutes (409
  `replay`) → per-device rate limit (429). It resolves the school from the header and reads the
  device **inside that school's `tenant_session`** (RLS), so no new `SECURITY DEFINER` function or
  `definer_access` policy is needed (invariant 1).
- The guard carries `sos_edge_agent = "signature"` (`"enrolment"` for the enrolment guard) and the
  pseudo-permission `tally.agent`, which is **not** in the permission catalog and can be held by
  no role. The route-enumeration test accepts these two guards **only** on the five routes under
  `/api/v1/edge/tally/`, and no other guard there. Agent routes are excluded from the role matrix
  (like `/api/v1/fleet/`) and have their own tests.
- Rate limits: config 1 per 30 s, catalog 1 per minute, sync 1 per 4 minutes, key rotation 1 per
  hour per device; enrolment 10 per school per hour. The WAF rate rule (SEC-022) also covers
  `/api/v1/edge/`.

### 4. What the agent sends (configured ledgers only)

| Route | What | Personal data? |
|---|---|---|
| `GET /edge/tally/config` | The company and the ledger **groups** the accountant selected, the sync interval | No |
| `PUT /edge/tally/catalog` | Company name, Tally product/version, the list of ledger **groups** (name, parent) | No (group names; the accountant is told not to name groups after people) |
| `POST /edge/tally/syncs` | One snapshot: `batch_id`, company, as-of date, and for each party ledger **under a selected group only**: Tally GUID, ledger name, group, closing balance | **Yes**: ledger names are usually student or parent names (C2); the balance is financial |
| `POST /edge/tally/key-rotation` | Nothing | No |

- The **server enforces the selection**: a snapshot naming a group that is not selected is
  refused (422 `group_not_selected`), and at most 5,000 parties per snapshot are accepted.
  Vouchers, narrations, addresses, phone numbers, GSTIN, bank details and other groups are never
  requested.
- **Idempotent sync.** The agent generates `batch_id` once per snapshot and retries with the same
  id (fresh nonce, backoff with jitter up to 30 minutes). The server stores the batch in
  `ops.tally_syncs` (`UNIQUE (tenant_id, device_id, batch_id)`) and answers a repeat with the
  first result without applying it twice. A snapshot is complete: parties no longer present are
  marked `present = false`, never deleted while links point to them.
- Offline behaviour: the newest snapshot supersedes older ones, so the agent keeps at most the
  current unsent snapshot **in memory** and simply takes a fresh one when the network returns.
  Nothing personal is written to disk.
- Audit (same transaction, IDs and counts only): `tally.sync.received` (device, batch, counts,
  whether it was a repeat), `tally.catalog.updated`, `tally.groups.selected`,
  `tally.party.linked` / `unlinked` (party and student ids). Logs carry device/batch ids, counts
  and codes; never ledger names or amounts (log-redaction test).

### 5. Fee data classification and access

- Party ledger names and balances are **C2 personal data with a finance restriction**: readable
  only with `finance.read` (owner, principal, accountant, auditor; school-wide grants only), shown
  on the fee dues and mapping screens, never in URLs, logs, notifications or audit summaries.
  They are not app-layer encrypted in this version (PO question 4 asks whether to treat them as
  C3).
- Two new permissions: `tally.device.manage` (owner; step-up; enrolment codes and revocation) and
  `tally.configure` (owner, principal, accountant; choose groups and link parties to students).
- Retention: the latest snapshot per party is kept while the connector is on; sync records
  (counts only) for 13 months; everything is deleted at offboarding (ADR-0029) and included in
  the school's full data export (FR-ADM-001).

### 6. Party ↔ student mapping and `get_fee_dues`

- A party is linked to a student **only by a person** holding `tally.configure`, on the mapping
  screen (`ops.tally_party_links`, many-to-many: a family ledger may be linked to each sibling).
  SchoolOS may **show** candidate students whose name or admission number appears in the ledger
  name to make linking faster, but it never links by itself, and **the AI never maps**: an
  unlinked party is invisible to `get_fee_dues`.
- `get_fee_dues` (knowledge record tool, read-only, whitelisted by this ADR): permission
  `finance.read` held **school-wide**; offered to the model only when the school's
  `tally.connector.enabled` flag is on. Input: `student_id` (from `find_students`) or nothing
  (a school summary). With a student it first checks the student is in the caller's scope
  (`students.service`), then selects in SQL only the parties **linked to that student**; the block
  gives each linked ledger's closing balance, the total, the Tally as-of date and the sync time.
  Without a student it returns numbers only (students with dues, total due, unlinked parties).
  Source URIs are `sos://fee/{id}` (an id derived from the school, student and snapshot; no
  names). No other party, name or amount can reach the model (invariants 8, 9).
- Eval gates (docs/06 §13.4, `evals/`): fee figure accuracy = 1.00, fee leakage = 0, fee
  citation validity = 1.00, guessed links = 0 (hard); refusal correctness ≥ 0.95 (hard).

### 7. Agent distribution, updates and local storage

- The agent is a pure-Python package with one runtime dependency (`defusedxml`, PSF-2.0, already
  used by the API). HTTP uses the standard library (`urllib`), configuration TOML (`tomllib`),
  Windows DPAPI through `ctypes` (no pywin32). The Windows service wrapper is **WinSW** (MIT),
  configured by `apps/edge-agent/windows/sos-tally-agent.xml`; it runs `sos-tally-agent run`
  under a dedicated local account.
- **On the PC** only the device credential is sensitive. It is stored with **DPAPI in the service
  account's user scope** (`CryptProtectData`, entropy bound to the school id), so other Windows
  users of the shared office PC cannot read it. The configuration file holds only the server URL,
  school id, device id and Tally port. Logs are local, rotated, and carry counts and codes only.
  On non-Windows systems (development, CI) a file store is used only with the explicit
  `--insecure-file-store` flag.
- **Updates**: releases are built in CI, signed with the SchoolOS code-signing certificate
  (Authenticode) and published with a SHA-256 manifest; the agent reports its version on every
  request and the server returns `min_agent_version` in `/config`; an agent below it stops
  syncing and says so on the connector screen. Automatic self-update is **not** built in this
  version (PO question 6): IT staff install the new MSI.
- **Supported Tally**: TallyPrime (release 2.x and later) with the XML/HTTP server enabled on the
  PC; Tally.ERP 9 release 6.x is expected to work with the same requests but is not tested. The
  version reported in the catalog is shown on the connector screen.

### 8. Rollout

Everything above is built **behind the per-school feature flag `tally.connector.enabled`, which
defaults OFF** (unknown flag = off, `app.core.feature_flags`). With the flag off every school
route and agent route answers 404, the nav entry is hidden and `get_fee_dues` is not offered to
the model. The flag is not to be switched on anywhere until this ADR is accepted.

## Consequences

- Good: fee questions can be answered from the school's own Tally data without re-entry
  (BR-13); SchoolOS never reaches into the school network; the agent can only read.
- Good: one guard, one canonical string and one test vector shared by server and agent; the
  signature scheme mirrors the fleet heartbeat already reviewed.
- Good: no new definer function, no new database role, no cross-tenant path.
- Bad: the device secret is recoverable server-side (wrapped by KMS) because HMAC needs it.
- Bad: a new program on customer PCs to build, sign, support and update; Tally versions differ.
- Bad: party-to-student linking is manual work for the accountant (about 2,000 links at the
  design partner, once); candidate suggestions reduce it but a person still confirms each.
- Follow-up: MSI installer and code-signing certificate; bill-wise dues (term-wise) if the PO
  wants them; staging test against a real TallyPrime; DPIA update (fee data).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| SchoolOS connects to Tally over the internet (port forward / tunnel) | Tally's XML server has no authentication and can write vouchers; exposing it is unacceptable (docs/03 §5) |
| Accountant uploads a Tally Excel export by hand | Works without an agent and may be a fallback (PO question 7), but the exit criterion is synced data without re-entry |
| Ed25519 device key pair instead of an HMAC secret | Nothing secret on the server; costs the `cryptography` wheel in the agent and a second signing scheme next to the fleet heartbeat. Kept as security question 3 |
| Tally's ODBC interface | Needs a Windows ODBC driver and exposes more of the company than needed; XML over HTTP is documented and scoped per request |
| AI suggests the party ↔ student mapping and applies it | Violates invariant 9 (AI writes) and risks attributing one family's dues to another child |
| A new `fin` schema and database role | Heavier bootstrap change for a handful of tables; `ops` already has the right grants and is not readable by `sos_readonly` |

## Questions for the product owner and security review

1. Which Tally version does the design partner run (PRD open question 5), on which Windows
   version, and who maintains the PC? Is Tally's XML server already enabled, and on which port?
2. Which ledger groups hold student fees at the design partner (for example "Sundry Debtors" or
   a group per class)? Are party ledgers named after the student, the parent, or the admission
   number?
3. Security: keep HMAC with a KMS-wrapped secret (built), or move to an Ed25519 key pair so the
   server stores only a public key?
4. Should fee balances be treated as C3 (app-layer encrypted, hidden unless asked) rather than C2
   with the `finance.read` restriction?
5. Who may enrol and revoke the agent: owner only (built), or also the principal?
6. Agent updates: manual MSI install (built), or a signed self-update channel?
7. Is a manual Excel upload of Tally outstanding needed as a fallback for schools without the
   agent?
8. Are bill-wise (term-wise) dues with due dates needed, or is the ledger closing balance enough?
9. When the agent is silent for 48 hours (default) the owner and accountant are notified in-app;
   is 48 hours right given that office PCs are off on Sundays and holidays?
10. May the accountant see fee dues for every student (built: `finance.read` is school-wide
    only), or should class teachers see their own class's dues (not built; would need a scoped
    grant and a new eval)?

## Related requirements

FR-TALLY-001..010 and US-1801..US-1805 (docs/03 §3.16, docs/02 C18; proposed, PO to confirm); docs/14 M6; docs/03 §5 (Tally interface), SEC-003, SEC-005, SEC-008, SEC-018, SEC-020, SEC-022,
FR-KB-004, FR-KB-005, FR-ADM-001, FR-PLT-005, BR-13; docs/04 §2, docs/05 §7.4, docs/06 §7, §13.4,
docs/07 §3 (TB9), §6.2, docs/08 §11, docs/09 §4, docs/10 §13.

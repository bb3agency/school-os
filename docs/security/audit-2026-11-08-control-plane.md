# Security audit 2026-11-08: control plane, fleet, public/webhook surface, worker and config

Auditor CP of the 2026-11-08 round, on a worktree at 987ded97. Scope:

- `app/platform`: provisioning, offboarding, billing (GST maths, invoice numbers), subscriptions
  and protected windows (D78), two-person rules, operator roles, the usage collector, the
  `tenant_audit` outbox (D68), the signed heartbeat answer and host-side apply (D79);
- `app/public`: handle resolver, parent links, the Meta and Razorpay webhook verifiers and replay
  window;
- `app/core/config.py` staging/prod start-up guards;
- the Celery beat schedule in both deployment modes;
- the server side of the edge agent (`/api/v1/edge/tally/*`);
- outbound httpx calls whose URL a school or operator can influence (SSRF).

Earlier rounds (`audit-2026-10-*.md`) are not repeated.

## Findings

| ID | Severity | Area | File:line | Description | Status |
|---|---|---|---|---|---|
| CP-01 | Medium | Config guards (SEC-009), online payments (FR-PAY-001) | `apps/api/app/core/config.py` `_guard_payments` (before the fix) | `SOS_RAZORPAY_OAUTH_MODE=test` was accepted in prod. docs/10 §11 says test mode is for staging only, but nothing enforced it. In prod, every OAuth connection would be made in Razorpay test mode. Test payments move no money, yet the worker re-fetches them with the same test token, sees them as `captured`, and issues **real receipts** in the school's gap-free series, which clear real dues. | **Fixed** in 6b55a770. Prod refuses any value other than `live` (staging keeps `test`). docs/10 §11 updated. Test: `tests/core/test_config.py::test_CP_01_SEC_009_prod_rejects_razorpay_oauth_test_mode` (failed before the fix), plus `test_CP_01_staging_allows_razorpay_oauth_test_mode`. |
| CP-02 | Low | Fleet key rotation (SEC-028) | `apps/api/app/platform/fleet.py` `_valid_keys`, `rotate_key` | After a rotation, the old heartbeat key stays valid for the full 7-day overlap, even after the host has started signing with the new key. While that overlap runs, a second rotation is refused (`409 rotation_pending`). If a host key leaked and that leak is why the operator rotated, the leaked key still works for up to 7 days, and the operator cannot cut it off sooner. The Tally agent already ends the old key at the first use of the new one (ADR-0032). The leaked key could forge that host's heartbeats (its usage counts and status) and read its signed answer (flags, AI allowance, billing summary); it reaches no other school and no student data. | **Reported** (documented design, docs/16 §12; a behaviour change). Proposed fix: when a heartbeat verifies with `heartbeat_next_key_id`, promote the next key under the deployment lock in `accept_heartbeat`, as the staleness sweep does. That keeps R-15 safe, because the host already uses the promoted key. Also add an emergency "revoke old key now" for platform owners. |
| CP-03 | Low (hardening) | Heartbeat signatures (ADR-0058) | `apps/api/app/platform/fleet.py` `sign`; `heartbeat_client.py` `verify_answer` | Requests and answers use the same MAC, `HMAC(key, ts "." body)`, with the same key and no direction label. Today a host's own request cannot be reflected back to it as an answer, or the reverse, only because the two schemas differ: `HeartbeatOut` requires `received_at`, `min_supported_version` and `announcements`, and `HeartbeatIn` is `extra="forbid"`. | **Reported**. Changing it is a protocol version (`v2=`) that hosts and the control plane must roll out together, as ADR-0058 did. Proposed: add a direction prefix (`"req."` / `"ans."`) under `v2=`, accepted alongside `v1=` for one release. |

## Outside my area

- **fees (Low-Medium):** `app/fees/online.py` (manual keys connect) accepts `rzp_test_` keys in prod and stores `environment="test"`. Those connections have the same effect as CP-01: test payments become real receipts. It needs an owner with `payments.configure` and step-up, so it is insider-only, but receipts are tax and fee records. Proposed fix: refuse a test-mode connection (manual keys or OAuth `live=false`) when `env=prod`.
- **messaging (Info):** a Meta webhook body has no timestamp. A captured signed body replayed after the replay window would be accepted again. A replayed `join` could re-opt-in a guardian who later sent STOP. Exploiting it needs the plaintext of a Meta delivery. Consider ignoring inbound opt-in events older than the guardian's latest STOP (by `event_at`).

## Verified clean

**Heartbeat request (`fleet.verify_heartbeat`)**
- The deployment and key are checked first.
- The timestamp must be within ±300 s; nonce TTL 600 s covers the whole window.
- The body is capped at 16 KB, and a global 1 MB body limit applies before that.
- `v1=` HMAC-SHA256 is compared with `hmac.compare_digest`.
- The schema is strict (`extra="forbid"`, no free text, bounded numbers and patterns).
- The body's `deployment_id`/`tenant_id` must match the registry.
- The nonce store is Redis `SET NX EX` and fails closed (503).
- Rate limit: one heartbeat per deployment every 55 s.
- Decommissioned and shared deployments are refused.
- Keys are unwrapped bound to `(tenant_id, deployment)`.
- The fleet and platform routers are mounted only on the shared tier.

**What a hostile dedicated host can do**
- It can write only its own deployment row, its own usage for today or yesterday (AA-09), and its own status.
- The answer carries only its own school's announcements, flags, AI allowance and billing summary (platform data).
- Forged usage counts are inherent to self-reported usage (known, docs/16 §11).

**Heartbeat answer on the host (`heartbeat_client.verify_answer`)**
- The host checks the key id, a timestamp within the skew, the constant-time HMAC, a strict `HeartbeatOut`, and that `request_nonce` equals the nonce it just sent (so an old answer cannot be replayed).
- A wrong signature never applies anything.
- The `SOS_HEARTBEAT_ANSWER_UNSIGNED_OK` fallback refreshes announcements only.
- `heartbeat_apply` steps are independent, idempotent and audited.
- Flags are validated by `_valid_key`.
- The allowance goes only through `tenancy.set_ai_answer_allowance` (ADR-0020 B3).
- The client uses `follow_redirects=False`.
- In staging/prod, `SOS_CONTROL_PLANE_URL` must be public https.

**Edge agent (`tally/agent_auth.py`)**
- The canonical string covers method, path, timestamp, nonce and the body hash.
- The device and key are looked up in the school's own `tenant_session` (RLS).
- The secret is unwrapped bound to the device.
- Bytes are compared in constant time.
- The nonce is scoped per school with TTL 600 s ≥ 2×skew.
- Each device and route has its own rate window.
- Enrolment is limited per address and per school, and fails closed when Valkey is down.
- None of the agent routes reads query parameters (the path is signed without the query).

**Webhooks (`public/guards.py`, `fees/webhook.py`, `messaging/webhook.py`)**
- An unregistered provider answers 404.
- The fake verifier is refused in staging/prod.
- The body limit is enforced while streaming.
- Razorpay:
  - the route resolves before any secret is read;
  - the current and previous secrets are tried in constant time;
  - the event id must be well formed.
- The Razorpay event id is not signed, but receipts are issued only after re-fetching the payment, and the provider payment id is unique. A replay under a new event id is therefore a no-op.
- Meta:
  - the app secret HMAC is checked;
  - unknown WABAs are dropped;
  - the replay key is the body hash.
- The replay claim fails closed and is released when storing fails.
- The Meta verify token is compared in constant time; in staging/prod it must be at least 32 characters and not `dev-only`.

**Public handles (`public/tokens.py`, `service.py`)**
- Keys come from the CSPRNG (32 bytes for parent tokens).
- Only SHA-256 hashes are stored.
- A key's shape is checked before any lookup.
- Unknown, expired and revoked handles all answer the same 404.
- Failed lookups are rate-limited per IP hash.

**Config guards (staging/prod)**
- The following are refused:
  - the local-dev key wrapper;
  - `dev-only` DB and service-token secrets;
  - fake KB, email, messaging and payments providers;
  - non-https public origins, control-plane URL and webhook origin;
  - Valkey TLS without certificate checks;
  - shared-tier Postgres without `verify-full`;
  - the placeholder supplier GSTIN or name;
  - turning the rate limiter off.
- The `dev-noop` AV scanner and the fake extraction provider refuse at use time.
- Messaging is refused on dedicated hosts.

**Beat schedule**
- Dedicated hosts run only the heartbeat and the tenant-audit deliver and purge jobs.
- The shared tier runs:
  - billing at 02:00 and 06:00 IST, after the usage collector at 01:30 IST;
  - staleness, announcements, offboarding, incident clocks and the support purge.
- Quota pulls are shared-only.
- Crontab hours are UTC (`timezone="UTC"`), and the IST comments match.

**Usage collector and D78**
- Dedicated tenants stay `provisioning` in the shared `core.tenants` (`_set_status` changes it only for shared schools). The collector therefore never overwrites heartbeat usage with zeros.
- Board sync for dedicated schools is a recorded gap (ADR-0057 §5).

**Two-person rules (`two_person.py`)**
- The approver must be a different operator.
- The request expires after 72 h.
- The requester must still be active and still hold the permission.
- The approver's role must be at least 7 days old.
- Neither operator's qualifying role may have been granted by the other.

**GST and invoices (`billing.compute_tax`, `format_invoice_number`)**
- Tax is computed per rate group with ROUND_HALF_UP.
- Intra-state CGST and SGST are equal halves.
- A negative total is refused.
- Invoice numbers are capped at 16 characters (CGST Rule 46).
- Overlapping live invoices block a new draft.

**SSRF**
- Every outbound httpx client has a fixed, configured base URL with `follow_redirects=False`:
  - Razorpay;
  - Meta Graph;
  - Vertex AI and Voyage;
  - JWKS;
  - the heartbeat.
- Ids that schools supply are pattern-checked before they go into a path (`_id(..., "pay_")`, `MetaId`).
- No school or operator controls a host.

## Schema and API changes

None. No migration, and no OpenAPI change.

## Checks run

- `uv run pytest apps/api/tests/core/test_config.py`: 49 passed (CP-01 failed before the fix).
- `ruff check` and `ruff format --check` on the touched files: clean.
- `uv run mypy apps/api apps/worker evals`: no issues in 1241 files.
- `uv run lint-imports`: 52 contracts kept.

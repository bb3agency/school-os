# ADR-0047: Routing public requests and webhooks to a school (public handles and one definer function)

| Field | Value |
|---|---|
| Status | **Accepted** 2026-10-14 (owner decision D12, docs/18 §8). Adds a `SECURITY DEFINER` function and a `definer_access` policy, which CLAUDE.md §11 allows only with an ADR |
| Date | 2026-10-14 |
| Deciders | Engineering (proposal); product owner (approved 2026-10-14) |
| Amends / supersedes | Amends [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (adds one allowlisted definer function and one `definer_access` table) |

## Context

R2 adds requests that arrive **without a signed-in member and without a school**:

- a parent opening `/p/{token}` (ADR-0046) and a verifier opening `/v/{code}` (ADR-0045);
- Razorpay webhooks for one school's account at `/api/v1/webhooks/razorpay/{route}` (ADR-0042);
- Meta webhooks for every school at `/api/v1/webhooks/meta`, carrying a WABA id (ADR-0043).

Each must find **one** school, then do all further work inside that school's `tenant_session` under RLS (invariant 1). The Tally agent solved this with an `X-SOS-Tenant` header (docs/07 TB9), but a public URL must not reveal the school (enumeration, ADR-0045 decision 7), and providers' webhooks cannot carry our header. Every tenant table is RLS-protected; without tenant context `sos_app` sees nothing.

## Decision

1. **A public handle table in `core`.** `core.public_handles` (tenant-owned: `tenant_id` first, standard RLS ENABLE + FORCE, `UNIQUE (tenant_id, id)`): `kind` (`cert_verify`, `parent_notice`, `parent_receipt`, `parent_dues`, `parent_optin`, `razorpay_route`, `meta_waba`), `key_hash bytea` (SHA-256 of the token, code, route or provider id; 32 bytes), `target_type`, `target_id`, optional `guardian_id` (composite FK, `ON DELETE SET NULL`), `expires_at` (NULL for provider routes and certificate codes), `revoked_at` / `revoked_reason`, `created_by`, `created_at`, `first_viewed_at`, `view_count`, `last_viewed_at`. `UNIQUE (kind, key_hash)` across all schools (a collision of 256-bit hashes of 128+-bit random values is not a practical concern; the insert fails closed). Written only by `app/public/service.py` inside the school's own `tenant_session`, so a school can never create a handle for another school.
2. **One definer function, returning the school only.** `core.resolve_public_handle(p_kind text, p_key_hash bytea) RETURNS TABLE (tenant_id uuid, handle_id uuid)`, owned by `sos_definer`, `SECURITY DEFINER`, `search_path = pg_catalog, pg_temp`, `EXECUTE` granted to `sos_app` only. It returns a row only when the handle exists, is not revoked, has not expired, and its school is `active` (or, for `parent_receipt` and `cert_verify`, `active` or `suspended`: a parent's receipt and a certificate's validity do not depend on the school paying SchoolOS); otherwise no row. It reads `core.public_handles` and `core.tenants` (both carry the `definer_access` policy; `core.tenants` already does). Every caller then opens `tenant_session(tenant_id)` and re-reads the handle and its target under RLS.
3. **Handles for provider routing.** On connecting Razorpay, the service creates a `razorpay_route` handle with a random 24-character route value used in the webhook URL; on connecting WhatsApp, a `meta_waba` handle whose key is the SHA-256 of the WABA id. Disconnecting revokes them.
4. **Guards, not ad-hoc code.** Public and webhook routes use only these dependencies, each pinned by the route-enumeration test to the paths listed in `app/public/public_routes.yaml` (the "public allowlist", next to the health checks): `require_public_handle(kind)` (parent and verification pages, called by the BFF with its service token), `require_webhook_signature("razorpay" | "meta")` and `require_meta_verify_token()` (provider calls straight to the API on `/api/v1/webhooks/*`). Each guard applies the rate limits of docs/09 §2.7 before resolving anything.
5. **Fail closed and look the same.** Unknown, expired, revoked and suspended-school handles produce the same 404 body and similar timing (the resolver runs one indexed lookup in every case); webhook signature failures answer 401 with no detail; a resolver error is a 503, never a guess.
6. **Tests.** The definer allowlist test gains the function and table; a catalog test checks the function's return columns (exactly two), grants and `search_path`; isolation tests show a handle of school A never opens school B's data and that a school cannot insert a handle for another school.

## Consequences

- Good: no school identifier in any public URL; one narrow, reviewed cross-tenant path, like `core.resolve_login`.
- Bad / costs: one more definer function and `definer_access` table to keep pinned; handle rows must be purged (expired parent handles 30 days after expiry) and included in offboarding.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| School id or code in the URL | Enumerates schools and narrows guessing; the owner asked for no tenant enumeration |
| A global table without `tenant_id`, readable and writable by `sos_app` | A bug could write a handle pointing into another school; RLS on a tenant-owned table prevents that |
| Encrypt the tenant id into the token | Ties token format to key management; revocation still needs a table |
| A separate Meta app per school | Each school would need App Review; impossible at scale |

## Related requirements

FR-PUB-001..012, SEC-036, SEC-039; docs/05 §3.3, §3.4, §15.5; 07 §6.6, §7, §11.2; 09 §4 "Public", "Webhooks"; ADR-0042, ADR-0043, ADR-0045, ADR-0046.

## Follow-up (2026-10-17, wave R2-0 built)

Built as decided in migration `0055_public_handles` and `app/public/` (the number moved from 0054 because `0054_r1_finish` landed first). Refinements: the app role may delete only handles expired more than 30 days (restrictive policy `purge_expired_only`), so the purge needs no other role; an update trigger keeps revocations final; parent tokens are resolved for all four parent kinds in one statement (`unnest` + `LATERAL` over the same function, still two return columns); the guards carry pseudo-permissions `public.handle`, `public.webhook`, `public.meta_verify` and are pinned to `public_routes.yaml` by the route-enumeration test; a failures-only budget is checked before the lookup (`ratelimit.ensure_room`) so a used-up budget also refuses valid keys. Verifiers for Razorpay and Meta register with `app.public.webhooks.register_webhook_verifier` in waves R2-2D and R2-1C.

## Owner decision (2026-10-18): STOP from parents of suspended schools

Wave R2-1C proposed amending decision 2 so that `meta_waba` handles of suspended schools resolve and a parent's STOP is applied during the suspension. The owner declined: **decision 2 stands**. A suspended school's webhooks get the uniform answer, Meta retries delivery for up to 7 days, and an opt-out sent during the suspension is applied only when the school is reactivated within that window (docs/18 §8 D31). SchoolOS sends nothing to parents of a suspended school in any case.

## Lead decision (2026-10-21): STOP recorded while suspended (D35)

D35 amends the owner decision above for one case. `core.resolve_public_handle` now also resolves `meta_waba` handles while the school is `suspended` (migration `0063_r2_closeout`; same signature, owner `sos_definer` and `sos_app`-only grant; an offboarding or deleted school still resolves nothing), so the Meta webhook can reach a suspended school. The messaging service then records **only** inbound STOP replies, silently, and drops every other event; nothing is sent (ADR-0043 follow-up 2026-10-21). Receipts and certificate codes keep resolving while suspended as before; parent notice, dues and opt-in links still do not.

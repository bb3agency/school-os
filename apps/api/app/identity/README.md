# identity: API-side authentication

This module answers one question: who is calling? Tenant membership, roles and scopes live in
`authz`. Requirements covered: FR-IAM-001, FR-IAM-002 (operators), FR-IAM-003 (step-up
input), FR-IAM-004 (token lifetime), SEC-004, SEC-005. Threat T2 in docs/07 §4 (forged or
replayed tokens) and trust boundary TB2 in docs/07 §3.

| File | Purpose |
|---|---|
| `tokens.py` | `TokenVerifier`: verifies OIDC access tokens (JWKS cache, algorithm allowlist, issuer/audience, lifetime), returns a frozen `VerifiedToken`. Two verifiers: tenant users (`SOS_OIDC_*`) and platform operators (`SOS_PLATFORM_OIDC_*`). |
| `service_token.py` | BFF → API service token (`X-Service-Token`, HS256, `iss=sos-web`, `aud=sos-api`, ttl ≤ 60 s, `jti` replay store). |
| `principal.py` | FastAPI dependencies `get_principal`, `get_operator_principal`, `recent_auth()`; `require_recent_auth()` (428 step-up); `PrincipalResolver` protocol for `authz`. |

## Request authentication (TB2)

Each API request must carry **both** headers:

```
X-Service-Token: <HS256 JWT minted by the BFF for this one request>
Authorization:   Bearer <user's OIDC access token>
```

1. Either header missing, or the Authorization header is not `Bearer <token>`: **401** `unauthenticated`.
2. The service token is checked first (HMAC, no network), so junk never reaches the JWKS cache.
   It is refused if the signature, `alg` (HS256 only), `iss`, `aud` (strict), `exp`/`iat`
   (5 s leeway; lifetime ≤ 60 s) or `jti` format is wrong, or if the `jti` was already used.
3. Access token checks (`TokenVerifier.verify`):
   - size ≤ 8 KiB; header `alg` ∈ {RS256, ES256}; `kid` required; `typ` absent, `JWT` or
     `at+jwt` (RFC 9068). Checked **before** any JWKS lookup.
   - The JWK for `kid` must declare or imply the same algorithm (no key confusion). Symmetric
     (`oct`), `use=enc`, RSA < 2048-bit and non-allowlisted keys in the JWKS are ignored.
   - Signature, then `exp`, `iat`, `iss`, `sub` required; `iss` must equal the configured
     issuer exactly; 30 s leeway; `nbf` honoured; `iat` in the future refused.
   - Audience: `aud` (string or list) must contain the configured audience, **or**, for Amazon
     Cognito access tokens that have no `aud`, `client_id` must equal it **and**
     `token_use == "access"`. Any token with `token_use` other than `access` (Cognito ID
     tokens) is refused.
   - `exp - iat` > 15 min is refused as an IdP misconfiguration. FR-IAM-004 requires ≤ 10 min.
   - `auth_time` (optional) must be numeric and not in the future.
   - Result: `subject`, `issuer`, `issued_at`, `expires_at`, `auth_time`, `mfa`,
     `session_id` (`sid`, or Cognito's `origin_jti`), `token_id` (`jti`), plus the claims as a
     read-only mapping that is left out of `repr`.
4. Expired access token: **401** with `code: token_expired`, so the BFF knows to refresh.
   Every other rejection: **401** `unauthenticated` with a generic message. The token is never
   echoed back.
5. IdP or JWKS unreachable, non-200, not JSON, over 64 KiB, no usable keys, or discovery
   `issuer` mismatch: **503** `service_unavailable`, with no upstream detail. Operators see
   `jwks_fetch_failed` in the logs (issuer host only).

### JWKS cache (docs/04 §10)

- The JWKS URI comes from OIDC discovery (`{issuer}/.well-known/openid-configuration`). The
  document's `issuer` must equal the configured issuer (OIDC Discovery 1.0 §4.3). An explicit
  `jwks_uri` can be passed instead (see open item 2).
- The key set is kept in process for **1 h**. An unknown `kid` triggers a refetch, but at most
  **once per 60 s** per issuer, whatever the `kid`. A flood of random `kid` values therefore
  costs at most one IdP request a minute.
- Stale-if-error: if the hourly refresh fails, the last good key set is served for up to 1 h
  more. Retries are spaced 60 s apart, or 5 s before the first successful fetch.
- The lock is held across the fetch, which has explicit httpx timeouts (connect 2 s, read 3 s)
  and no redirects. Concurrent requests wait for that one fetch instead of stampeding the IdP.
- We do not use PyJWT's `PyJWKClient`. It fetches with `urllib` (the stack standard is httpx,
  which is testable with `MockTransport` and instrumented by OpenTelemetry), and its cooldown
  starts on every successful fetch. We use `jwt.PyJWK` only to parse keys.

### Dev issuer guard

When `settings.is_production_like` (staging/prod) is true, `TokenVerifier` raises `ValueError`
at construction if the issuer (or an explicit JWKS URI) is not `https`, or if its host is
`localhost`, `*.localhost`, `127.*`, `::1`, `0.0.0.0`, `mock-oauth2-server` or `oidc`.
`SOS_ENV=prod` with the dev stub issuer therefore fails at startup instead of trusting it.

### Service token replay store

The `jti` is remembered for 120 s. That is at least the token's maximum validity
(60 s + 2 × 5 s leeway), so a token can never outlive its replay record.
- local/CI: `InMemoryReplayStore`, which is per process, bounded, and fails closed with 503
  when full.
- staging/prod: `RedisReplayStore` (Valkey), `SET sos:svc-jti:<jti> 1 NX EX 120`. Every API
  task shares it. A Valkey outage returns 503 (fails closed).
- Only fully valid tokens reach the store, so forged tokens cannot burn a legitimate `jti`.

## Step-up (SEC-005, docs/07 §5.2)

`require_recent_auth(principal, max_age=5 min)` raises `StepUpRequired` (**428**
`step_up_required`) unless `principal.mfa` is true **and** `auth_time` is at most 5 min old.
An `auth_time` more than 30 s in the future also fails. `authz.require(..., step_up=True)`
should call it. `Depends(recent_auth())` is the standalone form. The BFF handles 428 by
re-running Authorization Code + PKCE with `prompt=login`. Cognito managed login supports this
(below), and it produces a fresh `auth_time`.

## Principal resolution (for `authz`)

`principal.py` stops at a verified `Principal(subject, issuer, kind, auth_time, mfa, session_id,
expires_at)`. `authz` implements `PrincipalResolver[UserContext]`:

1. `core.resolve_login(principal.subject)`: the SECURITY DEFINER function (ADR-0013) returns the
   subject's user id and active memberships across tenants (minimal columns).
2. Pick the tenant from `tenant_hint` (the BFF's active tenant; `POST /me/active-tenant`) or
   the only membership. With no membership, or the hinted tenant not among them: 401/403.
   Never reveal which tenants exist.
3. Reject suspended tenants and deactivated users (docs/04 §5 step 3).
4. Load roles, permissions and scopes inside `core.db.tenant_session(tenant_id, user_id)`.
5. FR-IAM-002: if the membership holds `owner`, `principal` or `office_admin` and
   `principal.mfa` is false, refuse with 403 `mfa_required` (the BFF sends the user to MFA
   enrolment). This check belongs in authz because only authz knows the roles.
6. Audit `auth.login` on the first request of a new `session_id` (invariant 7).

`get_operator_principal` already requires `mfa` for every platform operator (contract §5:
"MFA mandatory for every operator"), and refuses without it with 403 `mfa_required`.

## Research: Amazon Cognito facts (checked 2026-09-26)

AWS documentation hosts (`docs.aws.amazon.com`, `repost.aws`) were blocked by this environment's
egress proxy. The facts below come from search-result extracts of those pages, the
`awsdocs/amazon-cognito-developer-guide` GitHub mirror and third-party write-ups. Verify each
item marked **(verify)** in the staging pool before go-live.

1. **Access tokens have no `aud`.** They carry `client_id` (the app client id) and
   `token_use: "access"`. ID tokens carry `aud` = the app client id and `token_use: "id"`. So
   `aud` alone cannot tell a Cognito ID token from an access token, and we check `token_use`.
   Sample access-token claims: `sub`, `cognito:groups`, `token_use`, `scope`, `auth_time`,
   `iss` (`https://cognito-idp.<region>.amazonaws.com/<poolId>`), `exp`, `iat`, `origin_jti`,
   `jti`, `client_id`, `username`. There is no `sid`. `origin_jti` stays the same for one
   sign-in across refreshes, so we use it as the session id.
   - https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-access-token.html
   - https://github.com/awsdocs/amazon-cognito-developer-guide/blob/main/doc_source/amazon-cognito-user-pools-using-the-access-token.md
   - https://repost.aws/questions/QU4wv0qKIMSk2O64Wk5P4jdg/why-do-cognito-access-tokens-not-have-an-audience-claim
   - https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-id-token.html
2. **`auth_time`** is present in access tokens: the Unix time the user completed
   authentication. **(verify)** that it keeps the original sign-in time after a refresh-token
   grant. Step-up depends on it. The public docs we could reach do not state it explicitly.
3. **No usable MFA signal in tokens.** Cognito user-pool tokens have no `amr` for native
   sign-in. Cognito reserves `amr`: a pre-token-generation trigger cannot add, change or remove
   it. The trigger event (V2_0/V3_0) carries `userAttributes`, `groupConfiguration`, `scopes`
   and `clientMetadata`, but **no authentication-method or challenge-result field**. So the
   trigger cannot see whether *this* sign-in used MFA, only whether the user has MFA set up.
   - https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-token-generation.html
   - https://github.com/dmeiser/kernelworx/pull/409 (documents the reserved-`amr` restriction and the missing auth-method field, found empirically)
4. **Access-token customisation** (adding claims to the access token, trigger event V2_0 or
   later) needs the **Essentials or Plus** feature plan.
   - https://aws.amazon.com/blogs/security/how-to-customize-access-tokens-in-amazon-cognito-user-pools/
   - https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-token-generation.html
5. **MFA configuration is per user pool:** `OFF`, `OPTIONAL` (only users who have set up MFA
   are challenged, at every sign-in) or `ON` (every user). There is no per-group or per-role
   MFA requirement. Passkeys count as MFA when `WebAuthnConfiguration.FactorConfiguration =
   MULTI_FACTOR_WITH_USER_VERIFICATION`.
   - https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa.html
   - https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_SetUserPoolMfaConfig.html
   - https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_WebAuthnMfaSettingsType.html
6. **Re-authentication:** since 2025-05-16, Cognito **managed login** supports the OIDC
   `prompt` parameter: `prompt=login` forces re-authentication even when a session exists, and
   `prompt=none` does a silent check. It needs the Essentials or Plus plan. We found no
   documentation that `max_age` is honoured, so **(verify)** before relying on it and use
   `prompt=login`.
   - https://aws.amazon.com/about-aws/whats-new/2025/05/amazon-cognito-oidc-prompt-parameter
   - https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html
7. **Token lifetimes:** access-token validity is configurable per app client from 5 min to
   1 day (default 1 h). **Set it to 10 min or less** (FR-IAM-004). This verifier refuses
   anything over 15 min, so the 1 h default fails closed. Refresh-token rotation exists
   (`RefreshTokenRotation`, with a grace period of up to 60 s for the old token).
   - https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_UserPoolClientType.html
   - https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_RefreshTokenRotationType.html
8. **JWKS**: `https://cognito-idp.<region>.amazonaws.com/<poolId>/.well-known/jwks.json`,
   advertised by the discovery document. Keys are RS256.
9. **PyJWT 2.15.0** (locked): `jwt.decode(..., algorithms=[...], issuer=..., leeway=...,
   options={"require": [...], "verify_aud": False, "strict_aud": ...})`. `jwt.PyJWK(dict)`
   exposes `.key` and `.algorithm_name`. `PyJWKClient(uri, cache_jwk_set, lifespan, timeout,
   cooldown_duration)` fetches with urllib (checked in the installed source).

## Recommendation for open item B22 (MFA per role and step-up on Cognito)

Cognito cannot require MFA per role, and it does not report whether a given sign-in used MFA.
Our design:

1. **Two user pools.** The *platform operator* pool has MFA **ON** (TOTP or passkey with user
   verification). The *school staff* pool has MFA **OPTIONAL**. Both pools use the Essentials
   plan (needed for access-token customisation and `prompt=login`).
2. **Pre-token-generation trigger (V2_0)** on both pools adds the access-token claim
   `sos:mfa = "true"` when the user has an MFA factor enabled (`UserMFASettingList`
   non-empty, or a registered passkey with the MULTI_FACTOR_WITH_USER_VERIFICATION setting),
   and `"false"` otherwise. It runs on sign-in and on refresh. With MFA OPTIONAL or ON, a user
   with MFA enabled is challenged at **every** sign-in, so "MFA enabled" implies "this sign-in
   used MFA" **provided that**:
   - **device remembering is off** (remembered devices can suppress the MFA challenge), and
   - adaptive authentication is not set to skip MFA for low-risk sign-ins, and
   - federated (SAML/OIDC) users always get `false` (Cognito does not challenge them for MFA).
   `MfaClaimPolicy` also accepts RFC 8176 `amr` containing `mfa`, so a future IdP that emits
   `amr` works without code changes.
3. **FR-IAM-002 enforcement in the API**, independent of IdP configuration: `authz` refuses
   memberships with `owner` / `principal` / `office_admin` when `mfa` is false (403
   `mfa_required`). `get_operator_principal` refuses every operator without MFA. When an owner
   assigns a privileged role to someone without MFA, school admin UI should show "Ask them to
   set up MFA first".
4. **Step-up**: the API returns 428. The BFF redirects to `/oauth2/authorize?...&prompt=login`,
   then the API checks `auth_time` ≤ 5 min and `mfa`. Because MFA-enabled users are challenged
   on every sign-in, a fresh `auth_time` from such a user means a fresh MFA.
5. **Alternative considered:** a single pool with MFA **ON** for everyone. It is simpler and
   removes the "enabled means used" inference (T1 residual risk says "encourage MFA for all").
   We rejected it for M0 because many office staff share PCs and do not have smartphones.
   Revisit it once email OTP or passkeys on shared PCs have been assessed. It needs no API
   change: the claim would then be `true` for everyone.

This should become an ADR (proposed: ADR-0018 "MFA signal and step-up with Cognito"). The
module owner should raise it with the lead.

## Open items

1. **Dev OIDC stub** (`infra/docker/oidc.json`, lead-owned): mock-oauth2-server issues
   1 h tokens by default, which this verifier refuses (> 15 min). It needs `tokenCallbacks`
   for issuers `schoolos` and `platform` with `tokenExpiry: 600`, `aud` = the client id, and
   `amr: ["pwd","mfa"]` (or `sos:mfa: "true"`) for operators.
2. **JWKS reachability in compose:** the issuer is `http://localhost:8080/...` (as the browser
   sees it). Inside the api container, `localhost:8080` is not the stub. Proposal: settings
   `SOS_OIDC_JWKS_URI` / `SOS_PLATFORM_OIDC_JWKS_URI` (optional; `TokenVerifier(jwks_uri=...)`
   already supports it), or run the stub with a hostname both sides resolve.
3. **(verify)** Cognito `auth_time` after refresh, and whether `max_age` is honoured (above).
4. Consider binding the service token to the access token (for example an `ath` claim =
   base64url(SHA-256(access token)), as in DPoP). That would stop a leaked service token being
   paired with a different access token within its 60 s life. It needs a contract change on
   the web side.

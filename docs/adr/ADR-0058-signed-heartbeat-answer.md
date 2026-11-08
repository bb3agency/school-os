# ADR-0058: The heartbeat answer is signed and carries flags, the AI answer allowance and a billing summary

| Field | Value |
|---|---|
| Status | Accepted (lead, 2026-11-08; owner may revisit; docs/14 D79) |
| Date | 2026-11-08 |
| Deciders | Lead engineer |
| Amends / supersedes | None. Extends the heartbeat of [ADR-0015](ADR-0015-deployment-and-commercial-model.md) (dedicated tier) within the boundaries of [ADR-0020](ADR-0020-control-plane-boundaries-and-guaranteed-audit-copies.md) (amendment B3: the allowance is the one number the school receives); closes docs/16 §19 Q5, Q7 and Q19 and audit finding W3-05 |

## Context

A dedicated host posts a heartbeat to the control plane every 5 minutes, HMAC-signed with its per-deployment key (`require_fleet_signature`, docs/16 §12, SEC-028). Until now the **answer** (announcements shown to school users) was not signed: only TLS authenticated the control plane to the host (W3-05, `docs/security/audit-2026-10-04-web-ai-infra.md`). Three things a dedicated school needs from the control plane had no path at all:

- **Q5** feature flags: the host's local `platform.feature_flags` cannot be written by the control plane (`sos_app` may only read that table);
- **Q7** the Plan and billing page: the host has no subscription rows, so `GET /tenant/billing` answered `available: false`;
- **Q19** the AI answer allowance: the school's AI budget follows its bundle (ADR-0038 C1, ADR-0020 B3), but `tenancy.set_ai_answer_allowance` is called only for shared-tier schools.

The heartbeat is outbound only (the host never accepts inbound calls from the control plane), and it already runs every 5 minutes with the deployment's key on both sides, so its answer is the natural carrier.

## Decision

1. **Signed answer.** The control plane MUST sign the heartbeat answer with the key that verified the request: headers `X-SOS-Key-Id`, `X-SOS-Timestamp` and `X-SOS-Signature = "v1=" + hex(HMAC-SHA256(key, timestamp + "." + raw_body))`, exactly as for the request. The answer MUST echo the request's nonce (`request_nonce`).
2. **Host verification.** The host MUST refuse an answer that is unsigned, has another key id, a timestamp outside the fleet skew (300 s), a wrong signature (constant-time compare), an invalid body or another `request_nonce`. A refused answer applies nothing (announcements included) and the last good state stays; the host logs `fleet.heartbeat.answer_rejected` with a reason code only (never the key, signature or body).
3. **Content.** A signed answer carries, for that one school and from platform rows only (never student data): the announcements (as before); `feature_flags`, the effective value of every flag the school is subject to (globals with their rollout and the school's overrides, evaluated for it); `ai_allowance.included_answers` of the live subscription's bundle (`null`: no bundle); and `billing`, a summary of the live subscription (plan code, name, tier and period, status, current period, trial, past-due and grace dates, cancel at period end, amount due, next invoice date and an estimate of its total including GST; `null`: no live subscription).
4. **Applying it on the host** (`app/platform/heartbeat_apply.py`), three independent steps, each idempotent and keeping the last good state when it fails:
   - flags become the school's overrides in the local `platform.feature_flags`, written as `sos_platform` through `platform.flags` (`apply_deployment_flags`); overrides whose key is no longer sent are removed; each change is audited (`flag.override_set` / `flag.override_removed`, actor `system`, `source: heartbeat`) on the host's platform chain. `sos_app` keeps read-only access;
   - the allowance goes through `tenancy.service.set_ai_answer_allowance` (unchanged ADR-0020 B3 call; audited `tenant.ai_allowance_set` on the school chain only when it changes);
   - the billing summary is stored in the host's Valkey (no expiry, replaced by each verified answer) and `GET /tenant/billing` shows it on a dedicated host with `summary_as_of`.
5. **One-release compatibility.** `SOS_HEARTBEAT_ANSWER_UNSIGNED_OK` (default `false`) lets a host accept an **unsigned** answer for announcements only, for the release in which a control plane may not sign yet; a wrong signature is never accepted. It MUST be removed in the release after 2026.11. Old hosts ignore the new answer fields and headers.

## Consequences

- Good: W3-05 closed: a network attacker who breaks or bypasses TLS can no longer show school users forged announcements or change a host's flags, budget or billing page. Q5, Q7 and Q19 answered with no new inbound path, no new tenant-side call and no migration.
- Good: the deploy pipeline no longer needs to write the flag set on each release (the docs/16 Q5 interim plan, never built).
- Bad / costs: flags, allowance and billing summary reach a dedicated host up to 5 minutes late, and not at all while the host cannot reach the control plane (it keeps the last good state). The next-invoice amount is an estimate (per-student lines and last month's AI overage are added when the invoice is drafted). The billing summary lives in Valkey (append-only file on dedicated hosts); if it is lost the page shows "not available" until the next heartbeat.
- Follow-up work: remove `SOS_HEARTBEAT_ANSWER_UNSIGNED_OK` in the next release; support tickets from dedicated hosts (rest of Q7) still go by email or phone.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep TLS only | W3-05: TLS termination or a mis-issued certificate would let anyone drive the host's flags and banners |
| A separate pull endpoint for flags and billing | A second machine-auth route and schedule for data the heartbeat answer can carry |
| Deploy pipeline writes flags on each release | Flags change between releases; the allowance and billing summary would still have no path |
| Asymmetric signature (control-plane key pair) | A second key to distribute and rotate; the per-deployment HMAC key is already shared, rotated with overlap and bound to the deployment |
| Store the billing summary in a new table | Needs a migration; the summary is a cache of control-plane data refreshed every 5 minutes |

## Related requirements

SEC-028, SEC-009, FR-PLT-022, FR-PLT-024, FR-PLT-030, FR-KB-011; docs/16 §12, §19 Q5, Q7, Q19; docs/10 §11; docs/07 (fleet controls); W3-05.

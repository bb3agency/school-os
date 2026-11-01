# Pilot readiness checklist

Status of every item of the pilot-ready gate ([docs/14 §3](../14-roadmap.md#3-pilot-ready-gate-before-any-real-student-data)) as of 2026-11-01, with the evidence that exists in the repository and what the owner still has to do. No real student data may enter SchoolOS until every row is closed (CLAUDE.md invariant 11).

Statuses:

- **Done in code**: built and covered by tests or a command; nothing left for the owner except to keep it green.
- **Ready, needs owner**: the repository has what is needed; a person (owner, CA, school) must decide, sign or run it.
- **Needs AWS-ops**: needs a cloud account (AWS, or the Google Cloud project for Vertex AI) and someone with operator access to run it.
- **Not started**: nothing in the repository yet.

## Summary

| Status | Rows |
|---|---|
| Done in code | 3 |
| Ready, needs owner | 10 |
| Needs AWS-ops | 4 |
| Not started | 2 |
| **Total** | **19** |

## Checklist

| # | Gate item (docs/14 §3) | What it needs | Evidence in the repository now | Status | Owner action |
|---|---|---|---|---|---|
| 1 | SEC-001..017, SEC-021..024 and SEC-026..029 implemented and verified | Each control traced to code and a passing test | [pilot-gate-sec-traceability.md](pilot-gate-sec-traceability.md) (being written in the same wave); `make test-security` (RLS catalog, isolation, route enumeration, authz matrix, BOLA); security audits in this folder (2026-10-04 to 2026-10-17) | Done in code | Read the traceability file when it lands; any control it marks open becomes its own row here |
| 2 | Restore drill completed within RPO/RTO; results recorded | Restore a backup into a scratch environment, time it against RPO/RTO, verify the audit chain, record the result | Runbooks in [docs/11](../11-observability-and-operations.md) (R11, quarterly drills); drill record in `docs/security/drills/` (being written in the same wave) | Needs AWS-ops | Run the drill on the staging/prod backups (RDS snapshot and ap-south-2 copy) and file the timed result |
| 3a | Incident runbook rehearsed, including the CERT-In 6-hour flow | A tabletop drill against the runbook, with timings | [docs/legal/breach-runbook.md](../legal/breach-runbook.md) v0.1; incident records in the control plane (ADR-0050); drill script [incident-drill.md](incident-drill.md) (being written in the same wave) | Ready, needs owner | Hold the tabletop with the second platform owner; record the time to the CERT-In report draft |
| 3b | CERT-In point of contact designated | A named person and contact details reported to CERT-In | Runbook names the role, not the person | Ready, needs owner | Designate the point of contact and send the details to CERT-In |
| 4a | DPA signed with the school | Signed data processing agreement | [docs/legal/dpa-template.md](../legal/dpa-template.md) v0.1 (not legal advice, owner decision D30) | Ready, needs owner | Fill the placeholders, optional one-time legal review, sign with the pilot school |
| 4b | School's parent and staff notices issued | The school issues its notices before data is entered | [privacy-notice-parents-template.md](../legal/privacy-notice-parents-template.md) and [privacy-notice-staff-template.md](../legal/privacy-notice-staff-template.md) v0.1; the app records which notice version was issued (FR-DPDP-003) | Ready, needs owner | Give the school the templates; the school records the issue in the app |
| 4c | DPIA completed | A completed DPIA for the pilot school | [docs/legal/dpia-template.md](../legal/dpia-template.md) v0.1 | Ready, needs owner | Complete it with the school |
| 5a | Sub-processor list shared | List of sub-processors given to the school | Sub-processor register in [docs/08 §1](../08-privacy-and-compliance.md) (Google Cloud Vertex AI in asia-south1; Anthropic only while a fallback is configured; no role uses Anthropic today, `make eval-live-preflight` prints this) | Ready, needs owner | Share the register with the school with the DPA |
| 5b | Vertex AI Zero Data Retention set up and confirmed with `SOS_LLM_ZDR_CONFIRMED` | Project caching disabled, no request-response logging, abuse-monitoring exception requested and answered; then the flag set | Start-up guard in `apps/api/app/core/config.py` (`_guard_knowledge`: staging/prod refuse `SOS_KB_ENABLED` without it); steps in [docs/10 §11.1](../10-infrastructure-and-devops.md); `make eval-live-preflight` checks the flag | Needs AWS-ops | Do the docs/10 §11.1 steps on the Vertex projects, wait for Google's answer on abuse monitoring, then set `SOS_LLM_ZDR_CONFIRMED=true` |
| 6 | Live model evaluation per role passed on Vertex AI, including the English-first gates | `make eval-live EVAL_SUITE=full` passing every hard and soft gate per role (docs/06 §13.7, §13.8) | Offline: `make eval` passes all 36 gates with `stub-perfect` and with `EVAL_ADAPTER=app-fake` (2026-11-01). Preflight: `make eval-live-preflight` (`apps/api/app/knowledge/live_preflight.py`, tests in `apps/api/tests/knowledge/test_live_preflight.py`) lists what is missing without any network call | Ready, needs owner | Provide a non-production Vertex project and service identity, run the preflight until it says READY, then run `make eval-live EVAL_SUITE=full` twice per candidate and record the chosen models in docs/14 and `models.yaml` |
| 7a | MFA on all operator access (operator pool MFA ON) | Cognito operator pool with MFA required | ADR-0018; `infra/terraform/modules/cognito/main.tf` (`mfa_configuration`); `infra/terraform/envs/prod/tests/prod.tftest.hcl` asserts the platform pool is `ON`; step-up permissions pinned by `apps/api/tests/platform/test_authz_matrix.py` | Done in code | Apply the prod Terraform (row 7b) and confirm every operator enrolled a TOTP device |
| 7b | Production accounts set up | Prod Terraform applied, operator accounts created | `infra/terraform/envs/prod` | Needs AWS-ops | Apply prod; create operator accounts through the bootstrap path |
| 7c | Break-glass tested | A support-access request approved, used and seen by the school, in production | `apps/api/app/breakglass/`; `apps/api/tests/breakglass/` (approval, visibility to the school, support sign-in) | Needs AWS-ops | Run one break-glass request end to end in prod against a synthetic school, then purge it (row 9b) |
| 7d | At least two active platform owners (two-person rules, docs/16 §19 Q1) | A second `platform_owner` | Two-person rules in `apps/api/app/platform/roles.yaml` (`two_person`, 7-day minimum role age); `apps/api/tests/platform/test_two_person_requests.py`; first owner from `python -m app.platform.bootstrap_owner` | Ready, needs owner | Appoint the second owner at least 7 days before it is needed (role age rule) |
| 8 | Invoice number format and GST registration confirmed with a CA (docs/16 §19 Q2-Q3) | CA confirmation of `SOS/26-27/000123`, GST registration and SAC, invoice wording | `apps/api/app/platform/billing.yaml` (`invoice_number_format`, `default_gst_rate`); invoice PDF v0 pending CA review (Q11); `apps/api/tests/platform/test_invoice_pdf*.py` | Ready, needs owner | Take the format, the v0 invoice PDF and the GST question to a CA; change `billing.yaml` only after the answer |
| 9a | Data-handling permission for imports and photos documented | Written permission from the school to import its lists and photograph its registers (BRD assumption A1) | Mentioned in docs/01 (A1) only; no permission template | Not started | Write a one-page permission letter (or a DPA annex) and get it signed with the DPA |
| 9b | Synthetic data purged from prod | Proof that no school, staff member, student or document made by `make seed-synthetic` exists in prod | `make synthetic-check` (`apps/api/app/devtools/synthetic_check.py`, read only; exit 1 with "synthetic data present: purge before real data" in staging/prod); tests in `apps/api/tests/devtools/test_synthetic_check.py`; the generator refuses to run outside local/ci (`apps/api/tests/devtools/test_seed_synthetic.py`) | Done in code | Run `make synthetic-check` in prod (as `sos_app`) before the first real import and keep the output; purge through offboarding if it fails |
| 10a | Staff training done (English; Telugu deferred, ADR-0036) | Training material and a session for the pilot school's office | None in the repository | Not started | Prepare a short English guide for onboarding, checks and Ask the school; run the session |
| 10b | Support channel and escalation path defined | A named channel, hours and escalation | In-app support tickets (docs/16 §15, Q6 settled); channels described in [docs/11](../11-observability-and-operations.md) (support WhatsApp number or email in school hours, phone for P1/P2) | Ready, needs owner | Choose the support number and email, publish the hours and the escalation contacts to the school |

## How to run the two checks

```bash
# Offline, no network and no model call. Exit 0 READY, 1 lists what is missing.
make eval-live-preflight

# Read only. Uses SOS_DATABASE_URL (or ARGS="--database-url ..."); ARGS="--json" for a record.
# Exit 0 clean (or synthetic data in local/ci), 1 synthetic data in staging/prod, 3 cannot read.
make synthetic-check
```

The preflight prints only the names of environment variables, never their values. The synthetic-data check prints school codes, ids and counts, never names of people.

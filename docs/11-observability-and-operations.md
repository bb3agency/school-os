# 11 · Observability & Operations

| Field | Value |
|---|---|
| Version | 0.1 · 2026-09-26 |
| Telemetry | OpenTelemetry SDKs → collector → CloudWatch Logs/Metrics + X-Ray (swap-able for Grafana stack) |
| Related | 07-Security §15, 08-Privacy §6, 10-Infrastructure |

---

## 1. Principles

- Observe systems, not people: telemetry carries IDs and technical data, never names, DOBs, phone numbers, questions or answers.
- Every signal is tagged with `tenant_id`, `env`, `service`, `version` so issues can be scoped to one school.
- Alerts are actionable and few; each links to a runbook.

## 2. Logging

**Format:** JSON, one event per line, UTC timestamps, fields from an allowlist.

```json
{"ts":"2026-09-26T09:12:03.412Z","level":"INFO","service":"api","version":"2026.10.1","env":"prod",
 "request_id":"req_01J…","trace_id":"4bf9…","tenant_id":"0192…","user_id":"0192…",
 "event":"change_request.approved","route":"POST /api/v1/change-requests/{id}/approve",
 "status":200,"duration_ms":84}
```

- Loggers pass structured fields only; `core.logging` drops non-allowlisted keys and runs `redact()` (Aadhaar/Verhoeff, 10-digit mobiles, emails) on any free-text field as a last line of defence.
- Never log request/response bodies, SQL bind values, prompts, completions, file contents or tokens.
- Levels: `ERROR` (needs action), `WARN` (degraded), `INFO` (business events), `DEBUG` (disabled in prod).
- Retention: 400 days in ap-south-1 (covers CERT-In 180 days in India and DPDP 1-year logs).
- Security-relevant events also go to the audit log (tenant-visible) where appropriate.

## 3. Metrics

| Category | Metrics |
|---|---|
| HTTP (RED) | request rate, error rate (5xx/4xx), latency p50/p95/p99 per route class |
| Jobs | queue depth and oldest message age per queue, task success/failure/retry, DLQ size, per-tenant concurrency |
| DB | CPU, memory, connections, replication lag, slow queries, deadlocks, storage, HNSW index size |
| Knowledge | answers/min, first-token and total latency, tool latency, retrieval candidates, fallback rate, refusal rate, citation drop rate, tokens and cost per tenant |
| Ingestion | documents processed, pages OCR'd, failures by stage, time-to-ready |
| DQ | runs, findings by severity/rule, time per 1,000 students |
| Security | login failures, lockouts, MFA events, step-up prompts, 403/404 ratios, RLS/tenant-context errors, break-glass activity, export volume |
| Business | weekly active users per tenant, pre-checks run, certificates issued (M3) |

## 4. Tracing

- OTel auto-instrumentation for FastAPI, SQLAlchemy, httpx, Celery, Redis; manual spans for `kb.ask`, `llm.call`, `tool.*`, `retrieval.hybrid`, `dq.run`, `pdf.render`.
- Trace context propagated from BFF → API → Celery tasks (headers) → external calls.
- Sampling: 100% errors, 20% of successful requests (tune), 100% of `kb.ask` in staging.
- Span attributes are allowlisted (no PII); SQL statements recorded without bind values.

## 5. SLOs and error budgets

| SLO | Target (Stage 0–1) | Measurement |
|---|---|---|
| API availability | 99.5% monthly (school hours weighted) | Successful responses / valid requests at ALB |
| API latency | 95% of reads < 300 ms, writes < 800 ms | ALB + OTel |
| Ask latency | 95% first token < 3 s; complete < 10 s | `kb.ask` spans |
| Ingestion freshness | 95% of text-layer PDFs ready < 3 min | job metrics |
| DQ run time | 95% of 2,000-student runs < 2 min | job metrics |
| Audit integrity | 100% daily chain verification success | beat job |

Error budget policy: if a month's budget is exhausted, freeze feature work and prioritize reliability until back within budget.

## 6. Alerting

| Alert | Condition | Severity | Runbook |
|---|---|---|---|
| API down | Health check failing 2 min | P1 | R1 |
| Error rate high | 5xx > 2% for 5 min | P2 | R1 |
| Latency high | p95 > 2× target for 15 min | P3 | R1 |
| Queue backlog | oldest message > 10 min (ingest/exports) | P3 | R3 |
| DLQ not empty | > 0 messages | P3 | R3 |
| DB pressure | CPU > 80% 15 min, storage > 80%, connections > 80% | P2 | R2 |
| LLM failures | error rate > 10% or circuit open | P3 | R4 |
| AI budget | tenant at 80% / 100% | Info / P4 | — |
| Audit chain broken | verification failure | **P1 security** | R5 |
| Tenant-context/RLS errors | any in prod | **P1 security** | R5 |
| Break-glass used | any | Info (notify) | — |
| Bulk export spike | > N exports/hour per tenant | P2 security | R5 |
| GuardDuty high finding | any | P1 security | R5 |

Delivery: phone push + SMS/email to the on-call (founder) for P1/P2; daily digest for P3/P4.

## 7. Incident response

### 7.1 Severity
| Sev | Examples | Response |
|---|---|---|
| **P1** | Outage during school hours; suspected data breach; audit tampering | Immediate; all hands; status updates every 30 min |
| **P2** | Major feature down (Ask, imports); severe degradation | Within 1 h |
| **P3** | Minor degradation; single-tenant issue | Same business day |
| **P4** | Cosmetic, low impact | Backlog |

### 7.2 Process
1. **Detect & declare:** open an incident record (time noticed = T0), assign severity.
2. **Contain:** e.g., revoke sessions/keys, disable feature flag, block IPs at WAF, isolate tenant, pause workers.
3. **Assess personal-data impact:** which tenants, data classes, number of individuals, time window.
4. **Notify (clocks start at T0 for security incidents):**
   - **CERT-In within 6 hours** for reportable cyber incidents (unauthorized access, data breach/leak, malware, etc.) using the prepared format.
   - **Affected schools (Data Fiduciaries) without undue delay**, target within 24 hours of confirmation, with facts they need for their own DPDP notices to parents and the Data Protection Board (Board: without delay + detailed report within 72 hours, per DPDP Rules).
   - Provide bilingual parent-notice templates and a facts package (nature, extent, timing, likely consequences, mitigation, what families can do, contact).
5. **Eradicate & recover:** fix root cause, restore from clean backups if needed, rotate secrets/keys, verify audit chain.
6. **Post-incident:** blameless postmortem within 5 working days (template `docs/templates/incident-postmortem.md`), action items tracked to closure, threat model updated.

### 7.3 Evidence preservation
Snapshot affected resources, export relevant logs to the log-archive account, preserve audit exports; record every action taken with timestamps.

## 8. Runbooks

| ID | Runbook | Key steps |
|---|---|---|
| R1 | API/web down or erroring | Check ALB target health → recent deploys (roll back if correlated) → DB/Redis health → dependency status → scale out → communicate |
| R2 | Database pressure | Identify top queries (`pg_stat_statements`) → kill runaway queries → check autovacuum/bloat → scale instance class (maintenance window) → add index via `CONCURRENTLY` |
| R3 | Queue backlog / DLQ | Identify queue and tenant → check provider errors (OCR/embeddings) → scale workers → inspect DLQ messages → fix and re-drive idempotently |
| R4 | LLM/embeddings provider outage | Confirm circuit breaker open → ensure search-only mode banner active → pause ingestion embeds → monitor provider status → re-enable gradually |
| R5 | Suspected security incident | Declare P1 → contain → preserve evidence → assess data impact → CERT-In 6-hour clock → notify schools → follow 7.2 |
| R6 | Restore from backup | Choose PITR timestamp → restore to new instance → validate (counts, audit chain) → switch connection via secret update → post-restore checks → document |
| R7 | Key/secret compromise | Rotate secret in Secrets Manager → redeploy → revoke old API keys at provider → for KMS/DEK concerns, rotate and re-encrypt → review access logs |
| R8 | Tenant offboarding | Confirm written request → full export delivered → disable access → delete data (jobs) → destroy tenant keys → verify → certificate of deletion |
| R9 | Portal format changed (board/UDISE+) | Obtain new format → new export profile version → test with synthetic data → release → notify affected schools |

## 9. Support model (schools)

- Channels: in-app help, a support WhatsApp Business number/email during school hours, escalation to phone for P1/P2.
- Support staff (initially the founder) cannot view school data without break-glass approval; most issues are diagnosed from IDs, job states and metrics.
- Onboarding playbook per school: kickoff, data permissions/DPA, import session, training for office staff (EN/TE), first pre-check with support on call.
- Status page for incidents and maintenance notices.

## 10. Operational reviews

- Weekly: alerts review, top errors, slow queries, eval drift, AI spend per tenant.
- Monthly: SLO report, cost per school, access reviews sample, dependency updates status.
- Quarterly: restore drill, incident drill (tabletop incl. CERT-In 6-hour flow), threat model review.

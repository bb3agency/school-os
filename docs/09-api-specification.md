# 09 · API Specification (v1)

| Field | Value |
|---|---|
| Version | 0.1 · 2026-09-26 |
| Style | REST/JSON over HTTPS · OpenAPI 3.1 generated from FastAPI (source of truth at runtime: `/api/v1/openapi.json` in non-prod) |
| Related | 03-TRD (FR IDs), 07-Security §6 (permissions) |

---

## 1. Topology

```
Browser ──(cookie session)──▶ web/BFF  /bff/api/v1/*  ──(Bearer access token + service auth)──▶ api  /api/v1/*
```

- Browsers never call the API directly and never hold tokens. BFF routes mirror API paths and add CSRF checks.
- The API trusts nothing from the BFF except a valid user access token plus internal service authentication; it re-checks tenant, permission and scope on every call.

## 2. Conventions

| Topic | Convention |
|---|---|
| Base path | `/api/v1` |
| Format | `application/json; charset=utf-8`; dates `YYYY-MM-DD`; timestamps RFC 3339 UTC; IDs UUID strings |
| Language | `Accept-Language: en` or `te` selects message/explanation language |
| Request ID | `X-Request-Id` (generated at edge if absent), echoed in responses and logs |
| Idempotency | `Idempotency-Key` required on POSTs that create resources or start jobs; stored 24 h per tenant+user |
| Concurrency | Mutable resources return `ETag`; updates require `If-Match`; mismatch → `412 Precondition Failed` |
| Pagination | Cursor-based: `?limit=50&cursor=…` (max 200); response `{ "data": [...], "next_cursor": "…" \| null }` |
| Filtering/sorting | Explicit query params per endpoint (e.g., `?section_id=&status=`); `sort=field` or `sort=-field` from an allowlist |
| Async jobs | `202 Accepted` + `Location: /api/v1/jobs/{id}`; job resource shows `status`, `progress`, `result_url` |
| Rate limits | `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` headers; `429` with `Retry-After` |
| Deprecation | Additive changes only within v1; removals announced with `Deprecation` and `Sunset` headers |

## 3. Errors (RFC 9457 problem details)

```json
{
  "type": "https://docs.schoolos.example/errors/validation",
  "title": "Validation failed",
  "status": 422,
  "code": "validation_error",
  "detail": "2 fields need attention",
  "instance": "/api/v1/students/0192f…/values",
  "request_id": "req_01J…",
  "errors": [
    { "field": "value_date", "code": "date_in_future", "message_key": "errors.date_in_future" },
    { "field": "attribute_key", "code": "aadhaar_full_number_rejected", "message_key": "errors.aadhaar_last4_only" }
  ]
}
```

| Status | When |
|---|---|
| 400 | Malformed request |
| 401 | Not authenticated / token expired |
| 403 | Authenticated but lacking permission (**only** when existence of the resource isn't sensitive) |
| 404 | Not found **or** outside caller's tenant/scope (never reveal existence) |
| 409 | State conflict (e.g., approving a non-pending request) |
| 412 | ETag mismatch |
| 413 / 415 | File too large / unsupported type |
| 422 | Validation errors |
| 428 | Step-up authentication required (`code: step_up_required`) |
| 429 | Rate limited / budget exhausted (`code: ai_budget_exhausted`) |
| 5xx | Server errors, no internals exposed |

## 4. Endpoint catalog (core)

Every endpoint declares its permission; scope rules from 07 §6 apply.

### Identity & session
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/me` | authenticated | User, active tenant, roles, effective permissions, scopes, language |
| POST | `/me/active-tenant` | authenticated | Switch tenant (membership required) |
| GET | `/me/sessions` · DELETE `/me/sessions/{id}` | authenticated | List/revoke own sessions |

### Tenant setup and users
| Method | Path | Permission |
|---|---|---|
| GET/PATCH | `/tenant` | `tenant.settings.manage` (PATCH, step-up) |
| GET/POST | `/academic-years` · `/classes` · `/sections` | read: `student.read_basic`; write: `tenant.settings.manage` |
| POST | `/academic-years/{id}/promotions:preview` · `:commit` · `:undo` | `tenant.settings.manage` |
| GET/POST | `/users` (invite) · PATCH `/users/{id}` | `user.manage` (step-up) |
| PUT | `/users/{id}/roles` · `/users/{id}/scopes` | `role.assign` (step-up) |
| GET | `/roles` · `/permissions` | `user.manage` |

### Students
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/students` | `student.read_basic` | `query` (EN/TE partial), `section_id`, `class_id`, `status`, `admission_no` |
| POST | `/students` | `student.create` | Creates student + first values (source required) |
| GET | `/students/{id}` | `student.read_basic` | Canonical profile + per-source values; C3 masked unless `student.read_sensitive` |
| GET | `/students/{id}/values?attribute=` | `student.read_basic` | Full history per attribute |
| POST | `/students/{id}/values` | `student.update_nonidentity` | Non-identity attributes, or adding a source observation; identity changes → change requests |
| POST | `/students/{id}/sensitive-reveal` | `student.read_sensitive` | Returns one C3 field; audited |
| GET/POST | `/students/{id}/guardians` | read_basic / update_nonidentity | |

### Change requests (maker-checker)
| Method | Path | Permission |
|---|---|---|
| POST | `/change-requests` | `student.identity_change.request` |
| GET | `/change-requests?status=pending` | request or approve permission |
| POST | `/change-requests/{id}/approve` | `student.identity_change.approve` (step-up; approver ≠ requester) |
| POST | `/change-requests/{id}/reject` | `student.identity_change.approve` |
| GET | `/change-requests/{id}/memo.pdf` | request or approve permission |

### Imports and extraction
| Method | Path | Permission |
|---|---|---|
| POST | `/imports` (multipart: file, `source`, `kind`) → 202 | `import.run` |
| GET | `/imports/{id}` · `/imports/{id}/rows?status=error` | `import.run` |
| PUT | `/imports/{id}/mapping` | `import.run` |
| POST | `/imports/{id}/commit` · `/imports/{id}/revert` | `import.commit` |
| GET | `/extraction-items?batch_id=&status=pending_review` | `import.run` |
| POST | `/extraction-items/{id}/confirm` · `/reject` | `import.commit` |

### Data quality
| Method | Path | Permission |
|---|---|---|
| POST | `/dq/runs` (`scope`, `profile_key`) → 202 | `dq.findings.read` |
| GET | `/dq/findings` (`severity`, `rule_id`, `section_id`, `status`) | `dq.findings.read` |
| POST | `/dq/findings/{id}/resolve` | `dq.findings.resolve` |
| POST | `/dq/findings/{id}/waive` | `dq.findings.waive` (step-up for blockers) |

### Documents
| Method | Path | Permission |
|---|---|---|
| POST | `/documents/uploads` → presigned POST (size/type constrained) | `document.upload` |
| POST | `/documents` (register uploaded object + metadata + ACL) → 202 | `document.upload` |
| GET | `/documents` · `/documents/{id}` | `document.read` |
| POST | `/documents/{id}/versions` | `document.upload` |
| PUT | `/documents/{id}/acl` | `document.manage_acl` |
| GET | `/documents/{id}/download-url?version=` | `document.read` (presigned, ≤ 5 min) |
| GET | `/documents/{id}/pages/{n}` | `document.read` (page image for citation preview) |
| DELETE | `/documents/{id}` | `document.manage_acl` |

### Knowledge
| Method | Path | Permission |
|---|---|---|
| POST | `/knowledge/ask` (SSE) | `kb.ask` |
| POST | `/knowledge/queries/{id}/feedback` | `kb.ask` (own queries) |
| GET/POST | `/knowledge/verified-answers` | read: `kb.ask`; write: `kb.verified_answer.manage` |
| GET | `/knowledge/resolve?source=sos://…` | permission of the underlying resource |

### Exports, audit, admin, jobs
| Method | Path | Permission |
|---|---|---|
| GET | `/export-profiles` | `export.board` |
| POST | `/exports` (`profile_key`, `scope`, `format`) → 202 | `export.board` / `student.export` (step-up for bulk personal data) |
| GET | `/exports/{id}` → presigned URL when ready | same |
| GET | `/audit/events` (`actor`, `resource`, `action`, `from`, `to`) · `/audit/verify` | `audit.read` |
| POST | `/admin/tenant-export` → 202 | `tenant.export_all` (step-up) |
| GET/PUT | `/admin/retention` | `tenant.settings.manage` |
| POST | `/admin/break-glass/{id}/approve` · `/revoke` | `breakglass.approve` (step-up) |
| GET | `/jobs/{id}` | job owner or admin |
| GET | `/healthz` · `/readyz` | public (no data) |

## 5. Examples

### 5.1 Search students
`GET /api/v1/students?query=venkat%20sai&section_id=0192…&limit=20`
```json
{
  "data": [{
    "id": "0192f3a0-…", "display_name": "K. VENKATA SAI", "admission_no": "2019/0457",
    "class_section": "IX-B", "status": "active",
    "match": { "field": "full_name", "class": "SPACING" }
  }],
  "next_cursor": null
}
```

### 5.2 Student with per-source values
`GET /api/v1/students/0192f3a0-…`
```json
{
  "id": "0192f3a0-…", "status": "active", "etag": "W/\"7\"",
  "canonical": {
    "full_name": { "value": "K. VENKATA SAI", "source": "admission_register", "verified": true, "provisional": false },
    "dob": { "value": "2012-03-14", "source": "admission_register", "verified": true }
  },
  "values": {
    "full_name": [
      { "source": "admission_register", "value": "K. VENKATA SAI", "verified": true, "recorded_at": "2026-09-02T05:10:00Z", "evidence_document_id": "0192…" },
      { "source": "aadhaar_as_printed", "value": "KOMMINENI VENKATASAI", "verified": true },
      { "source": "udise_plus", "value": "VENKATA SAI K", "verified": false }
    ],
    "aadhaar_last4": [{ "source": "aadhaar_as_printed", "value": "••••", "masked": true }]
  },
  "open_findings": [{ "id": "0192…", "rule_id": "DQ-001", "severity": "medium", "match_class": "INITIALS" }]
}
```

### 5.3 Submit and approve an identity correction
```http
POST /api/v1/change-requests
Idempotency-Key: 5b0a…
{ "student_id": "0192f3a0-…", "attribute_key": "dob", "new_value_date": "2012-03-15",
  "reason": "Birth certificate shows 15/03/2012", "evidence_document_id": "0192…" }
→ 201 { "id": "0192…", "status": "pending", "expires_at": "2026-10-26T…" }

POST /api/v1/change-requests/0192…/approve        (same user as requester)
→ 403 { "code": "self_approval_forbidden", … }

POST /api/v1/change-requests/0192…/approve        (principal, no recent MFA)
→ 428 { "code": "step_up_required", … }
```

### 5.4 Ask the school (SSE)
```http
POST /api/v1/knowledge/ask
Accept: text/event-stream
{ "question": "DEO circular lo exam timings enti?", "session_id": "0192…" }
```
```
event: meta
data: {"query_id":"0192…","language":"mixed","mode":"full"}

event: token
data: {"text":"12 Aug 2026 DEO circular prakaram, exams "}

event: citation
data: {"index":1,"source":"sos://doc/0192…/v1#p2","title":"Circular · DEO Guntur · Exam timings · 12 Aug 2026 (p.2)"}

event: done
data: {"latency_ms":4120,"cited_sources":1}
```

### 5.5 Start a pre-check export
```http
POST /api/v1/exports
Idempotency-Key: 9c1e…
{ "profile_key": "cisce-registration-2026", "scope": { "section_ids": ["…9A","…9B","…9C","…9D"] }, "format": ["pdf","xlsx"], "language": "te" }
→ 202  Location: /api/v1/jobs/0192…
```

## 6. Internal domain events (for workers and future webhooks)

`student.value.recorded` · `student.canonical.changed` · `change_request.submitted|approved|rejected` · `import.committed|reverted` · `dq.run.completed` · `document.version.ready|failed|deleted` · `kb.verified_answer.needs_review` · `export.ready` · `breakglass.granted|expired`.
Events are emitted after commit (transactional outbox table `ops.outbox`) and consumed by workers; payloads carry IDs only, never personal values.

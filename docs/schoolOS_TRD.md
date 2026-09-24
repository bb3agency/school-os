I couldn’t generate the downloadable Markdown file because file-generation is unavailable in this session. Below is the complete TRD content in Markdown; save it as **`schoolos-trd-v0.1.md`**.

# SchoolOS Technical Requirements Document

**Document status:** Draft v0.1  
**Audience:** Founder, product engineer, implementation/DevOps partner, security reviewer, pilot-school stakeholders  
**Scope:** Architecture and operating model for SchoolOS, an AI-native school operations platform for Indian schools  
**Last updated:** 23 September 2026

***

## 1. Purpose

SchoolOS is a secure, AI-native operating system for schools. It reduces spreadsheet-heavy and fragmented operations; unifies authorised school information; simplifies or complements existing ERP workflows; and converts verified records into actionable workflows, summaries, and student-support intelligence

SchoolOS is **not** only an ERP database and is **not** only a chatbot over school documents. It combines:

1. Deterministic operational records: attendance, marks, fees, enrolment, staff, schedules, approvals, and audit events.
2. Document and knowledge management: policies, circulars, report cards, meeting notes, notices, forms, and evidence.
3. A governed AI layer: retrieval-augmented generation (RAG), summaries, drafting, analysis, and recommendations over authorised data.
4. Action workflows: review, assignment, approval, communication, escalation, and outcome tracking.
5. A Holistic Learner Record: evidence-backed academic and support context for every student.

The initial product must support both a shared multi-tenant SaaS model and a dedicated school deployment model without maintaining separate product codebases.

***

## 2. Product principles

### 2.1 Source of truth

Critical school facts must remain in structured, deterministic storage. AI and vector search help with retrieval and interpretation; they do not replace source-of-truth records.

| Data type | System of truth | Examples | AI use |
|---|---|---|---|
| Operational records | Relational database | Attendance, marks, fee ledgers, roles, student IDs | Explain, summarise, detect patterns; never invent values |
| Documents | Object storage plus metadata | Circulars, certificates, policies, meeting notes | Search, cite, summarise, extract |
| Semantic retrieval index | Vector database/index | Chunk embeddings plus metadata | Retrieve authorised contextual evidence |
| School configuration | Relational database/config store | Academic calendar, grading scale, logo, enabled modules | Apply rules and personalise workflows |
| Audit history | Append-oriented audit store | Access, exports, changes, approvals | Investigate and report activity |

### 2.2 Evidence before inference

Any AI output about a student, staff member, financial status, attendance pattern, or school operation must be traceable to authorised source records. The interface must show citations, record links, dates, data freshness, and relevant limitations.

### 2.3 Human accountability

AI must not autonomously make consequential decisions about students. It may identify evidence requiring review or draft a recommended action. A designated human must approve decisions involving grades, promotion, discipline, admissions, fee concessions, sensitive communication, or student-support plans.

### 2.4 Configuration over custom code

Schools should differ through configuration, templates, feature flags, approved integrations, and versioned add-on modules. The SchoolOS codebase must not contain permanent customer-specific branches or `if schoolId === ...` logic.

### 2.5 Secure by default

Child and school data requires strict isolation, least privilege, encryption, logging, backup, and controlled data export. Tenant boundaries must be enforced in the UI, API, database, storage, retrieval index, cache, background jobs, and telemetry. Tenant isolation is a foundational SaaS security requirement. [aws.amazon](https://aws.amazon.com/blogs/security/security-practices-in-aws-multi-tenant-saas-environments/)

### 2.6 Mobile-first operation

The platform must work on ordinary phones and constrained internet connections. It must reduce repeated administrative entry rather than create another system that staff manually maintain.

***

## 3. Problem statement

Indian schools often operate through a mixture of ERP modules, Excel sheets, paper registers, WhatsApp groups, office files, and individual staff memory. Data is partially digitised but remains fragmented.

This leads to:

- Duplicate data entry.
- Inconsistent reports.
- Delayed management visibility.
- Weak student-support coordination.
- Poorly tracked parent communication.
- Compliance evidence scattered across people and folders.
- ERP data that records transactions but does not reliably coordinate action.

SchoolOS addresses the operational question:

> **What requires attention today, who owns the next action, what evidence supports it, and did the action work?**

***

## 4. Goals and non-goals

### 4.1 Goals

- Provide a common platform for school operations, student support, and institutional knowledge.
- Let schools begin through data imports or ERP exports instead of requiring disruptive migration.
- Maintain a Holistic Learner Record with verified facts, bounded human observations, interventions, and outcomes.
- Offer AI assistance that is permission-aware, evidence-backed, and approval-based.
- Support multi-tenant SaaS as the normal commercial model.
- Support dedicated school-owned or school-controlled VPS deployment as a premium option.
- Preserve one product codebase and one release process across deployments.
- Provide schools data portability, auditability, and a defined exit process.

### 4.2 Non-goals for MVP

- Replacing statutory payroll or full accounting systems.
- Building every ERP module before validating customer demand.
- Autonomous student ranking, discipline, promotion, or counselling decisions.
- Continuous behavioural surveillance of children.
- Training general-purpose AI models on customer school data by default.
- Supporting permanent customer-specific source-code forks.
- Treating RAG or vector storage as the source of truth for operational records.

***

## 5. Primary users and roles

| Role | Primary needs | Typical permissions |
|---|---|---|
| School owner/trustee | Operational visibility, institutional risk, finance overview | Aggregated reporting; restricted sensitive-data access |
| Principal | Academic oversight, student support, approvals, compliance | School-wide operational visibility and approvals |
| Administrator | Admissions, records, documents, attendance follow-up | Operational access within assigned scope |
| Teacher/class teacher | Attendance, assessments, remarks, parent messages | Assigned classes and students only |
| Counsellor/student-support lead | Intervention and support workflows | Explicit access to assigned sensitive cases |
| Accountant | Fees, receipts, concessions, reports | Finance data; no unnecessary learner details |
| Parent/guardian | Notices, child-specific information, consent | Their own child or children only |
| Student | Own schedule, approved feedback and work | Own approved records only |
| SchoolOS support operator | Reliability and technical support | Health metadata by default; time-bound break-glass access only |

***

## 6. Functional scope

### 6.1 Foundation modules

1. **Identity and access:** users, roles, staff assignments, parent-child relationships, MFA, sessions.
2. **Student information:** admissions, identity, guardian details, class history, permissions and consent records.
3. **Academic operations:** subjects, assessments, marks, competency evidence, report templates.
4. **Attendance:** daily attendance, absence reasons, approvals, escalations, parent communication evidence.
5. **Fees and finance operations:** fee structures, invoices, receipts, outstanding review, concession workflow, exports.
6. **Documents and knowledge:** upload, classification, retention, versioning, extraction, citation, secure retrieval.
7. **Parent communication:** notices, templates, delivery status, responses, escalations, communication history.
8. **Workflows and approvals:** tasks, owners, due dates, statuses, approvals, outcomes.
9. **Compliance workspace:** requirements, evidence, owners, due dates, document versions, export packs.
10. **Audit and reporting:** access logs, data changes, exports, approvals, integrations, AI retrieval events.

### 6.2 Holistic Learner Record

The learner record is a **student-support tool**, not a hidden behavioural score.

It may include:

- Verified student and enrolment facts.
- Attendance and punctuality history.
- Assessment and competency evidence.
- Assignment completion and submitted work references.
- Teacher observations with author, date, visibility, and review status.
- Approved strengths, interests, and achievements.
- Parent meetings and agreed follow-up actions.
- Intervention plans, ownership, dates, and outcomes.
- Relevant documents and work samples.

It must separate facts from observations and AI suggestions.

| Category | Meaning | Example |
|---|---|---|
| Verified record | Directly entered or validated fact | 72% attendance in Term 1 |
| Human observation | Attributed staff observation | Teacher noted repeated incomplete geometry work |
| AI insight | Non-authoritative analysis | Three assessments indicate a possible geometry gap |
| Action | Approved next step | Two-week remedial plan assigned |
| Outcome | Recorded result | Subsequent assessment shows improvement/no improvement |

The system must not store gossip, unsupported personality labels, hidden risk scores, unnecessary family details, unverified sensitive claims, or automated statements about a child’s potential.

### 6.3 AI and RAG capabilities

Permitted AI use cases:

- Summarise authorised student history before meetings.
- Explain changes in attendance, completion, or academic evidence.
- Draft teacher-reviewed parent communication.
- Identify records needing human review.
- Turn approved meeting decisions into tasks.
- Search school policies and documents with citations.
- Draft inspection or compliance packs from verified records.
- Answer management questions grounded in source records.
- Generate differentiated learning-support material subject to teacher approval.

AI outputs must:

- Respect the current user’s identity, role, school, and permissions.
- Cite retrieved records or documents.
- Show data cut-off time where relevant.
- Avoid unsupported diagnosis, prediction, and labelling.
- Require human approval for consequential actions.
- Be logged without retaining unnecessary sensitive prompt or output content.

***

## 7. Deployment model

### 7.1 Supported deployment modes

| Mode | Infrastructure | Typical customer | Recommendation |
|---|---|---|---|
| Shared multi-tenant SaaS | Vendor-managed shared environment with strict logical isolation | Small and mid-sized schools | Default |
| Dedicated managed tenant | Dedicated environment managed by SchoolOS | Premium schools and chains | Paid enterprise option |
| Customer-hosted, vendor-managed | VPS/cloud account owned by school but operated by SchoolOS | Schools demanding infrastructure control | Premium contractual option |

### 7.2 Architecture decision

Build SchoolOS as a **tenant-aware SaaS platform from day one**.

That means:

- Every core record supports `tenant_id`.
- Every query is scoped to a tenant.
- Every document is associated with a tenant.
- Every vector retrieval has tenant and permissions metadata.
- The same application can later run in dedicated single-tenant mode.

A dedicated deployment is not a separately built product. It is the same SchoolOS release operating with a single school’s data, configuration, services, secrets, and infrastructure.

### 7.3 Non-negotiable rule

> **There is one SchoolOS source code repository and one release line.**

Never maintain:

```text
school-a branch
school-b branch
school-c branch
```

Never add permanent school-specific code like:

```ts
if (tenantId === "school-a") {
  // one-off permanent behaviour
}
```

School-specific behaviour must be driven by configuration, templates, policies, feature flags, integrations, or a separately versioned add-on module.

***

## 8. Reference architecture

```text
                         CENTRAL PRODUCT ENGINEERING
+------------------------------------------------------------------+
| schoolos-core repository                                         |
| Automated tests, security checks, versioned container images     |
| Release registry, changelog, migration registry                  |
| Staging tenant, demo tenant, pilot release ring                  |
+------------------------------+-----------------------------------+
                               |
                          approved release
                               |
      +------------------------+--------------------------+
      |                                                   |
      v                                                   v
SHARED SAAS                                      DEDICATED SCHOOL DEPLOYMENT
+----------------------------+                  +----------------------------+
| API/web app                |                  | School-owned/assigned VPS  |
| worker(s)                  |                  | API/web app                |
| tenant-aware PostgreSQL    |                  | worker(s)                  |
| object storage             |                  | dedicated PostgreSQL       |
| vector/search service      |                  | dedicated object storage   |
| central observability      |                  | dedicated vector index     |
+----------------------------+                  | encrypted off-host backup  |
                                                +----------------------------+
```

### 8.1 Logical components

| Component | Responsibility | Initial guidance |
|---|---|---|
| Web/API application | UI, API, auth, workflows | Modular monolith, containerised |
| Background worker | Imports, notifications, OCR, embeddings, reports | Separate worker container |
| Relational database | Transactional source-of-truth data | PostgreSQL |
| Object storage | Original files and generated exports | S3-compatible, versioned, encrypted |
| Vector/search service | Embeddings and metadata-filtered retrieval | Managed or dedicated service |
| Cache/queue | Jobs, rate limits, temporary cache | Redis or managed equivalent |
| AI gateway | Model calls, prompt policy, logging | Application-owned abstraction |
| Identity service | Authentication, MFA, sessions | Built-in initially or managed IdP |
| Observability stack | Metrics, errors, audit, uptime | Centralised and privacy-minimised |
| Control plane | Tenant registry, release state, deployment actions | Internal operations tooling |

### 8.2 MVP choice

For the first one to three schools:

- Use a modular monolith.
- Use Docker Compose.
- Use PostgreSQL.
- Use one separate worker process.
- Use encrypted object storage outside the VPS.
- Use automatic backups.
- Avoid Kubernetes and microservices.

***

## 9. Tenant isolation requirements

Tenant isolation must be enforced at every system layer. UI-level restrictions alone are not sufficient. AWS guidance also stresses that each SaaS tenant’s resources must be isolated and that isolation strategy depends on deployment and service choices. [docs.aws.amazon](https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/tenant-isolation.html)

| Layer | Requirement |
|---|---|
| Authentication | Every session has authenticated tenant context |
| Authorization | Check role, tenant, relationship to resource, and action |
| API | Derive tenant from trusted auth/domain context; do not trust client tenant IDs |
| Database | Every tenant-owned table includes `tenant_id`; use Row-Level Security where practical |
| Object storage | Tenant-specific prefixes/buckets; signed URLs scoped to object and expiry |
| Vector index | Tenant and permission metadata on every chunk; enforce at retrieval |
| Cache | Tenant-prefixed cache keys |
| Queues | Tenant ID included in jobs; server-side re-authorisation before execution |
| Logs | Tenant ID included; redact/minimise personal content |
| Backups | Encrypted and segmented backups |
| Support access | Explicit, time-bound, audited break-glass procedure |

### 9.1 Shared SaaS database model

Every tenant-owned table includes `tenant_id`.

```sql
CREATE TABLE students (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  admission_number TEXT NOT NULL,
  full_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, admission_number)
);
```

The production application database role should not bypass Row-Level Security. Migration/admin credentials must remain separate from runtime application credentials.

### 9.2 Dedicated deployment model

A dedicated school environment contains:

- Its own PostgreSQL database.
- Its own file/object-storage namespace.
- Its own vector index or collection.
- Its own secrets.
- Its own backup chain.
- Its own deployment and health record.

Keep `tenant_id` in the data model even in dedicated mode, because it preserves portability and supports future school chains or multiple institutions.

***

## 10. Configuration and customisation

### 10.1 Configuration hierarchy

```text
Core product defaults
    ↓
Plan/module defaults
    ↓
School tenant configuration
    ↓
Academic-year configuration
    ↓
Role/class/workflow configuration
    ↓
User-specific preferences
```

### 10.2 Configuration domains

| Domain | Examples |
|---|---|
| Branding | Logo, school name, colours, custom domain, contact details |
| Academic | Board, year, terms, subjects, grades, competency framework |
| Attendance | Thresholds, leave categories, escalation timing |
| Fees | Fee heads, schedules, concession approval flow |
| Communications | Languages, templates, channels, sender identity |
| Roles | Custom roles, class scope, approvals |
| Workflows | PTM process, absence escalation, intervention lifecycle |
| Reporting | Report-card templates, dashboards, exports |
| Integrations | Payments, SMS/WhatsApp, biometrics, ERP imports |
| Feature flags | Modules, pilot features, add-ons |

### 10.3 Customisation classification

| Request type | Example | Policy |
|---|---|---|
| Core product | Attendance, roles, audit logs | Build once for all schools |
| Configuration | Grade scale, colours, attendance threshold | Configure without code |
| Template | Report-card layout, notices | Versioned template capability |
| Integration | Payment or biometric connector | Reusable connector, paid setup |
| Add-on module | Transport, hostel, advanced compliance | Versioned paid module |
| Bespoke request | One school’s unusual workflow | Paid project, isolated extension |
| Permanent fork | School-specific core code forever | Not supported |

### 10.4 Product rule

> If three schools could plausibly need a feature, productise it.  
> If one school needs it, configure it first.  
> If it cannot be configured, charge separately and isolate it as an extension.

***

## 11. Releases, updates, and migrations

### 11.1 Versioning

Use semantic versioning:

```text
MAJOR.MINOR.PATCH
```

| Release type | Example | Meaning |
|---|---|---|
| Patch | `1.2.4` | Security fix, bug fix, small compatible correction |
| Minor | `1.3.0` | Compatible new module or feature |
| Major | `2.0.0` | Breaking architecture/workflow change requiring migration |

For every production tenant, store:

- App release version.
- Database schema version.
- Configuration schema version.
- Enabled modules and flags.
- Migration status.
- Deployment timestamp.
- Backup snapshot reference.
- Rollback version.
- Release operator.

### 11.2 Environments

```text
Local development
  → Shared development
  → Staging
  → Demo tenant
  → Pilot school
  → General production rollout
```

Production data must not be copied into local, demo, or normal development environments.

### 11.3 Progressive rollout

Use feature flags to release functionality safely:

1. Deploy code without activating it for everyone.
2. Enable it for internal testing.
3. Enable it for a pilot school.
4. Monitor errors, speed, support requests, and user feedback.
5. Expand to selected tenants.
6. Roll out generally.
7. Remove obsolete flags after stability is confirmed.

### 11.4 Database changes

Use the **expand → migrate → contract** pattern:

1. Add a new nullable field/table/index.
2. Deploy code that supports old and new data structures.
3. Backfill old data using safe background jobs.
4. Switch the workflow using a feature flag.
5. Verify data quality and user flow.
6. Remove obsolete schema only after all tenants are migrated.

This approach supports rollback without data loss during deployment. [planetscale](https://planetscale.com/blog/backward-compatible-databases-changes)

### 11.5 Rollback requirements

- Roll back the application to the previous stable image.
- Keep schemas compatible during rollout.
- Use feature flags to disable faulty functionality quickly.
- Take backups before destructive operations.
- Log every rollout, migration, rollback, and flag change.

***

## 12. Internal control plane

The control plane is an internal SchoolOS operations capability. It is not a customer-facing priority in early MVP stages.

### 12.1 Tenant registry

Maintain these fields:

| Field | Description |
|---|---|
| Tenant ID | Immutable internal school identifier |
| School name | Legal and display name |
| Deployment mode | Shared, dedicated, customer-hosted |
| Domain | Active domain and TLS status |
| App version | Current, target, previous stable release |
| Schema version | Migration state |
| Feature set | Enabled modules and flags |
| Health | Uptime, error rate, resource status |
| Backup state | Last successful backup and restore-test status |
| Contract plan | SLA and renewal information |
| Technical contacts | Customer-approved contacts only |
| Residency/storage notes | Approved infrastructure details |

### 12.2 Required operations

- Provision a school environment.
- Deploy an approved release.
- Run or schedule migrations.
- Verify backups.
- Create restore points.
- Roll back app versions.
- Toggle approved feature flags.
- Rotate secrets.
- View health without reading student content.

### 12.3 First implementation

For the first few schools, this may be:

- Private admin interface.
- Audited deployment scripts.
- Ansible playbooks.
- Tenant registry database.
- Central monitoring dashboard.

Before scaling past a small number of deployments, convert this into a proper internal operations console.

***

## 13. Infrastructure and DevOps

### 13.1 Initial dedicated VPS topology

```text
Internet
  ↓
Cloudflare / DNS / WAF / TLS edge
  ↓
School VPS
  ├── Reverse proxy
  ├── SchoolOS API/web container
  ├── SchoolOS worker container
  ├── PostgreSQL
  ├── Redis
  └── Backup agent

External managed services
  ├── Encrypted object storage
  ├── Email/SMS/WhatsApp provider
  ├── AI and embedding provider through SchoolOS AI gateway
  └── Central metrics/error monitoring
```

### 13.2 Provisioning as code

Use infrastructure-as-code rather than manual server setup. OpenTofu can define infrastructure resources using versioned configuration, while deployment tooling such as Ansible can configure servers and deploy containers. [opentofu](https://opentofu.org/)

### 13.3 Secrets management

- Never commit secrets to Git.
- Keep a separate secret set per tenant and environment.
- Use a password manager or secrets manager with access history.
- Limit CI/CD credentials to minimum permissions.
- Rotate keys after staff exit, suspected exposure, or routine intervals.
- Keep third-party API keys server-side.

### 13.4 Environments

| Environment | Purpose | Data policy |
|---|---|---|
| Local | Developer testing | Synthetic data only |
| Development | Shared engineering | Synthetic/anonymised data |
| Staging | Pre-production checks | Synthetic or approved masked data |
| Demo | Sales demonstration | Fully synthetic data |
| Pilot | Limited live school use | Real data with production controls |
| Production | Live school operations | Real data with full controls |

***

## 14. AI and RAG architecture

### 14.1 AI request flow

```text
Authenticated user request
  → Resolve tenant + role + permissions
  → Identify task and sensitivity
  → Query structured facts through authorised access
  → Retrieve authorised document chunks with metadata filters
  → Validate retrieved content as untrusted reference material
  → Build policy-constrained prompt with citations
  → Call model through SchoolOS AI gateway
  → Validate output
  → Attach evidence, limitations, approval requirement
  → Write audit metadata
```

### 14.2 RAG ingestion flow

```text
Upload/import
  → Malware and file-type validation
  → OCR/text extraction in isolated worker
  → Classify document and attach tenant/role metadata
  → Scan for prompt injection and suspicious content
  → Chunk and embed
  → Store original immutable file and metadata
  → Store vector chunks with access controls
  → Quality check before retrieval availability
```

### 14.3 Vector metadata requirements

Every vector chunk requires metadata such as:

```json
{
  "tenant_id": "school-a",
  "document_id": "doc-uuid",
  "source_type": "policy|student_record|meeting_note|report",
  "classification": "internal|restricted|sensitive",
  "permitted_roles": ["principal", "teacher"],
  "permitted_subject_ids": ["optional-ids"],
  "student_id": "optional-student-id",
  "created_at": "timestamp",
  "version": 1,
  "content_hash": "sha256-hash",
  "retention_until": "timestamp"
}
```

The client must never be able to set the tenant namespace or override role filters.

### 14.4 RAG safety controls

OWASP recommends treating RAG as an end-to-end security problem: protect ingestion, embeddings, vector storage, retrieval, generation, and downstream tool use. It specifically recommends tenant isolation, retrieval-time access checks, chunk-level permissions metadata, document hashes, and prompt-injection handling. [cheatsheetseries.owasp](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)

Required controls:

- Treat uploaded and retrieved content as untrusted.
- Scan uploads and chunks for prompt injection.
- Detect hidden Unicode and suspicious embedded instructions.
- Reinforce that retrieved content is reference data, not system instruction.
- Never give a model unrestricted tool access based on retrieved text.
- Store content hashes and provenance.
- Apply permissions at retrieval time.
- Rate-limit and monitor retrieval queries.
- Require citations for factual answers.
- Return “cannot verify from authorised records” rather than fabricate.

### 14.5 AI data-use policy

- School data is not used to train SchoolOS or third-party models by default.
- Evaluate AI vendors for retention, model-training terms, subprocessors, regional processing, and contracts.
- Redact or minimise sensitive data sent to external model APIs where possible.
- Set retention limits for prompts and outputs.
- Require human approval before AI sends messages, updates records, gives grades, imposes discipline, or reveals sensitive content.

***

## 15. Security requirements

### 15.1 Identity and access

- Modern password hashing.
- MFA for principals, admins, finance, and SchoolOS support operators.
- Session expiry and token rotation.
- RBAC plus contextual access controls.
- Separate human, service, and integration credentials.
- Immediate staff-offboarding procedure.
- Periodic privileged-access review.

### 15.2 Encryption

- HTTPS and TLS 1.2+ in transit.
- Encryption at rest for databases, storage, and backups.
- Short-lived signed file-download links.
- Separate keys/secrets by environment.
- Consider separate encryption-key strategy for premium dedicated deployments.

### 15.3 Application security

- Input validation and output encoding.
- CSRF protection where needed.
- Rate limits for login, AI, exports, and public endpoints.
- Secure headers and Content Security Policy.
- Dependency pinning and vulnerability scanning.
- Security assessment for every new integration or parser.
- Separate migration/admin database credentials from runtime app credentials.

### 15.4 Audit logs

Log:

- Logins, MFA events, password resets.
- Account activation/deactivation.
- Restricted record views, downloads, exports.
- Important data changes.
- Permission and role changes.
- Consent changes.
- AI request/retrieval metadata.
- Integration status and errors.
- Break-glass support access.
- Backup, restore, deployment, migration, and rollback actions.

### 15.5 Security testing

- Dependency and secret scans on pull requests.
- Static analysis before releases.
- Automated authorisation and tenant-isolation tests.
- Penetration test before substantial production scale.
- Backup restore drills.
- Incident-response tabletop exercises.

***

## 16. Privacy and child-data requirements

This section is a product baseline, not legal advice. Obtain review from qualified Indian privacy counsel before production use.

The Digital Personal Data Protection Act, 2023 treats individuals under 18 as children. It requires verifiable parental or guardian consent before processing children’s personal data unless an applicable exception applies, prohibits processing likely to harm a child’s well-being, and restricts tracking, behavioural monitoring, and targeted advertising directed at children. The DPDP Rules, 2025 were notified on 14 November 2025. [meity.gov](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf)

### 16.1 Product controls

- Maintain a data inventory and data-flow register.
- Support consent and notice records as required by school policy and legal advice.
- Minimise collection and retention.
- Prohibit advertising, sale, or unauthorised sharing of student data.
- Restrict counselling, health, safeguarding, or disability-related records.
- Ban opaque student profiling and permanent risk labels.
- Frame AI flags as evidence requiring review, not statements of fact.
- Maintain subprocessor records and suitable contracts.
- Support access, correction, retention, export, and grievance workflows where applicable.

### 16.2 Data classification

| Classification | Examples | Access policy |
|---|---|---|
| Public | Published school notice | Public or school-defined |
| Internal | Timetable, general policies | Authenticated school users |
| Restricted | Marks, attendance, fee status, parent messages | Role and relationship limited |
| Sensitive restricted | Counselling, health, safeguarding, disability accommodations | Explicit need-to-know access |

### 16.3 Retention and deletion

Every record or document category must define:

- Purpose.
- Owner.
- Classification.
- Retention basis and duration.
- Archive policy.
- Deletion/anonymisation method.
- Legal or academic hold overrides.

Deletion processes must be technically truthful. Do not promise instant deletion from backups if backup-retention policies make that impossible.

***

## 17. Backups and disaster recovery

### 17.1 Minimum production baseline

- Automated encrypted database backup at least daily.
- Versioned document backup outside the VPS.
- Separate backup failure domain/account where possible.
- Backup monitoring and alerting.
- Monthly restore tests.
- Documented restore runbook.
- CPU, memory, disk, database growth, and certificate-expiry monitoring.

### 17.2 Initial targets

| Metric | Initial target |
|---|---:|
| RPO | Up to 24 hours |
| RTO | 4–8 business hours |
| Backup check | Daily automated verification |
| Restore test | Monthly |
| Availability | Contract-defined; avoid unrealistic SLA promises early |

### 17.3 Incident procedure

1. Detect alert or receive report.
2. Assess tenant, service, integrity, and security scope.
3. Preserve evidence and stop harmful automation.
4. Notify authorised school contact based on severity.
5. Restore or recover using runbook.
6. Validate data, permissions, integrations, and services.
7. Document incident and customer impact.
8. Conduct post-incident review for material incidents.

***

## 18. Observability and support

### 18.1 Central operational telemetry

Collect only what is necessary:

- Uptime and endpoint health.
- Latency and application errors.
- CPU, memory, storage, and database capacity.
- Queue backlog and failed jobs.
- Backup success/failure.
- TLS certificate expiry.
- App/schema/config versions.
- Integration health.
- AI latency, cost, retrieval success, and errors.

Avoid sending raw student records, parent messages, documents, or unnecessary AI prompt text to third-party monitoring products.

### 18.2 Support access

- Default: health and anonymised metadata only.
- Elevated: explicit approved request, named operator, purpose, time limit, and audit trail.
- Break-glass: emergency-only, time-bound, reviewed afterward.

### 18.3 Support tiers

| Tier | Customer | Coverage |
|---|---|---|
| Standard | Shared SaaS schools | Business-hours support |
| Premium | Dedicated deployment | Faster response and scheduled upgrades |
| Enterprise | Chains/regulatory customers | Contractual SLA and governance process |

***

## 19. Data portability and exit

The school owns its operational data. SchoolOS owns the product source code, reusable modules, templates, generic improvements, and platform intellectual property unless a separate agreement states otherwise.

At contract exit, provide an agreed export package containing:

- Structured data in CSV, JSON, or documented database format.
- Original uploaded documents with a manifest.
- Relevant configuration and templates where technically suitable.
- Data dictionary and import guidance.
- Audit/export history where contractually required.

For customer-hosted deployments, define:

- VPS/root-access ownership.
- How SchoolOS access is removed.
- Environment retention or shutdown procedure.
- Backup-retention and deletion timeline.
- Migration assistance scope and fees.

***

## 20. Commercial and contractual model

### 20.1 Commercial components

| Component | Commercial treatment |
|---|---|
| SchoolOS licence | Monthly or annual recurring subscription |
| Initial implementation | One-time onboarding, migration, training |
| Dedicated deployment | Premium setup and managed-operations fee |
| VPS/cloud/domain | Prefer direct school billing where ownership is promised |
| Integrations | Setup fee plus ongoing vendor costs |
| Bespoke extension | Fixed project fee plus optional maintenance |
| Support/SLA | Plan-based recurring add-on |

### 20.2 Contract must define

- School data ownership and SchoolOS IP ownership.
- Licence scope.
- Support hours and maintenance windows.
- Update policy and emergency-patch authority.
- Data processing and subprocessor terms.
- Breach response.
- Backup/restore scope and limitations.
- Data export/exit procedure.
- Custom-development scope and maintenance.
- Infrastructure-account ownership.
- School responsibilities for lawful data collection and authorised use.

***

## 21. MVP delivery plan

### Phase 0: Validation and architecture

- Interview principals, teachers, administrators, accountants, and parents.
- Validate one high-pain workflow: attendance follow-up or student-support intervention.
- Define data model, roles, tenant boundary, and security baseline.
- Create synthetic demonstration data.
- Draft privacy, support, and hosting terms.

### Phase 1: Foundation

- Authentication, tenants, roles, audit events.
- School/student/class structures.
- Attendance and basic assessment imports.
- Document upload/classification.
- Configuration framework.
- Dockerised app, worker, database, and backup system.
- Staging/demo environments.

### Phase 2: Action workflows

- Attendance escalation.
- Parent communication approval and records.
- Tasks, ownership, deadlines, and outcomes.
- Holistic Learner Record v1.
- Management dashboard with evidence links.
- Data import/mapping tool.

### Phase 3: Controlled AI

- Permission-aware document search.
- AI summaries with citations.
- Retrieval metadata enforcement.
- Approval workflow for AI drafts.
- Prompt-injection protections.
- AI cost limits.

### Phase 4: Pilot hardening

- Pilot onboarding and data migration.
- Backup/restore drill.
- Tenant-isolation test suite.
- Basic control plane.
- Feature flags and progressive rollout.
- Support process and release notes.

### Phase 5: Dedicated deployment

- One-command deployment template.
- Dedicated VPS runbook.
- Customer-owned infrastructure onboarding.
- Central health monitoring.
- Automated updates and rollback.

***

## 22. Acceptance criteria

### Tenant safety

- Cross-tenant API, storage, cache, queue, and vector tests fail closed.
- School A users cannot retrieve or infer School B data.
- Every tenant-owned record contains a tenant identifier.
- Support staff cannot view content without logged elevated access.

### Core workflow

- Staff can record/import attendance.
- Configured rules can create a review case.
- Staff can inspect source evidence.
- An authorised user can approve parent communication.
- The system records delivery, follow-up, and outcome.
- Learner records distinguish facts, observations, AI insights, actions, and outcomes.

### AI safety

- AI answers cite records/documents.
- Retrieval honours tenant and role filters.
- Uploaded malicious instructions do not override AI policy.
- AI cannot take consequential action without approval.
- The system can say it cannot verify information.

### Operations

- A school can be provisioned from documented templates.
- A named release can deploy and roll back.
- Migration status is visible.
- Backup success is monitored and restore demonstrated.
- Version, health, backups, and features are visible for every tenant.

***

## 23. Risks and mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Customer-specific forks | Updates become impossible to manage | Configuration-first policy and extension model |
| Cross-school data leak | Serious child-data and trust harm | Defence-in-depth isolation and automated tests |
| AI hallucination | Wrong student or school action | Citations, source-of-truth data, approval gates |
| Prompt injection | Documents may manipulate RAG outputs | Scan, treat context as untrusted, restrict tools |
| Excessive profiling | Child-data and trust risk | Data minimisation, no opaque scores, human review |
| VPS failure | Downtime/data loss | Off-host backups and restore drills |
| Weak adoption | Staff continue Excel/WhatsApp | Start with urgent workflow and minimise entry |
| Dedicated-deployment cost | Operations grow per school | Automate and charge premium |
| Fragile integrations | Vendors/APIs change | Adapters, retries, monitoring, fallbacks |
| AI cost growth | Margins become unpredictable | Budgets, quotas, model routing, monitoring |

***

## 24. Assumptions to validate

1. Schools will pay for operations intelligence and student-support workflows.
2. Schools will accept shared SaaS if isolation and data export are credible.
3. Premium schools will pay enough for dedicated VPS environments.
4. Existing ERP data can be exported in useful formats.
5. Teachers will use AI only if it saves time and preserves approval/control.
6. Messaging integrations can work lawfully with appropriate consent and workflow design.
7. Holistic student records can be maintained without excessive teacher burden.
8. Chosen AI vendors can meet required privacy, latency, and cost requirements.

***

## 25. Final architecture position

SchoolOS should be built as a **portable, tenant-aware SaaS platform**.

- Shared SaaS is the default commercial offering.
- Dedicated VPS/cloud deployments are a premium option.
- Both run the same release of the same SchoolOS codebase.
- School differences are handled through data, configuration, templates, flags, integrations, and versioned modules.
- Relational records remain the source of truth.
- RAG and vector search provide controlled retrieval and contextual intelligence.
- AI must be evidence-backed, permission-aware, human-approved for consequential actions, and safe for child-data contexts.

This gives SchoolOS the speed and maintainability of SaaS while retaining a credible path for schools that require dedicated infrastructure.
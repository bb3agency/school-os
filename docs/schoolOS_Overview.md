# SchoolOS Project Summary

**Project type:** AI-native school operations platform  
**Target geography:** India  
**Initial target customers:** Private schools, beginning with mid-sized CBSE/ICSE/state-board schools that still operate through spreadsheets, WhatsApp, paper registers, disconnected ERP modules, and manual reporting

SchoolOS should be built as a **tenant-aware SaaS platform with an optional dedicated VPS deployment**, not as a separately customised application for every school. The central product goal is to turn school data into trusted operational action: identify what needs attention, show the evidence, assign ownership, and record whether the action worked.

India is a large potential market for this category: UDISE+ is the national platform used for school reporting, while 2024–25 data indicates roughly 3.40 lakh recognised private unaided schools and about 9.59 crore enrolled students in that segment. [udiseplus.gov](https://udiseplus.gov.in/)

***

## The core problem

Most schools are “digitised” but not genuinely automated or intelligent.

They may have:

- An ERP for basic attendance, fees, admissions, and report cards.
- Excel sheets for staff work, student lists, fee follow-up, examination analysis, and compliance.
- WhatsApp groups for parent communication and teacher coordination.
- Paper registers, PDFs, scans, and unstructured office records.
- Important operational knowledge held only by a principal, coordinator, or admin staff member.

This creates several gaps:

| Current problem | Operational consequence | SchoolOS opportunity |
|---|---|---|
| Fragmented records | Staff repeatedly search across sheets, files, and messages | Unified operational workspace |
| Delayed reports | Principals see issues after they become serious | Real-time or scheduled exception alerts |
| Limited student context | Teachers see marks or attendance but not complete evidence | Holistic Learner Record |
| Untracked communication | Parent messages are sent but outcomes are unclear | Approval, delivery, follow-up, and audit trail |
| Compliance burden | Evidence is scattered before inspections | Compliance workspace and evidence packs |
| ERP limitations | Existing systems record transactions but do not coordinate action | AI-assisted workflow engine |

The product should not position itself as “another ERP.” It should be positioned as an **AI operations layer and school intelligence system** that can initially work with an existing ERP, Excel exports, and school documents.

***

## Product vision

> **SchoolOS helps schools turn scattered records into verified answers, owned actions, parent communication, and evidence-backed student support.**

It is not just a database and not just a chatbot. It has five connected layers:

1. **Operational system of record** — attendance, assessments, fees, enrolment, staff, classes, workflows.
2. **Knowledge system** — circulars, policies, forms, meeting notes, reports, evidence documents.
3. **AI intelligence layer** — permission-aware search, summaries, analysis, drafting, and alerts.
4. **Action layer** — tasks, approvals, escalations, interventions, outcomes.
5. **Holistic Learner Record** — verified student context across academics, attendance, support, and follow-up.

***

## What SchoolOS should do

### Core operational capabilities

| Module | What it should handle |
|---|---|
| Identity and access | Users, staff roles, parent-child relationships, MFA, permissions |
| Student information | Admissions, profiles, guardians, class history, consent records |
| Attendance | Daily attendance, absence reasons, escalation, follow-up |
| Academics | Subjects, assessments, marks, competency evidence, report cards |
| Fee operations | Fee heads, invoices, receipts, dues, concession workflow |
| Parent communication | Notices, templates, approvals, delivery, responses, follow-up |
| Documents | Uploads, classification, retention, search, citations |
| Workflows | Tasks, owners, deadlines, approvals, outcomes |
| Compliance | Requirements, evidence, deadlines, inspection packs |
| Reporting | Principal dashboards, staff worklists, audit reports |
| AI workspace | Permission-aware queries and cited AI summaries |

### First high-value workflow

The initial MVP should not try to solve everything. It should focus on one workflow with obvious daily value:

```text
Attendance recorded/imported
  → threshold/rule detects concern
  → class teacher or admin reviews evidence
  → system drafts approved parent communication
  → communication is sent and recorded
  → follow-up task is assigned
  → intervention/outcome is recorded
  → principal sees unresolved cases
```

This is a strong initial use case because it connects operational data, parent communication, task ownership, auditability, and AI assistance without requiring the platform to immediately replace every ERP module.

***

## Holistic Learner Record

The Holistic Learner Record is a key SchoolOS differentiator. It should not be a hidden behavioural-score system.

It should present a student in a responsible, evidence-backed way:

| Information type | Meaning | Example |
|---|---|---|
| Verified record | Objective operational fact | 72% attendance in Term 1 |
| Academic evidence | Marks, competency data, completed work | Low scores in geometry assessments |
| Human observation | Dated, attributed teacher note | “Often leaves geometry work incomplete” |
| AI insight | Non-authoritative pattern for review | “Three recent results suggest a possible geometry gap” |
| Action | Approved intervention | Two-week remedial plan |
| Outcome | Measured follow-up result | Improved score in subsequent assessment |

The record may include:

- Student identity and enrolment information.
- Attendance and punctuality history.
- Academic and competency evidence.
- Assignment or work-completion references.
- Teacher observations with author and date.
- Parent meeting notes and action items.
- Intervention plans and owners.
- Measured outcomes.

It must not include:

- Unsupported personality labels.
- Staff gossip.
- Hidden “risk scores.”
- Irrelevant family data.
- Unverified sensitive claims.
- AI-generated statements presented as facts.
- Automated predictions about a child’s future, intelligence, behaviour, or ability.

Because SchoolOS processes children’s data, this design must follow a minimisation and human-review mindset. India’s DPDP Act and notified 2025 Rules create an operational framework for responsible personal-data processing, including child-data protections and phased compliance. [static.pib.gov](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf)

***

## AI strategy

AI should be a **governed assistant**, not an autonomous decision-maker.

### Good AI use cases

- Summarise a student’s authorised attendance and academic history before a meeting.
- Explain which source records caused an attendance or academic alert.
- Draft a teacher-reviewed parent message.
- Turn meeting notes into tasks and follow-up actions.
- Search policies, circulars, and compliance documents.
- Generate evidence-backed management summaries.
- Identify records that require staff review.
- Draft inspection or compliance packs from source documents.

### AI actions that require approval

- Sending messages to parents.
- Changing student records.
- Creating discipline actions.
- Assigning grades.
- Granting fee concessions.
- Publishing student-specific conclusions.
- Revealing sensitive records.
- Making admissions, promotion, or counselling decisions.

### AI safety rules

- Every answer involving school facts must show citations.
- AI retrieves only data the current user is authorised to see.
- AI should state when it cannot verify something.
- AI must not invent attendance, marks, fees, or student history.
- Sensitive information should be minimised before external model calls.
- Customer school data must not train AI models by default.
- Model prompts, responses, and retention must be controlled.
- Documents are untrusted input and may contain malicious prompt-injection instructions.

OWASP’s RAG security guidance recommends securing the full chain—from document ingestion and embeddings through retrieval, generation, and downstream actions—rather than treating a vector database as inherently safe. [cheatsheetseries.owasp](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)

***

## The deployment decision

There are three practical models.

| Model | Where it runs | Best fit | Main benefit | Main drawback |
|---|---|---|---|---|
| Shared multi-tenant SaaS | Your managed cloud platform | Most small/mid-sized schools | Lowest operating cost and fastest updates | Must enforce strong tenant isolation |
| Dedicated managed tenant | Separate environment managed by you | Premium schools, chains | Stronger infrastructure isolation | More DevOps work per school |
| Customer-hosted deployment | School-owned VPS/cloud, managed by you | Schools requiring ownership/control | School owns infrastructure/data environment | Highest operations complexity |

### Recommended strategy

Build **one portable, tenant-aware SaaS product**:

- Shared SaaS is the default product.
- Dedicated VPS/cloud is a paid enterprise option.
- Both modes run the same versioned container image.
- Both use the same application code.
- Schools differ through data, settings, templates, enabled modules, and integrations—not code branches.

AWS guidance frames this as a trade-off: pooled environments reduce cost and simplify operations, while siloed/dedicated environments provide stronger isolation but increase management complexity. [docs.aws.amazon](https://docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/saas-tenant-isolation-strategies.pdf)

***

## The critical rule: no code forks

Do not create this:

```text
school-a branch
school-b branch
school-c branch
```

Do not create this:

```ts
if (tenantId === "school-a") {
  // special permanent code
}
```

Instead, use this:

```text
One SchoolOS codebase
  ├── Shared release version
  ├── Tenant-specific configuration
  ├── Tenant-specific data
  ├── Feature flags
  ├── Report/message templates
  ├── Integration settings
  └── Optional add-on modules
```

For example:

```text
School A
- CBSE grading
- English + Telugu
- Attendance threshold: 75%
- Attendance + fees + learner record enabled

School B
- ICSE grading
- English + Hindi
- Different report-card template
- Attendance + transport + admissions enabled
```

Both should still run:

```text
SchoolOS application image: v1.2.0
```

A bug fix built because School A found a defect should be tested, versioned, and deployed to School B as part of the common product release if relevant.

***

## Multi-tenant data model

Every school-owned record must carry tenant context.

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

Tenant boundaries must exist across all infrastructure layers:

| Layer | Isolation requirement |
|---|---|
| Login/session | Verified tenant context |
| API | Tenant derived server-side |
| Database | `tenant_id`, scoped queries, Row-Level Security where practical |
| Storage | Tenant-specific object prefixes/buckets |
| Vector search | Tenant/role metadata filters on every retrieval |
| Cache | Tenant-prefixed cache keys |
| Jobs | Tenant ID and re-authorisation in workers |
| Logs | Tenant identifiers with personal-data minimisation |
| Backups | Encrypted and segmented |
| Support | Audited, temporary elevated access |

Never allow browser clients to submit a tenant ID and trust it. Derive tenant identity from the authenticated session and/or the verified school domain.

***

## Configuration strategy

School configurations are not school-specific code.

```text
school configurations
├── school-a configuration
├── school-b configuration
└── school-c configuration
```

These configurations can define:

- School name, logo, colours, domain.
- Academic board and grading system.
- Terms, timetable labels, year structure.
- Attendance thresholds.
- Fee heads and instalments.
- Notification templates.
- Languages.
- Roles and permission scopes.
- Workflow approval chains.
- Report-card templates.
- Payment or biometric integrations.
- Enabled feature modules.

A good configuration hierarchy is:

```text
Core product defaults
  → Subscription plan defaults
  → School configuration
  → Academic-year configuration
  → Role/class/workflow configuration
  → Individual user preferences
```

***

## Customisation policy

Every customer request should be classified before development.

| Request | Example | How to handle it |
|---|---|---|
| Core feature | Attendance, audit logs, role permissions | Build once for all schools |
| Configuration | Different grading scale | Configure it |
| Template | Report-card format | Build template capability |
| Integration | Payment provider or biometric reader | Build connector interface, charge setup |
| Add-on | Transport or hostel | Versioned optional module |
| Bespoke workflow | One school’s special process | Paid extension, isolated from core |
| Permanent fork | “Change core system only for us forever” | Decline or reframe as paid module |

Use this decision rule:

> **If three schools could need it, productise it. If one school needs it, configure it first. If configuration cannot solve it, charge separately and isolate it as an extension.**

***

## Release and update strategy

Every SchoolOS environment must run a named version:

```text
SchoolOS v1.0.0
SchoolOS v1.0.1
SchoolOS v1.1.0
```

Use semantic versioning:

| Version | Meaning |
|---|---|
| Patch: `1.0.1` | Security or bug fix |
| Minor: `1.1.0` | Compatible new feature/module |
| Major: `2.0.0` | Breaking change requiring migration |

For every school deployment, track:

- Application version.
- Database schema version.
- Configuration version.
- Enabled features.
- Migration status.
- Previous stable release.
- Backup snapshot before update.
- Deployment date and operator.

### Release pipeline

```text
Developer machine
  → Shared development
  → Staging
  → Internal demo tenant
  → Pilot school
  → Progressive customer rollout
```

Never deploy directly from a developer machine to all schools.

### Release workflow

1. Build and review code.
2. Run automated tests and security scans.
3. Build immutable Docker image.
4. Deploy to staging.
5. Test migration and rollback.
6. Release internally.
7. Deploy to one pilot school.
8. Monitor errors and staff feedback.
9. Gradually deploy to other schools.
10. Publish release notes.
11. Retire old feature flags later.

***

## Database migration strategy

Database changes are often the most dangerous part of updates. Use:

```text
Expand → Migrate → Contract
```

1. **Expand:** Add compatible fields/tables without deleting old ones.
2. **Migrate:** Deploy code supporting both old and new structures.
3. **Backfill:** Move historical data in controlled background jobs.
4. **Switch:** Enable new workflows through feature flags.
5. **Validate:** Confirm accuracy and performance.
6. **Contract:** Remove old structures only after all schools are stable.

Never run destructive database operations without:

- Verified encrypted backup.
- Tested restore procedure.
- Explicit migration plan.
- Known rollback or forward-fix plan.
- Audit record.

***

## Feature flags

Feature flags allow one code release but controlled activation.

Example:

```text
FEATURE_ATTENDANCE_ALERTS=true
FEATURE_CBSE_REPORT_CARD=true
FEATURE_TRANSPORT_MODULE=false
FEATURE_AI_MEETING_SUMMARY=true
FEATURE_NEW_ADMISSIONS_FLOW=false
```

Use feature flags to:

- Launch a module to only one pilot school.
- Turn off a faulty feature quickly.
- Offer paid add-ons.
- Test features safely.
- Avoid code forks.
- Roll out changes gradually.

Flags should have an owner and expiry/review date, otherwise they become long-term technical debt.

***

## Control plane

You need an internal **SchoolOS Control Plane** or operations console.

Initially, it can be an admin dashboard plus deployment scripts. Later, it should manage:

- Tenant registry.
- Deployment mode.
- Domain and certificate status.
- App/schema versions.
- Feature flags.
- Environment health.
- Backup status.
- Migration status.
- Rollback controls.
- Secret rotation.
- Support contacts.
- Contract plan and renewal details.

The control plane should manage infrastructure and release metadata without exposing student content by default.

***

## Recommended initial tech stack

For the first 1–3 schools:

```text
Frontend/API: Modular monolith
Runtime: Docker containers
Deployment: Docker Compose
Server configuration: Ansible
Infrastructure provisioning: OpenTofu/Terraform equivalent
Database: PostgreSQL
Queue/cache: Redis
Files: Encrypted versioned S3-compatible object storage
Background work: Worker container
AI: Internal provider gateway abstraction
Monitoring: Central error/uptime/resource monitoring
CI/CD: GitHub Actions or equivalent
```

Avoid Kubernetes at the beginning. It introduces operational complexity before you have enough customers or traffic to justify it.

Infrastructure-as-code tools such as OpenTofu help define and manage infrastructure through versioned configuration rather than manual setup. [opentofu](https://opentofu.org/)

***

## Security baseline

### Identity

- MFA for principals, admins, finance staff, and support operators.
- Strong password hashing.
- Short session expiry.
- Token rotation.
- Staff offboarding process.
- Separate human and machine credentials.

### Application security

- Input validation.
- Output encoding.
- CSRF protection where relevant.
- Rate limiting.
- Secure HTTP headers.
- Dependency scanning.
- Secret scanning.
- Static code analysis.
- Penetration tests before meaningful scale.

### Encryption

- TLS in transit.
- Encryption at rest for databases, documents, and backups.
- Short-lived signed document URLs.
- Separate secrets per school/environment.

### Logging

Audit at least:

- Login and MFA events.
- Permission changes.
- Data changes.
- Sensitive record views.
- Downloads and exports.
- AI requests/retrieval metadata.
- Support access.
- Deployments.
- Migrations.
- Backup and restore operations.

***

## Backup and disaster recovery

Minimum baseline:

- Daily encrypted database backups.
- Document backup outside the production VPS.
- Versioned storage.
- Separate failure domain where possible.
- Backup alerts.
- Monthly restore drills.
- Documented recovery runbook.
- Disk/database/certificate monitoring.

Initial realistic targets:

| Metric | Target |
|---|---:|
| Recovery Point Objective | Up to 24 hours |
| Recovery Time Objective | 4–8 business hours |
| Backup verification | Daily |
| Restore test | Monthly |

Do not promise enterprise-grade availability targets before you have tested operations and staffing to support them.

***

## Privacy and compliance

SchoolOS will process significant child and family data. Treat privacy as a product requirement, not paperwork.

Required design principles:

- Data minimisation.
- Purpose limitation.
- Role-based access.
- Sensitive-record segmentation.
- Consent/notice record support.
- Controlled exports.
- Retention rules.
- Deletion and archival rules.
- Incident response process.
- Data portability.
- Vendor/subprocessor tracking.

The DPDP Rules, 2025 were notified in November 2025 and provide for phased compliance. SchoolOS should obtain legal review before production deployment and should not represent itself as providing legal compliance automatically. [pib.gov](https://pib.gov.in/PressReleasePage.aspx?PRID=2190655&reg=3&lang=2)

***

## Commercial model

| Revenue component | Suggested model |
|---|---|
| SchoolOS subscription | Monthly or annual recurring fee |
| Onboarding | One-time implementation, data migration, training |
| Dedicated VPS deployment | Premium setup plus monthly managed-operations fee |
| Infrastructure | Prefer direct billing to the school for school-owned VPS/cloud |
| Integrations | Setup fee and pass-through vendor costs |
| Custom work | Fixed fee plus ongoing maintenance agreement |
| Support SLA | Higher plans pay for faster response and governance |

The school should own its operational data. You should own SchoolOS source code, reusable modules, generic product improvements, and platform IP unless a contract states otherwise.

***

## Go-to-market plan

### Best first customer profile

Target private schools with:

- 500–2,000 students.
- Existing spreadsheet/ERP pain.
- A principal or owner who feels reporting and coordination burden.
- Willingness to pilot one workflow.
- Adequate IT comfort but no internal software team.
- A strong need for attendance, parent communication, academic analysis, or compliance support.

### Initial sales pitch

> **“SchoolOS works with your existing records and ERP exports. It turns scattered school information into verified answers, approval-ready actions, and inspection-ready evidence—without forcing you to replace everything on day one.”**

### Pilot offer

The pilot should be limited and measurable:

- One school.
- One term or 8–12 weeks.
- One defined workflow.
- One or two roles.
- Clear success metrics.
- Controlled data import.
- Agreed implementation owner from school.
- Feedback meeting every two weeks.

### Pilot success metrics

- Hours saved per week for administrative staff.
- Time from attendance concern to parent follow-up.
- Percentage of unresolved cases.
- Parent message delivery and response rates.
- Data-import accuracy.
- Teacher adoption.
- Principal usage.
- Support requests per active user.
- AI citation accuracy and approval rates.

***

## MVP roadmap

### Phase 0: Discovery and validation

- Interview several schools.
- Identify urgent workflow pain.
- Create realistic synthetic demo.
- Validate willingness to pay.
- Define privacy and contract requirements.

### Phase 1: Foundation

- Authentication and tenants.
- Roles and audit logs.
- Student/class/staff records.
- Attendance and assessment import.
- Basic documents.
- Configuration.
- Dockerised app and backup baseline.

### Phase 2: Action layer

- Attendance escalation.
- Parent communication.
- Task ownership.
- Outcome tracking.
- Learner Record v1.
- Principal dashboard.

### Phase 3: AI layer

- Permission-aware search.
- Cited summaries.
- Meeting preparation.
- AI drafts with approvals.
- Prompt-injection controls.
- AI cost limits.

### Phase 4: Pilot hardening

- Data import process.
- Restore testing.
- Tenant-isolation tests.
- Control-plane baseline.
- Feature flags.
- Support processes.
- Release notes.

### Phase 5: Enterprise readiness

- Repeatable dedicated VPS deployment.
- Automated deployment/rollback.
- Central health monitoring.
- Customer-owned infrastructure support.
- Advanced integrations and modules.

***

## Final strategic recommendation

Build SchoolOS as a **portable, tenant-aware SaaS product**.

- Start with a narrow, painful workflow rather than a full ERP replacement.
- Keep operational data in relational records as the source of truth.
- Use AI as an evidence-backed assistant, not an autonomous decision-maker.
- Make multi-tenant SaaS the default.
- Sell dedicated VPS/cloud environments as a premium option.
- Use one codebase and one release pipeline.
- Let schools differ by configuration and modular extensions, never permanent forks.
- Build security, privacy, child-data protection, backup, audit, and data portability into the product from the beginning.

This approach allows you to validate quickly with Indian schools while preserving a scalable product business rather than becoming a custom software agency.
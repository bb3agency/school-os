# ADR-0009: Host on AWS in India

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) · Amended by [ADR-0015](ADR-0015-deployment-and-commercial-model.md) (dedicated-tier EC2 hosts in ap-south-1) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

Schools and parents expect children's data to stay in India. CERT-In directions require ICT logs to be kept for 180 days within India, and DPDP Rules require at least one year of logs (08 §6). A solo founder needs managed services (database, keys, identity, backups) rather than self-run infrastructure. A second region inside India is needed for backups and disaster recovery.

## Decision

- Primary region: **AWS ap-south-1 (Mumbai)**. Backups and disaster-recovery copies: **ap-south-2 (Hyderabad)**.
- Managed services: ECS Fargate (or a single container host at Stage 0), RDS PostgreSQL, ElastiCache, S3, KMS, Secrets Manager, Cognito, CloudWatch, WAF (10 §4).
- AWS Organizations with separate `staging` and `prod` accounts (plus `log-archive`, `security` and `backup` accounts from Stage 1). SCPs deny actions outside ap-south-1/ap-south-2 (global services excepted), deny disabling CloudTrail/GuardDuty/Config, and deny public S3 (07 §13; 10 §2).
- Infrastructure is defined in Terraform; no manual console changes in staging or production.
- Security, access and application logs are kept 400 days in ap-south-1.

## Consequences

- Good: data residency in India for storage and backups; managed security services; mature Terraform support.
- Good: a second Indian region gives DR without leaving the country.
- Bad: some services cost more in ap-south-1/2 than in larger regions; NAT gateway is a notable fixed cost at Stage 0 (10 §3).
- Bad: sub-processors outside India (LLM, possibly embeddings/OCR) still exist and are disclosed (08 §1).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Azure or GCP India regions | Viable; AWS chosen for team familiarity, Cognito, and two Indian regions with mature services |
| Indian hosting provider / colocation | More operational work; fewer managed security services |
| On-premise at each school | Patching, backups and physical security impossible to guarantee; rejected again in ADR-0015 |

## Related requirements

NFR-PRV-001, NFR-PRV-002, NFR-AVL-001..003, SEC-011, SEC-023, PRV-012, PRV-018; 04 §3; 07 §13; 08 §1, §6; 10 §1–5, §10.

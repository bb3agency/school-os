# Security Policy

SchoolOS handles children's personal data. We take reports seriously and appreciate responsible disclosure.

## Reporting a vulnerability
- Email: **security@<your-domain>** (replace before launch). Do not open public issues for security problems.
- Include: affected URL/endpoint, steps to reproduce, impact, and any proof-of-concept (use your own test accounts only).

## Our commitments
- Acknowledge within **2 business days**; initial assessment within **5 business days**.
- Keep you informed until resolution; credit you (if you wish) after a fix is released.
- Fix timelines: critical ≤ 7 days, high ≤ 30 days, medium ≤ 90 days.

## Rules of engagement (safe harbour)
- Test only against accounts and data you own; never access, modify or exfiltrate other users' or schools' data.
- No denial-of-service, social engineering, physical attacks, or automated high-volume scanning of production.
- Stop and report immediately if you encounter personal data.
- Good-faith research following these rules will not be pursued legally by SchoolOS.

## Scope
In scope: SchoolOS web app and API (shared tier), the platform admin panel (admin host) and its fleet heartbeat endpoint, dedicated-tier school hosts (including custom domains pointing at them), and the edge agent. Out of scope: third-party services (report to their owners), a school's own DNS or email, findings requiring physical access or compromised devices.

If you can reach student data from the platform admin panel, or one school's data from another school's account or host, treat it as critical and report it immediately.

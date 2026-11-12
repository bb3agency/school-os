# Demo school and demo script (sales kit)

| Field | Value |
|---|---|
| Version | 0.1 · 2026-11-12 (FA-12, feature wave F3) |
| Requirements | FR-DEMO-001..015 ([docs/03 §3.38](../03-TRD.md)) |
| Audience | The SchoolOS owner (selling), association partners who present SchoolOS |
| Data | **Synthetic only** (CLAUDE.md invariant 11). The demo school "Sunrise Synthetic Demo School" and every person in it are made up by the generator. Never show, type or import a real school's data in a demo. |

This kit is for the February to May 2027 buying window (budgets in February and March, decisions
in April and May; [docs/18 §1.9](../18-product-plan-2026-27.md)), when the AP SSC correction window is
also likely to open. It has two parts: how to make the demo school on your laptop, and a
10-minute walkthrough that follows the product's wedge (check the records before the portals,
fix them with evidence, answer from the school's own records).

## 1. Make the demo school (laptop only)

The demo runs on a developer laptop with the local stack (`make dev-host` or `make dev`). There
is no demo server: a demo on a hosted machine needs an ADR for a new `SOS_ENV` value and the
owner's decision on hosting money (D2), so it is not built (FR-DEMO-015).

| Command | What it does |
|---|---|
| `make seed-demo` | Creates the demo school, or completes it if it exists. Safe to re-run. |
| `make demo-reset` | Deletes the demo school (the same purge as an offboarded school) and creates a fresh copy with the next code (`demo-g2-a`, `demo-g3-a`, ...). Run it before every demo so you start from the same state. |
| `make seed-demo DEMO_ARGS="--no-documents"` | Faster: no register-page images or circulars (the Ask and extraction parts of the script then have nothing to show). |

Both commands refuse to run unless `SOS_ENV` is `local` or `ci`, exactly like
`make seed-synthetic`. They print one JSON line with codes and counts; the school's code is in
`tenants[0].code`.

Sign in at `http://localhost:3000/dev/sign-in` and type a subject of the form
`synthetic|<code>|<role>|1`, for example `synthetic|demo-g1-a|principal|1` or
`synthetic|demo-g1-a|office_admin|1`. Roles you will use: `owner`, `principal`, `office_admin`,
`accountant`, `class_teacher`.

### What the demo school contains

| Area | What is there | Where to show it |
|---|---|---|
| School | "Sunrise Synthetic Demo School", boards AP State Board (BSEAP) and CBSE, years 2026-27 (current) and 2025-26, Nursery to XII | Settings |
| Staff | One person for every role, all with `.example.invalid` e-mails | Users and roles |
| Students | 400 students with guardians, enrolments and register, Aadhaar-as-printed, UDISE+ and board values; a known share of mismatches planted (spellings, initials, date of birth, missing fields) | Students, Findings |
| Findings | One full data-quality run, so every planted mismatch is a finding | Findings, Readiness |
| SSC readiness and UDISE+ follow-ups | Verification slips recorded as given to four parents; two UDISE+ correction requests "sent" 20 days ago, so they show as overdue | Findings → Readiness → Follow-ups, UDISE+ to-do |
| Fees | Class X plan (two terms), receipts, one cancelled, one part refunded | Fees |
| Earlier-year dues | Six of today's Class X students owe their 2025-26 Class IX fees | Fees → Dues ("Earlier years only"), the counter's "Earlier years" panel |
| Add-on fees | Transport Band 1 and Band 2 and a Robotics club fee on picked Class X students | Fees → Add-ons |
| Compliance certificates | Building safety (valid), fire NOC (expires in 3 weeks), water safety (expired), trust registration (no expiry); every number says DEMO | Compliance |
| School calendar | Sundays and second Saturdays off, Dasara and Sankranti holidays, Republic Day, one extra working day (demo dates, not an official calendar) | Settings → Structure → Calendar |
| CBSE disclosure | A draft with a few fictional values; nothing published | Settings → Disclosure |
| Weekly brief | Last week's management brief | Brief (owner, principal) |
| WhatsApp | Only when messaging is switched on locally (`SOS_MESSAGING_ENABLED`): settings with every switch off and no WhatsApp connection, so nothing is ever sent | Settings → WhatsApp |

Not in the demo school yet: exam marks and the year-end results (FT-08), so the year-end
progression pages are empty (follow-up).

## 2. The 10-minute walkthrough

Start with `make demo-reset`, open the browser at 1366×768 or larger, and sign in as the office
admin. Say once, at the start: "Everything you see is made-up sample data."

| Minute | Show | Say (in your own words) |
|---|---|---|
| 0-1 | Home page as the office admin | The office enters a student once; SchoolOS checks it against the other sources before any portal deadline. |
| 1-2 | **Imports** | Schools start from the spreadsheet they already have; an import can be undone for 24 hours. (Show the screen; do not upload a real file.) |
| 2-4 | **Findings → Readiness** (SSC 2027) | Which Class X students are ready for the board portal and which are not, and why. Open one student: the register says one spelling, Aadhaar-as-printed another. |
| 4-5 | **Follow-ups** | Who must fix it: the parent (Aadhaar update) or the MEO (UDISE+). Slips already given; the two UDISE+ requests are overdue after 14 days. Print a parent slip. |
| 5-6 | **A change with evidence** | Correct a field only through a change request with an evidence document; another person approves it. SchoolOS never auto-corrects an official record. (Do it live with a synthetic student.) |
| 6-7 | **Ask the school** (sign in as principal) | Ask a question about a circular; the answer cites the passages it used, or says it was not found. On a laptop the answers come from the local stand-in provider unless a real one is configured with a service identity. |
| 7-8 | **Fees → Dues** (sign in as accountant) | This year's dues, earlier-year dues first at the counter, add-on transport bands, maker-checker for cancellations and refunds. The school is always the merchant; SchoolOS never holds money. |
| 8-9 | **Compliance** and **Settings → Disclosure** | The fire NOC expires in three weeks and the reminder goes to its owner; the expired water certificate warns before the CBSE disclosure is published. |
| 9-10 | **Brief** (sign in as owner) | What management reads every Monday: counts only, no student names. Close with the pricing page and "a demonstration on your own sample sheet". |

### Rules for presenters

- Never type, upload or photograph real student data, not even "just to show". If a prospect
  offers a real sheet, decline and offer a pilot with a signed data processing agreement.
- Do not promise prices, dates or features beyond the public pricing and features pages. Plans
  and prices are in the web catalogue (`apps/web/src/features/marketing/catalogue.ts`) and on
  `/pricing`; quote only what those pages say.
- Do not connect a real WhatsApp number or a personal AI account to the demo.
- Run `make demo-reset` after the demo, so the next one starts clean.

## 3. Handouts and pages not built

The research report (docs/research/2026-11-features/ai-platform.md, FA-12) also proposes a demo
deployment (`SOS_ENV=demo`, needs an ADR and the owner's hosting decision), a "Board readiness"
landing page, a DPDP page, a price calculator on `/pricing` and association "compliance clinic"
handouts. None of these are built in wave F3; they are follow-ups for the lead and the owner.

# Feature research: parents and communication (FP)

| Field | Value |
|---|---|
| Date | 2026-11-09 |
| Area | Parents and communication: WhatsApp utility messages, parent link pages, notices and circulars, parent-teacher meetings, consent collection, absence alerts, report card delivery, parent grievance intake, Telugu, parent self-service |
| Modules read | `apps/api/app/messaging` (+ `sends/`), `notifications`, `circulars`, `public`, `privacy` (requests, consents), `core/languages.py`, `insights/rules.yaml`; web `apps/web/src/app/(public)`, `apps/web/src/features/{messaging,public,consents,privacy,circulars}` |
| Status | Research only. Nothing here is decided. Each proposal names its BRD objective and user (docs/14 §5). Anything that adds a public write surface, a new data class or a sub-processor is marked "ADR + privacy review". |

## 1. What exists today (short inventory)

| Capability | State | Where |
|---|---|---|
| WhatsApp from the school's own number (Meta Cloud API, school pays Meta) | Built (R2), shared tier only | ADR-0043; `messaging/config.yaml`; D24 |
| Templates | 5 utility purposes only: `optin_confirm`, `school_notice`, `absence_alert`, `fee_reminder`, `fee_receipt` | `messaging/templates.yaml:28-102` |
| Opt-in | Per guardian and category (`notices`, `attendance`, `fees`), JOIN slip, signed form, in person; STOP honoured silently | `messaging/config.yaml` `optin`; ADR-0043 dec. 7; D32, D35 |
| Notice broadcasts | Approved M4 notice → chosen sections → preview with skips and estimated Meta cost → confirm; delivery counts (queued/sent/delivered/read/failed/skipped) | `messaging/sends/schemas.py:52,111`; US-2204 |
| Quiet hours | 21:00-07:00 for notices; absence alerts and receipts until 20:00; no override | `messaging/config.yaml:65-67`; ADR-0043 dec. 10 |
| Absence alerts | Once a day at the school's time, `absent` only, child's first name and date | ADR-0043 dec. 11; `templates.yaml:73` |
| Consecutive-absence follow-up | Staff-side insight (3 days, AP practice), not linked to parents | `insights/rules.yaml:15-20` |
| Parent link pages | Read-only notice, receipt, dues (Pay), opt-in; 30/90/7/7-day tokens; no login; "no page lets the parent change any record" | ADR-0046 line 22; `public/config.yaml`; `public/public_routes.yaml` |
| Inbound replies | No inbox; keywords only; one auto-reply a day pointing to the office phone; text not stored | ADR-0043 dec. 9 |
| Optional-purpose consents | Register exists; collected on **printed** forms only | `privacy/consents_api.py:161`; `consent_purposes.yaml` |
| DPDP requests and grievances | One register with a 7-day due date; **staff log them**; verification `in_person_document`, `guardian_phone_on_record`, `staff_sign_in` | `privacy/schemas.py:20-30`; ADR-0048 |
| Privacy notices | A record that a notice was issued and by which channel (`paper`, `school_whatsapp_group`, `sos_notice`, ...); not tied to a broadcast | `privacy/schemas.py:30`, `privacy/models.py:37` |
| Parent language | English or Telugu per school, per-guardian override; **every Telugu template still `reviewed: false`** (9 entries) | ADR-0044 dec. 6; `templates.yaml`; `core/parent_texts.yaml` |
| Report cards to parents | Absent (marks exist in `academics`; no report card output, no parent page shows marks) | ADR-0046 line 22 |
| Parent-teacher meetings | Absent (only a synthetic PTM circular in `devtools/corpus.py:128`) | |
| Parent self-service (phone change, certificate request) | Absent; parents call or visit the office | ADR-0046 line 41 (parent login rejected) |

## 2. Current external facts that matter (checked 2026-11-09)

1. **Meta per-message pricing** since 1 Jul 2025. **From 1 Oct 2026** utility templates sent inside an open customer-service window are **no longer free**, and **service (free-form) messages beyond 1,000 per business number per month are charged**. Meta may change rates only on 1 Jan, 1 Apr, 1 Jul or 1 Oct. India billing in INR since 1 Jan 2026; **non-INR accounts stop delivering on 1 Jan 2027** (migrate by 31 Dec 2026). Source: [Meta pricing docs](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing) (accessed 2026-11-09). India utility rate about ₹0.115 + GST, marketing about ₹0.8631 (secondary: [baat.ai](https://baat.ai/blog/whatsapp-api-pricing-india), [Monty Mobile](https://montymobile.com/blogs/whatsapp-business-api-pricing-in-india-inr-rates-gst-and-the-2026-currency-migration-deadline); **not verified against Meta's INR rate card**).
   - Effect on SchoolOS: ADR-0043 and `catalogue.py:17` treat `optin_stopped` and the auto-reply as "free inside the window". That stopped being fully true on 1 Oct 2026 (see FP-11).
2. **Meta barred general-purpose AI chatbots** on the Business API from 15 Jan 2026; business-specific support bots remain allowed ([TechCrunch](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform/), [respond.io](https://respond.io/blog/whatsapp-chatbot-policy-2026); secondary, policy text not read).
3. **DPDP Rules 2025**: notified 13/14 Nov 2025; consent managers one year later (about **13 Nov 2026**, sources conflict); notice (Rule 3), children's consent (Rule 10), rights and grievances (Rules 13-14) from **13 May 2027**. Rule 14 requires the fiduciary to **publish on its website or app the means** by which a data principal makes a request, and to answer grievances within a published period of **at most 90 days** ([Mondaq](https://www.mondaq.com/india/data-protection/1708164/digital-personal-data-protection-rules-2025-notified), [MediaNama](https://www.medianama.com/2025/11/223-rights-data-principals-dpdp-rules-2025/), [PIB summary](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf); Gazette G.S.R. 846(E) not read directly).
4. **AP Mega PTM.** Mega PTM 2.0 on 10 Jul 2025 was ordered for government **and private aided and unaided** schools (61,000+ institutions), with Holistic Progress Cards discussed one-to-one; Mega PTM 3.0 on 5 Dec 2025 (government schools) ([The News Minute](https://www.thenewsminute.com/andhra-pradesh/andhra-pradesh-gears-up-for-mega-ptm-with-record-participation-on-july-10-2), [Hans India](https://thehansindia.com/andhra-pradesh/mega-ptm-20-to-be-held-across-state-on-july-10-984378); secondary). Whether a 2026-27 Mega PTM covers private schools is **not verified**.
5. **Short-notice closures.** Cyclone Montha (Oct 2025): District Collectors closed government, aided and **private** schools by orders dated the evening before (for example Kakinada, order dated 26-10-2025); later extended to 31 Oct ([Careers360](https://news.careers360.com/cyclone-montha-andhra-pradesh-odisha-schools-closed-colleges-today-tomorrow-holiday-storm-nears-coast-imd-warning-updates/amp), [JNTUK notice](https://jntuk.edu.in/jntuk_uploads/notification/3964331761470533506.pdf)).

## 3. Proposals

### FP-01 · Opt-in coverage and notice reach follow-up
- **Users:** office admin, class teacher, principal.
- **Problem:** WhatsApp only helps if most families are opted in and actually open the message. Today a broadcast shows skips by reason (`messaging/sends/schemas.py:77,111`) and parent pages count views (`public/models.py:44-46`), but nobody gets a **list of the families who were not reached** (no opt-in, no phone, failed, delivered but never opened) to act on. The market research names "whether parents read WhatsApp notices" as unknown (docs/18 §5).
- **Exists:** per-broadcast counts; per-guardian opt-in state; join slips per section (`messaging/slips.py`).
- **Proposal:**
  - Section-level coverage card: % of students with at least one opted-in guardian per category, with "Print slips for the missing ones".
  - Per broadcast: "Not reached" list per section for the class teacher (child name, guardian relation, reason; no phone number shown, staff use the existing reveal-with-audit).
  - "Not opened after 48 h" (Meta `read` or page view) as a follow-up list, never an automatic resend.
  - Principal tile: coverage trend by week.
- **BRD objective:** BO-07 (repeatable onboarding), P5 (fewer counter questions). **Invariants:** 3 (class teacher sees own sections), 5 (no phones in logs). No ADR.
- **Dependencies:** none. **Effort:** S. **Value:** H — every other parent feature depends on reach. **Risks:** pressure on staff to "chase" parents; keep it a list, not a score. **Open question:** may class teachers see the not-reached list, or only office staff?

### FP-02 · Urgent closure message (cyclone, rain, unrest)
- **Users:** principal, owner; parents.
- **Problem:** Collectors close schools by evening orders (fact 5). Today a closure needs a notice → approval → section picker → preview, and notices wait out quiet hours (21:00-07:00) — a 22:00 order reaches parents at 07:00, when buses may already be running. No urgent path exists (`grep urgent` finds nothing in messaging/circulars).
- **Exists:** notice broadcasts, quiet-hours deferral (`messaging/sends/service.py:364`), daily cap.
- **Proposal:**
  - New utility template `school_closure` ("{{school}} will be closed on {{date}} because of {{reason}}. Details: button") with a short reason list (weather, government order, other).
  - Two-step send for `notice.approve` holders with step-up: whole school or chosen sections; the approval *is* the send (one person, audited, since they already hold approval rights).
  - May send during quiet hours until 22:30 and from 05:30 (configurable); never between.
  - Linked parent page shows the order text the principal typed; optional reopening message.
- **BRD objective:** P4 (short-notice circulars), BR-18 (utility only — Meta lists safety/essential updates as utility, to confirm at template review). **Invariants:** 7 (audit), 13 (template in config). ADR-0043 amendment (quiet-hours exception).
- **Effort:** S. **Value:** H for coastal AP schools; a visible, demo-able safety feature. **Risks:** misuse for non-urgent messages → limit to the closure template, count uses per term on the principal's screen. **Open question:** quiet-hours window for urgent sends.

### FP-03 · Digital consent on the parent link (optional purposes and event permission slips)
- **Users:** office admin, principal; parents.
- **Problem:** Optional-purpose consents (photos online, names in results, sharing with an outside organiser) are collected only on printed forms (`privacy/consents_api.py:161`). Paper forms are lost, and trip or competition permission slips are a weekly chore. From **13 May 2027** consent for non-educational purposes must be verifiable (fact 3).
- **Exists:** consent register with purposes and form versions (`consent_purposes.yaml`), guardian-bound parent tokens (ADR-0046), utility templates.
- **Proposal:**
  - New link kind `parent_consent` (7-14 days): shows the purpose text as the school wrote it, "I agree / I do not agree", the form version; one decision per guardian per purpose.
  - Evidence: token bound to the guardian's phone on record, timestamp, wording version, device-free (no IP stored beyond the existing rate budget).
  - Event permission = a consent purpose with an event name and an end date (reuses `share_outside_organiser`).
  - Template `consent_request` (utility, opt-in category `notices`), sent per section.
  - Withdrawal stays "write to the school" (staff record it), as today.
- **BRD objective:** BO-05, BR-10. **Compliance:** first **public write** besides opt-in → **ADR (amend ADR-0046, ADR-0049) + privacy review**; whether a phone-on-record link is "verifiable parental consent" under Rule 10 needs the D30 legal review (not legal advice). Not for APAAR (government form stays paper).
- **Dependencies:** FP-01 coverage. **Effort:** M. **Value:** H. **Risks:** a forwarded link lets another person decide → show the child's first name only and record which guardian; a decision can be corrected by the office. **Open question:** accept link consent for photos online, or require paper for that one?

### FP-04 · Parent request page for DPDP rights and grievances
- **Users:** parents (data principals), office admin, principal (grievance officer).
- **Problem:** Rule 14 (from 13 May 2027) asks the school to **publish the means** to make a rights request and to answer grievances within a published period ≤ 90 days (fact 3). Today only staff can log requests (`privacy/api.py:150`); the auto-reply tells parents to phone the office (ADR-0043 dec. 9). The school has no link to put on its website or notice.
- **Exists:** request/grievance register with 7-day due date, verification method `guardian_phone_on_record` (`privacy/schemas.py:28`), encrypted texts, opaque public keys with no expiry (`school_disclosure` pattern, ADR-0051).
- **Proposal:**
  - A per-school public "Privacy requests" link (opaque key, revocable, like disclosure) plus an entry from any parent link.
  - Form: request type (access, correction, erasure, withdraw consent, grievance, nominate), child's admission number and class, a short text (limit, Aadhaar mask applied), callback preference.
  - From a parent link: verification recorded as `guardian_phone_on_record`; from the public link: request lands as "verification pending" and staff verify in person or by calling the number on record.
  - Acknowledgement page with a reference number; status visible by reference on a later parent link.
  - Rate limits and WAF rules as `/p/`; no attachments in v1.
- **BRD objective:** BO-05, BR-08. **Compliance:** **ADR + privacy review** (public write, free text from unknown people); invariant 4 (mask), 5 (no text in logs). Matches D37/D38 (school answers, SchoolOS sends nothing by itself).
- **Effort:** M. **Value:** H, **time-sensitive (13 May 2027)**. **Risks:** spam and abusive text → captcha-free design needs per-IP and per-school budgets; text encrypted, shown only to `privacy.manage`. **Open question:** publish 7 days or a longer period as the school's stated period (the law allows up to 90)?

### FP-05 · Certificate requests by parents (bonafide, study, conduct, TC)
- **Users:** parents; office admin; principal.
- **Problem:** "Parents queue at the counter for certificates" is BRD pain P5 (`docs/01-BRD.md:41`); BO-04 targets < 5 minutes from request to print but the request itself still needs a visit. Bonafide and study certificates are needed for scholarships, bank accounts, bus passes and Aadhaar updates.
- **Exists:** certificates module with serials, verification codes, maker-checker where needed; dues page with Pay.
- **Proposal:**
  - On a parent link (or the FP-04 page): "Request a certificate" → type, purpose (list), copies.
  - Creates a request in the office queue; staff issue through the normal certificate flow (no automatic issue, invariant 6 untouched).
  - When ready: utility template `certificate_ready` ("collect from the office" or, for bonafide/study only, a 7-day link to the PDF with its verification code); TC always collected in person.
  - Optional certificate fee as a fee head paid through the existing dues link.
- **BRD objective:** BO-04, P5. **Compliance:** shares FP-04's ADR (public write). TC requests start the school's own withdrawal process; never block for dues (D16).
- **Dependencies:** FP-04 foundation. **Effort:** M. **Value:** H — the clearest "saves a trip" feature for parents and a counter-time saving for the clerk. **Risks:** a TC request by one parent in a custody dispute → TC requests only flag for a call-back, never queue issue. **Open question:** send bonafide PDFs on WhatsApp, or collection only?

### FP-06 · Contact update requests (new phone number) with re-opt-in
- **Users:** parents, office admin.
- **Problem:** Phone changes are common; today the office edits the guardian (`students/schemas.py:525`), which revokes links and needs a **new opt-in** (US-2202 note, `docs/02-PRD.md:683`). Parents who changed numbers silently drop out of every message.
- **Exists:** guardian PATCH with reveal audit, JOIN slips, blind index.
- **Proposal:**
  - Parent asks from the old number (parent link) or the office prints a "change of number" slip; the parent sends `JOIN <code>` from the new number.
  - The office sees a pending change (old number masked, new number from WhatsApp) and approves it; the change and re-opt-in happen in one transaction with audit.
  - Failed-delivery report (FP-01) feeds a "numbers to check" list.
- **BRD objective:** BO-05, BR-18. **Compliance:** guardian phone is C3; staff approval keeps invariant 6 spirit (no silent change); ADR-0043 amendment for a JOIN that proposes a number change. **Effort:** S-M. **Value:** M. **Open question:** may a JOIN from an unknown number ever change a record, even after approval? (Proposed: only after staff approval.)

### FP-07 · Report card delivery on a parent link
- **Users:** class teacher, exam coordinator, principal; parents.
- **Problem:** Term-end report cards are the most anticipated parent communication; today marks exist but no report card is produced or sent, and ADR-0046 forbids marks on parent pages (line 22). AP's PTMs centre on Holistic Progress Cards (fact 4).
- **Exists:** marks and exams (`academics`), PDF rendering via Chromium (`pdf` queue), parent link machinery.
- **Proposal:**
  - Link kind `parent_report` (30 days) showing one child's report card PDF (the school's approved layout), never other children.
  - Template `report_card_ready` (utility, opt-in category: new `academics` or reuse `notices`).
  - Published only after the exam coordinator locks marks and the principal approves release (ties to audit item YA-04 "exam lock", D83).
  - Optional "ask for a meeting" button feeding FP-08.
- **BRD objective:** P5 and BO-08 (parents engaged with weak students). **Compliance:** marks are student data on a public link → **ADR amending ADR-0046 + privacy review**; invariant 14 (no ranking messages to other parents).
- **Dependencies:** report card generation and exam lock (academics area; not built). **Effort:** M (delivery only). **Value:** H once report cards exist. **Risks:** forwarded links expose marks → short lifetime, first name only in the message, revocable. **Open question:** do schools want report cards over WhatsApp, or only handed over at the PTM?

### FP-08 · Parent-teacher meeting scheduling
- **Users:** principal, class teacher; parents.
- **Problem:** PTMs happen each term (and the state ran a Mega PTM including private unaided schools on 10 Jul 2025, fact 4); scheduling is done by phone and paper lists. Nothing exists (`grep ptm` finds only a synthetic circular).
- **Exists:** sections and class-teacher scopes, notices and broadcasts, parent links.
- **Proposal:**
  - A PTM event per section with slot length and teacher availability.
  - Template `ptm_invite` with a link where the parent picks a slot (or "cannot come"); reminder on the day.
  - Teacher's printable schedule; attendance of parents recorded (for the state's PTM returns, if asked).
  - No meeting notes from AI; a teacher's note is optional and staff-only.
- **BRD objective:** BO-08, P5. **Compliance:** public write (slot choice) → included in the FP-03/FP-04 ADR. **Effort:** M-L. **Value:** M (many schools do PTMs as open house without slots). **Open question:** slot booking or simple "PTM on date, please confirm attendance"? (The second is S.)

### FP-09 · Absence alert replies (structured, no inbox)
- **Users:** class teacher, office; parents.
- **Problem:** Parents reply to absence alerts ("he has fever", "I applied leave") and get an auto-reply; the reply is not stored (ADR-0043 dec. 9). The 3-day follow-up insight (`insights/rules.yaml:15-20`) then makes staff phone parents who already explained.
- **Exists:** absence alert template, inbound keyword handling, insight flags with actions.
- **Proposal:**
  - Quick-reply buttons on `absence_alert` v3: "On leave with the school's knowledge" / "Please call me". No free text stored.
  - The class teacher sees the reply next to the day's absence; "call me" becomes a task; the 3-day insight shows the replies.
  - Optional "late arrival" alert (`late` mark) as a separate school switch.
- **BRD objective:** BO-08, child safety (BR-10). **Compliance:** new inbound data (a reply code) → small ADR-0043 amendment; health reasons are not collected (buttons only). **Effort:** M. **Value:** M. **Risks:** a button answer misread as permission for leave → wording says "the school will check".

### FP-10 · Telugu return plan (parent-facing first)
- **Users:** principal, parents.
- **Problem:** Parent-facing Telugu is built (ADR-0044, US-2301..2303) but **every Telugu template and page text is `reviewed: false`** (9 template entries; `core/parent_texts.yaml`), and D23 forbids a Telugu school going live without a fluent reviewer. Research named local language as a tier-2 blocker (docs/18 §1.9) and parents' reading of English WhatsApp as unknown (docs/18 §5).
- **Proposal:**
  - Treat the Telugu review as a pilot task with a checklist: templates, parent pages, slips, consent and APAAR forms (the reviewer, the version reviewed, date in config).
  - Measure first: in the first pilot term, record each guardian's chosen language on the opt-in page and the open rate per language (FP-01 data).
  - Trigger for turning on more Telugu: ≥ 25 % of guardians in pilot schools choose Telugu (proposed threshold), then the staff UI stays English and **AI answers in Telugu stay off** until the live Telugu eval gates pass (docs/14 §3).
  - Do not add Urdu or Hindi until a school asks (see §5).
- **BRD objective:** BR-07, BO-06. **Compliance:** none new. **Effort:** S (engineering), plus a paid reviewer. **Value:** M-H for semi-urban State Board schools. **Open question:** who is the fluent reviewer, and by when?

### FP-11 · Meta pricing alignment and monthly spend view
- **Users:** owner, principal.
- **Problem:** Since 1 Oct 2026 Meta charges in-window utility messages and service messages above 1,000 per number per month (fact 1). ADR-0043 and `messaging/catalogue.py:17` still describe in-window texts as free; the broadcast estimate uses one configured rate (`messaging/config.yaml:105-106`). The school pays Meta directly (D7) and has no view of what it actually spent.
- **Exists:** status webhooks already store `billable` and `pricing_category` (`messaging/webhook.py:147-163`).
- **Proposal:**
  - A monthly "Messages and estimated Meta cost" card from stored pricing fields (utility, service, count by purpose).
  - Update docs and ADR-0043 wording; keep rates in config with a "rate checked on" date and a reminder to recheck each 1 Jan/1 Apr/1 Jul/1 Oct.
  - Before 1 Jan 2027: a banner for any connected school whose WABA is not INR (built check, FR-MSG-004) with the steps.
- **BRD objective:** BO-06 (trust in cost). **Compliance:** none. **Effort:** S. **Value:** M. **Time-sensitive:** INR deadline 31 Dec 2026.

### FP-12 · WhatsApp for dedicated-tier schools (signed relay)
- **Users:** owner of a dedicated-tier school.
- **Problem:** Dedicated schools get no WhatsApp (D24, ADR-0043 dec. 12) — the premium tier lacks a feature the cheaper tier has.
- **Proposal:** a relay in the shared deployment that forwards only that school's Meta events to its host over the signed fleet channel (ADR-0058 pattern); the host sends with its own token; the shared tier stores nothing beyond routing.
- **BRD objective:** BO-06. **Compliance:** ADR (touches ADR-0020/0047; the control plane must not read message content). **Effort:** M. **Value:** L-M until a dedicated school signs. **Open question:** build only when a dedicated-tier customer asks?

### FP-13 · Privacy notice delivery to every guardian, with evidence
- **Users:** principal, owner.
- **Problem:** From 13 May 2027 the school must give a notice (Rule 3). Today a privacy notice is a record of "issued by channel X" (`privacy/schemas.py:30`) with no link to who received it.
- **Exists:** notice records, M4 notices, broadcasts with per-guardian delivery status.
- **Proposal:** "Send this privacy notice on WhatsApp" creates an M4 notice from the approved privacy notice and broadcasts it; the privacy notice record keeps the broadcast id and the counts (delivered, opened, not reached); the not-reached list (FP-01) gets the paper copy.
- **BRD objective:** BO-05. **Compliance:** none new (utility template `school_notice`). **Effort:** S. **Value:** H, **time-sensitive (13 May 2027)**.

## 4. Ranked shortlist (value ÷ effort)

| Rank | Id | Why first |
|---|---|---|
| 1 | FP-01 Opt-in coverage and reach follow-up | S, H; every other parent feature depends on reach; no ADR |
| 2 | FP-02 Urgent closure message | S, H; real AP need (Cyclone Montha 2025), demo-able safety value |
| 3 | FP-13 Privacy notice delivery with evidence | S, H; DPDP notice duty 13 May 2027; no new surface |
| 4 | FP-04 Parent request page for DPDP rights and grievances | M, H; Rule 14 requires a published means by 13 May 2027; builds the public-write foundation |
| 5 | FP-05 Certificate requests by parents | M, H; BRD P5/BO-04; reuses FP-04's foundation |

Next: FP-03 digital consent (M, H, needs the legal read), FP-10 Telugu review (S + reviewer), FP-11 Meta spend (S, before 31 Dec 2026).

## 5. Not worth building (now)

| Idea | Why not |
|---|---|
| Native parent app or parent login (OTP) | Kept out of scope (docs/18 §3); parent apps are the most complained-about ERP feature (MyClassboard 2.4/5); links cover the need (ADR-0046) |
| Two-way chat inbox for the office | Out of scope (BRD §7.3); turns the clerk into a help desk, stores free text about children; FP-09 buttons give the useful part |
| WhatsApp "Ask the school" bot for parents | Meta allows business-specific bots, but grounding to one parent's own child needs parent identity, and invariant 8 plus a new AI surface need an ADR and evals; low value against the risk now |
| SMS fallback | D6 (no SMS, no DLT); revisit only if FP-01 shows low WhatsApp coverage |
| Teacher-to-parent class messages (homework, daily diary) | LMS/homework is out of scope; high volume would push the school's number into quality ratings |
| Marketing messages (admissions, events, camps) | BR-18, invariant 14; Meta bills them as marketing at about 7x the utility rate |
| Urdu or Hindi parent language | No evidence of demand yet; each language needs reviewers and template approvals; wait for a school to ask |
| Automated parent reminders from AI-written text | Templates must be pre-approved by Meta; AI drafting is already in M4 notices with staff approval |

## 6. Questions for the owner

1. FP-03/04/05/08 open **public write surfaces** (consent, requests, slot choice). Approve one ADR amending ADR-0046 ("no page lets the parent change any record") for request-type writes that staff must act on?
2. FP-02: may the principal send a closure message during quiet hours, and in which window?
3. FP-10: who is the fluent Telugu reviewer and by when; agree the 25 % trigger for more Telugu?
4. FP-07: report cards on WhatsApp links, or at the PTM only?
5. FP-04: which grievance period to publish (keep 7 days, or a longer stated period within the 90-day limit)?

## 7. Unverified claims

- India INR rates (₹0.115 utility, ₹0.8631 marketing) are from secondary sources; check Meta's INR rate card.
- The exact Meta chatbot policy wording (only secondary reports read).
- DPDP dates for Rule 4 (consent managers) conflict between sources; Gazette not read.
- Whether a 2026-27 AP Mega PTM covers private unaided schools.
- Whether a Meta reviewer will accept `school_closure`, `consent_request`, `ptm_invite`, `certificate_ready` and `report_card_ready` as utility (likely, to confirm at template submission).

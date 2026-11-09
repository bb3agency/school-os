# Portal calendar: yearly refresh checklist

| | |
|---|---|
| Status | Active (FO-05, 2026-11-09) |
| Requirements | FR-RDY-001, FR-RDY-011..020 (docs/03 §3.30) |
| Decisions | ADR-0053 and its amendment A1, ADR-0057 and its amendment A1, owner decision D3, lead decision D84 |
| Runbook | docs/11 §8 R14 |

The portal calendar (`apps/api/app/dq/config/portal_calendar.yaml`) gives every school the expected
dates of its portal and board windows. Dates move every year, so each window is an expectation
(`verified: false`) until a person at the school confirms it. This checklist says who reads which
circular in which month, so the shipped expectation is as close as possible before the first
reminder goes out (30 days before a window closes by default).

## Who does it

| Role | Does |
|---|---|
| Calendar owner (SchoolOS product lead) | Reads the sources in the table below each month, edits the calendar file and the billing copy, opens the PR. |
| Support lead | Asks pilot schools by phone or support ticket which dates their circulars gave. Never reads a school's confirmed dates from its data (the control plane has no access to tenant tables, invariant 1). |
| Reviewer (any engineer) | Checks the sources, runs the tests below, merges. |

## Month by month

Months are calendar months. "Watch" means: open the source at least once a week in that month.

| Month | Watch | Calendar events it affects |
|---|---|---|
| January | CBSE circulars on OASIS (school and teacher data, Class XI-XII teachers for on-screen marking) and the mandatory public disclosure; cse.ap.gov.in PSIS notices on recognition renewal; BSEAP notice of the nominal-roll correction window | `cbse_oasis_update_*`, `cbse_disclosure_update_*`, `ap_psis_recognition_*`, `bseap_ssc_*_nominal_roll` |
| February | CBSE SARAS opening for the next affiliation session; BSEAP nominal-roll window (usually only a few days); AP RTE 12(1)(c) admission notification | `cbse_saras_*`, `bseap_ssc_*_nominal_roll`, `ap_rte_*_round_1` |
| March | AP RTE lottery schedule; any CBSE note on the Class X second board exam | `ap_rte_*`, `cbse_second_exam_loc_*` |
| April | CBSE circular for the Class X second exam LOC (in 2026 it came one day before a 7-day window: confirm the same week); RTE round 1 results; UDISE+ progression opening | `cbse_second_exam_loc_*`, `ap_rte_*_round_1`, `udise_plus_*_progression` |
| May | RTE round 2; MeitY or gazette notices on DPDP dates (2027 only); add the next academic year's events if missing | `ap_rte_*_round_2`, `dpdp_duties_*` |
| June-July | UDISE+ new-year opening and the Data Capture Format; APAAR drive circulars; progression closing date | `udise_plus_*`, `apaar_*` |
| August | CBSE registration (Class IX and XI) and LOC (Class X and XII) circulars; CISCE CAREERS registration notice | `cbse_registration_*`, `cbse_loc_*`, `cisce_registration_*` |
| September-October | UDISE+ freeze date and dropbox notices; SARAS extensions; BSEAP SSC exam fee notice (bse.ap.gov.in) | `udise_plus_*_freeze`, `udise_plus_*_dropbox`, `cbse_saras_*`, `bseap_ssc_*_exam_fee*` |
| November | BSEAP SSC exam fee schedule and late-fee stages (dates "will not be extended": confirm within days); UDISE+ freeze | `bseap_ssc_*_exam_fee`, `bseap_ssc_*_exam_fee_late`, `udise_plus_*_freeze` |
| December | Check that every window from January to May of the next year is in the file; plan new readiness profiles for the next exam cycle (the next year's board events link the latest profile until then, FR-RDY-016) | all |

## How to change the file

1. Edit `apps/api/app/dq/config/portal_calendar.yaml`. Each event keeps its `source` list (https
   only). Add the new source; keep the old one while it still explains the date.
2. Keep `verified: false` unless you hold the official document (circular, gazette or portal
   screen) that gives the date. Mark a date `*_approximate: true` unless a source states it.
3. Do not tighten a date without a source. Prefer an early closing date: a reminder that comes a
   little early is better than one that comes late.
4. Board-portal events (`bseap`, `cbse`, `cisce` with boards) are copied into
   `apps/api/app/platform/billing.yaml` `protected_windows` in the same order (ADR-0057). Update
   both in the same PR. `school_admin` and `rte` events are not copied.
5. A new kind of deadline uses an existing portal kind. `school_admin` is for the school's own
   administrative deadlines with no student data; `rte` is for RTE rounds. A new kind needs a code
   change (`app/dq/calendar.py`, the reminder template, the web labels) and an ADR-0053 amendment.
6. Labels hold no student data (FR-RDY-020). A label may tell the school what to check first, as
   the AP SSC exam fee does ("check readiness before paying the fee").
7. Run:

   ```bash
   cd apps/api
   uv run pytest tests/dq/test_calendar.py tests/platform/test_board_sync.py -q
   uv run pytest tests/dq/test_calendar_api.py -q   # needs Docker (testcontainers)
   ```

8. Release before the first reminder of the changed window (30 days before it closes by default).
   A date a school has already confirmed always wins over the file, so a later release does not
   undo a school's confirmation.

## After a release

- If a window moved by more than a week, post a platform announcement to the schools it applies to
  ("the expected dates of ... changed; confirm them when your circular arrives").
- Note in the PR which pilot schools confirmed which dates (school names only, no student data).

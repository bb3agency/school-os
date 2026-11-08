# Audit 2026-11-08 (round 2): web student records

Round 2 per-feature auditor (prefix XS), in a worktree from `main` at `b64d0dbf`. Scope: student
records end to end in `apps/web`: `features/students`, `features/imports` (spreadsheet onboarding,
24-hour undo), `features/extraction` (register-photo verification queue, masking), `features/findings`,
`features/change-requests` (maker-checker with evidence), `features/sheets`, `features/users`, their
pages under `app/[locale]/(school)` and the BFF calls they make. Focus: invariant 4 (no full Aadhaar
number shown or sent), invariant 6 (identity edits only through change requests), object scope, and
cache freshness after mutations.

Each fixed finding has a test that fails before the fix.

## Findings

| ID | Severity | File | Description | Status |
|---|---|---|---|---|
| XS-01 | Medium | `apps/web/src/features/students/aadhaar.ts` `containsFullAadhaar` | The student features kept their own copy of the full-Aadhaar check. It accepted only one space or hyphen between groups and only a lead digit 2–9. `lib/aadhaar.ts` (aligned with `core.redaction` in round 1, DPA-01/03, D52) also refuses dots, slashes, wide gaps, NBSP and zero-width characters, `|`/`:` separators and any lead digit. The narrow copy guards the student create/edit/record forms, the student search box, the paste guard (`GuardedTextField`), import row edits, extraction confirm, enrolments, the sheet grid, and (outside this area) APAAR/consent notes, privacy requests and breaches. A number typed as `2345.6789.0123` or pasted with NBSPs passed the client check, so the clerk got a 422 instead of the inline explanation. In the sheet grid the cell was saved locally as "not saved yet" before the API refused it. FR-STU-012, invariant 4. | Fixed in `6f6d82bd`: `containsFullAadhaar` delegates to `lib/aadhaar.containsAadhaarNumber`. Test: `students.test.tsx` "XS-01: refuses every grouping the API refuses (dots, wide gaps, slashes, a 0/1 lead)". |
| XS-02 | Low-Medium | `features/change-requests/ChangeRequestDetailScreen.tsx` `Decisions`, `features/extraction/ItemReview.tsx` `ConfirmForm`, `features/imports/ImportDetail.tsx` | Several actions change student records without invalidating the cached `["staff","students",…]` queries (30 s stale time; the change-request header's student label uses 60 s): approving a correction (FR-CR-003 writes the new value), confirming a register-photo row (adds or links a student) and an import that finishes adding or undoing (both run in a worker and are polled). Opening the student page or list right afterwards showed the old official value or the list without the new students. That invites a duplicate correction request. | Fixed in `855281c4`. Approve and confirm also invalidate `["staff","students"]`. `ImportDetailView` invalidates it when a batch seen as `committing` becomes `committed` or `reverting` becomes `reverted`. Tests: `change-requests.test.tsx` "XS-02 (FR-CR-003) …", `extraction.test.tsx` "XS-02 (FR-IMP-021) …", `imports.test.tsx` "XS-02 (FR-IMP-005) …". |
| XS-03 | Low | `features/imports/ImportDetail.tsx` `UndoCard` | The Undo button and the "you can undo this import until {deadline}" text depended only on `can_revert` from the loaded batch. If the page was left open past `revert_deadline` (common on a shared office PC), the countdown pill disappeared, but the page still offered Undo for a time already gone. The API then refused with 409 `revert_window_closed`. FR-IMP-005. | Fixed in `e459d176`: undo is open only while the deadline is still ahead (the existing per-minute clock). Test: `imports.test.tsx` "XS-03 (FR-IMP-005): a page left open past the deadline stops offering undo". The existing undo test used a 2026-09-28 deadline, which had already passed on the real clock, so it now uses a deadline in the future. The e2e stand-in already uses `now + 1 day`. |
| XS-04 | Medium | `features/users/InviteUserScreen.tsx` `inviteSchema` | The staff edit form refuses a full Aadhaar number in the display name or email (invariant 4). The invite form did not. `POST /users` and `PATCH /users/{id}` have no server-side Aadhaar check (`apps/api/app/identity/schemas.py` `DisplayName`/`Email` only constrain length and control characters). An invited name such as "Lakshmi 2345 6789 0123" would therefore be stored and shown on every audit line, assignment and notification. | Web fixed in `55cb6896` (same check and message as the edit form). Test: `users.test.tsx` "XS-04 (FR-STU-012, invariant 4): the invite form refuses a full Aadhaar number too". **Server gap reported** (API change needed): `InviteIn`/`UserUpdateIn` should apply `core.redaction.contains_full_aadhaar` to `display_name` and `email` (422 `aadhaar_full_number_rejected`), like the other modules. |

## Verified clean

- **Aadhaar display.** `SensitiveValue` and `students/parts.tsx` show `aadhaar_last4` only via `aadhaarDisplay` (`XXXX XXXX 1234`, never widening a mask). Revealed values are kept only in component state, hidden again after 60 s, and never put in the query cache. Extraction shows masked fields read-only and never sends them back (`confirmBody` uses only the non-masked keys). A page whose photo was withheld cannot be confirmed.
- **Student search.** Names and admission numbers go in the `POST /students/search` body, not the URL. A query that looks like Aadhaar is not sent. The APAAR field is exempt only for the exact 12-digit typed ID, as on the server.
- **Invariant 6.** `StudentEdit` edits only status, class/section and guardians. Identity fields link to `/change-requests/new` (only with `student.identity_change.request`). "Record a value" handles 403 `identity_change_required` with a link to change requests. The sheet grid and import edits do not edit canonical identity values directly.
- **Maker-checker.** Approve and reject are shown only when `can_decide`, the caller is not the requester (`membership_id === requested_by`, the same comparison as `changes/service.py`) and the caller holds the approve permission. Cancel is shown only to the requester. Decisions send `If-Match` and require step-up.
- **If-Match formats.** `"3"` (record value) and `W/"3"` are both accepted by `authz/http.py` `_ETAG_RE`.
- **Findings.** Resolving a blocker is offered only with `dq.findings.waive` (DL-06). The correction link appears only for the school-record route and with the request permission.
- **Evidence upload.** Type and size are checked before upload. The upload goes straight to storage with `credentials: "omit"`. A file name containing an Aadhaar number is refused by the API (`FileName` validator).

## Outside my area / not fixed

- The server-side gap in XS-04 (identity module; backend).
- `features/students/aadhaar.ts` still has its own `verhoeffValid`, used by `test/records-fixtures.ts`. Not a bug; it could re-export the one in `lib/aadhaar.ts`.
- Section pickers list every section of the school for a class teacher. The search results are scoped by the API, so this shows only class and section names, not student data.

## Checks run

`npx tsc --noEmit -p .` clean; `npx eslint` (touched features) `--quiet` clean; `npx prettier --check` on touched files clean; `npx vitest run` for students, imports, extraction, change-requests, users, sheets, apaar, consents, privacy and breaches passed; `npx eslint . --quiet` clean. Full `npx vitest run`: 159 of 160 files, 2406 tests passed. `platform/actions.test.tsx` "announcements …" timed out at 5 s under load from parallel agents; it passes when run alone (11 of 11) and is outside this area.

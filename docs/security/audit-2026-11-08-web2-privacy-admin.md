# Audit 2026-11-08 (round 2): web privacy, compliance and school administration

Round 2 per-feature auditor (prefix XP), in a worktree from `main` at `b64d0dbf`. Scope in
`apps/web`: `features/privacy` (requests and complaints, access reports, lawful erasure, notices,
record classes), `consents`, `breaches`, `apaar`, `disclosure`, `insights`, `admin` (retention,
data export), `settings`, `support-access`, `break-glass`, `support`, `school-status`, their pages
and the BFF handlers. The focus was on irreversible actions (erasure, publishing or freezing the disclosure,
closing a breach, approving break-glass): each needs a confirm step and must not send twice. Sensitive
lists must not stay cached across users in the browser.

Every fixed finding has a test that fails before the fix. Second pass (lead decision): XP-03 and XP-05 fixed.

## Findings

| ID | Severity | File | Description | Status |
|---|---|---|---|---|
| XP-01 | Medium | `apps/web/src/features/disclosure/DisclosureScreen.tsx` (`PublishDialog`, `DraftEditor`) | Publish (`POST /disclosure/publish`, step-up) freezes the **saved** draft as the public snapshot. The button stayed enabled while the editor had unsaved changes. A principal who typed a correction and pressed Publish therefore published the old values. The dialog says "The page will show the draft as it is now", which is false in that case. FR-DISC-008. | Fixed in `942cff2d`. Publish is now disabled while the editor is dirty, and a visible line says "Save your changes before publishing" (new key `disclosure.publish.saveFirst`, added to en and te). Test: `features/disclosure/DisclosureScreen.test.tsx` "offers Publish only once the typed changes are saved (XP-01)". |
| XP-02 | Low | `disclosure/DisclosureScreen.tsx` draft save; `privacy/RequestDetailScreen.tsx` `WorkForm.save` and `AccessPanel.run` | Same class as WD-06. These double-submit guards checked React state (`pending`), which a second Enter or click in the same frame does not see yet. The draft PUT and the notes PATCH went out twice, and the second showed a spurious 412 "someone changed it". The access-report build (`POST …/report`, no Idempotency-Key) queued two exports. | Fixed in `942cff2d` and `ddc167bf`: a ref is checked and set before the request and cleared in `finally`. Tests: "two synchronous submits send one PUT (XP-02)", "two synchronous submits send one PATCH (XP-02)" and "two synchronous clicks send one POST (XP-02)". The remaining sites with the same guard are XP-05. |
| XP-04 | Low | `privacy/RequestDetailScreen.tsx` `ErasurePanel` | After an erasure the API marks the request `decided`. The dialog refreshed only the request and its erasure plan. The requests list (`["staff","privacy","requests",…]`) kept showing the old status for up to the 30 s stale time, so the request still looked open to act on. FR-DPDP-012. | Fixed in `ddc167bf` (invalidates `PRIVACY_KEYS.all`). Test: `privacy.test.tsx` "the list on the same page shows the decided status (XP-04)". |
| XP-03 | Low-Medium | `apps/web/src/features/admin/RetentionScreen.tsx`, `admin/data.ts` | Shortening a retention period permanently deletes the older working data at the next daily clean-up. Save sent the PUT at once. No confirm step named which categories got shorter or said that the deletion cannot be undone. Only the general "Shorter periods apply from the next daily clean-up" line and a possible step-up prompt warned the user. FR-ADM-002. | Fixed in `e0134b72`. When any period gets shorter (`shortenedCategories`), Save opens a danger `ActionDialog` placed outside the form. The dialog has step-up, because `PUT /admin/retention` requires a recent MFA sign-in. It lists each shortened category with old → new days and says "Data older than the new period is deleted at the next daily clean-up and cannot be recovered". A save that only lengthens periods goes straight through. A 412 in the dialog shows "Load the latest" on the page. New keys `admin.retention.shorten*` were added in en and te. Tests in `admin.test.tsx`, "shortening a retention period is confirmed first (XP-03)": shorter periods named and nothing sent until confirmed; lengthening saved at once; 412 in the confirm step. The existing save test now confirms, and the 412 test lengthens a period. |
| XP-05 | Low | `insights/FlagsScreen.tsx` `RulesCard`, `apaar/ApaarScreen.tsx` `FormLanguageCard`, `consents/PurposesScreen.tsx` `SuggestionRow`, `settings/BoardsCard.tsx`, `settings/ParentLanguageCard.tsx`, `apaar/StudentConsentScreen.tsx` and `consents/StudentConsentsPanel.tsx` record forms, `privacy/parts.tsx` `NoticePdfButton` | The same state-only guard as XP-02. The If-Match PUTs and PATCHes showed a spurious 412 on a same-frame double submit. The consent record forms keep one Idempotency-Key per attempt, so a duplicate got 409 `idempotency_in_progress`: no second record, only a confusing error. The purpose suggestion and `NoticePdfButton` could send a second POST (a second PDF export). | Fixed in `4fcb9435` with the same ref guard. A shared helper, `src/test/double-submit.ts` (`fireTwice`, `expectOneCall`), backs eight tests named "(XP-05)" in `insights`, `apaar`, `consents`, `settings/boards` and `privacy` tests. All eight fail without the fix. |

## Verified clean

- **Confirm steps on irreversible actions.** Each of these goes through `ActionDialog`, which has a title, a consequence line and step-up where the API needs it. `useApiForm` provides its synchronous guard (WEB-02):
  - erasure: names the record count and the number of kinds, and only ticked erasable rows can be chosen;
  - access-report release, request close, complaint answer and withdraw;
  - disclosure publish (no-student-data checkbox as `z.literal("on")`), withdraw (reason 10–500) and link rotation;
  - breach close (If-Match; a reason is required while duties are open), duty done and affected counts;
  - break-glass approve, deny and revoke (step-up; approve and deny are hidden for emergency grants);
  - insights note erase and flag erase.
- **Break-glass visibility.** The list and detail pages are gated on `breakglass.approve` with no request sent otherwise. The scope shows only `section_id` and `class_id` as limiting access; other keys are listed as not limiting (DL-10). The support-access banner and the suspended banner are plain text with `data-print="hide"`.
- **Insights purpose limit (invariant 14).** The student timeline tab is added only when the caller has `insights.read` and the sensitive-read permission. The API's 404 is shown as "not in your classes". The flag list, flag detail and rules pages send no request without `insights.read`. Note, raise-flag and erase actions follow `insights.note`, `insights.act` and `insights.manage`.
- **Privacy lists.**
  - The requests list is a POST search, so nothing about the person goes in a URL. Only the tab is in the address.
  - The decide buttons follow `privacy.request.decide` and the server's `can_decide`. "Someone else must decide" is shown to the person who logged the request.
  - Letters open in a new tab with `noopener`.
- **Browser caching across users.**
  - BFF proxy answers carry `Cache-Control: no-store`, and public pages `private, no-store`.
  - Sign-out, school switch and the end of a support session do a full page load, so the QueryClient is discarded (as in round 1).
  - Nothing in these features writes to `localStorage` or `sessionStorage`.
- **If-Match / Idempotency-Key.** Every PATCH or PUT in these features sends `If-Match` from the version it read. Every create that takes `IdempotencyDep` sends a key that stays stable across retries: privacy requests, notices, purposes, APAAR and consent records, breach create and disclosure publish.

## Outside my area (seen, not changed)

- None.

## Checks run

From `apps/web`:
- `npx tsc --noEmit -p .`: clean.
- `npx eslint . --quiet`: clean.
- `npx prettier --check` on the touched files: clean.
- `npx vitest run src/features/disclosure src/features/privacy src/i18n`: green.
- Full `npx vitest run`: 161 files, 2406 passed, 3 skipped after the first pass; 161 files, 2417 passed, 3 skipped after the second pass (XP-03, XP-05). tsc and eslint clean again after the second pass.

No e2e spec asserts the changed behaviour: `e2e/r3.spec.ts` publishes the disclosure without editing it first.

# Audit 2026-11-08: web, operator console, public and parent pages, print, performance

Frontend auditor of the 2026-11-08 round, worktree branch `worktree-agent-a08b598883ea1554e` from
`main` at `813c3996`. Scope: the operator console (`app/[locale]/platform/*`,
`features/platform`), the public and parent pages (`/welcome`, `/pricing`, `/p/*`, `/v`, `/d/*`
and their BFF handlers in `server/public`), print CSS, and client performance on 1366×768 office
PCs on slow links. Prefix `WP-`. Each fixed finding has a test that failed before the fix.

## Findings

| ID | Severity | Area | Evidence | Fix | Test | Status |
|---|---|---|---|---|---|---|
| WP-01 | Medium | Operator console, two-person rule (SEC-029) | Three second-step buttons were offered to the operator who took the first step: "Approve offboarding" to the operator in `offboard_requested_by` (`features/platform/SchoolDetailView.tsx:231`), the second "Confirm emergency" to `emergency_confirmed_by_1` (`OperationsViews.tsx:827`), and "Approve" on a critical announcement to `submitted_by` (`AnnouncementsView.tsx:153`). The API refuses each with 409 `same_operator`, so nothing was bypassed, but the console invited the requester to approve their own request after a step-up sign-in. The incident confirmation and reopen screens already hid it. | `3759fb9f`: each button needs a loaded `/platform/me` whose `operator_id` differs from the first operator. The old `actions.test.tsx` case that clicked the button as confirmer 1 now uses a list that is out of date (another operator), so the 409 message is still covered. | `two-person.test.tsx` "the operator who asked cannot give the second step (WP-01, SEC-029)" (3 tests) | Fixed |
| WP-02 | Low | Parent notice page, PDF preparing (D71, AZ-02, FR-PUB-009) | After AZ-02 a failed render answers 202 `preparing` with `Retry-After` up to 900 s. `server/public/actions.ts` `documentRedirect` dropped the header and `/p/view?pdf=preparing` always said "Try again in a minute", so parents tapped again inside the cooldown and got the same answer for up to 15 minutes. | `4a856ff0`: the redirect carries the wait as whole minutes (`&wait=N`, 2..60; anything odd is ignored) and the page says "Try again in N minutes". New key `common.pdfPreparingMinutes` at the end of `parent.en.json` and `parent.te.json`. | `server/public/actions.test.ts` (Retry-After 900, 61, 999999, junk), `features/public/PublicPages.test.tsx` "names the wait the API asked for…" | Fixed |
| WP-03 | Low | CBSE disclosure page (FR-DISC-009) | `/d/{key}/documents/{n}` (`server/public/disclosure.ts`) sent the reader back to the same page, unchanged, for a 404, 429, 5xx, unreachable API or an odd file URL. On a phone the link looked dead and nothing said why. | `256254ab`: back to `/d/{key}?doc=0`, and the page shows an alert ("The document could not be opened just now. Try again in a few minutes."). An invalid key still goes to `/d/unknown`. New key `public.disclosure.documentFailed` at the end of the object in `en.json` and `te.json`. | `server/public/disclosure.test.ts`, `features/public/DisclosurePages.test.tsx` | Fixed |
| WP-04 | Medium (performance on slow links) | Every console and marketing page (not the `(public)` group) | `app/[locale]/layout.tsx` rendered `<NextIntlClientProvider>` with no `messages` prop. In next-intl 4 the server provider then passes the request's whole catalogue (`node_modules/next-intl/dist/esm/production/react-server/NextIntlClientProviderServer.js`: `messages === undefined ? await getMessages()`), so every full page load inlined the whole English catalogue into the RSC payload: **456.7 KB raw / 130.5 KB gzip of JSON** (en.json with en.telugu-off.json; the file itself is 560,379 B with whitespace), more than the 80 KB gzip of route JS that `/welcome` adds over the framework. A clerk or a visitor to `/welcome` paid for the operator console's, every module's and the marketing texts. | Lead decision 2026-11-08. `5a575b29`: the root layout sends only `CLIENT_NAMESPACES.root` (`src/i18n/client-messages.ts`); the `(school)` and `platform` layouts and the marketing and dev pages wrap themselves in `<ClientMessages scope=…>` (`src/i18n/ClientMessages.tsx`, a nested provider that picks the area's namespaces from the request catalogue; the parent pages already had no provider). **Measured message payload per page, before → after (JSON gzip):** every page 130.5 KB → sign-in states and school picker 9.2 KB, marketing pages 17.5 KB, operator console 52.4 KB, school console 116.5 KB (the console's client components use almost every module's texts), dev pages 41.1 KB (script: root list plus the area's list, both providers counted). Route JS sizes are unchanged (`next build`). | `src/i18n/client-messages.test.ts` (walks the imports of each area's layouts and pages and fails when a namespace a component asks for, by `useTranslations`, a `namespace`/`errorNamespace` prop or a full key, is not in its list; root carries no `platform`, `fees`, `school`, `marketing`); `src/app/root-messages.node.test.tsx` (the root provider gets exactly the root list); `layouts.node.test.tsx` (school and platform layouts wrap their shell in their scope); `english-only.test.tsx` now renders every page with its area's namespaces and fails on any missing message (it found the marketing pages before they had their own scope). **Follow-up (wave F4, NFR-PERF-013..015):** the school console is split per area: its layout sends `schoolBase` (shell, menu labels `<area>.nav`, banners; 0.073 of the catalogue) and each area's `layout.tsx` (`(school)/<folder>`, settings by subfolder, the home page itself) sends its own list (`src/i18n/school-areas.ts`), whose entries may be subtrees (`"school.nav"`). The scan now works on message paths, each area has a budget, and a school page carries 28-72 KB gzip of messages with the root list (was 147 KB at `1cd65ade`) | Fixed |
| WP-05 | Low | Operator dashboard incident clocks (D49 part 2, FR-DPDP-031) | The dashboard's clock alerts (`DashboardView.tsx`) came from the dashboard KPIs, read once when the page opened, so "due in 2 h" did not move while the dashboard stayed open. The incidents list already asks for `/platform/incidents/clocks` every minute. | `c74c92d6`: for operators whose dashboard carries the clocks (`platform.incidents.read`), `DashboardScreen` reads the same `INCIDENT_KEYS.clocks` query with a 60 s `refetchInterval` and shows that list; others never ask for it. | `incidents/incident-console.test.tsx` "the dashboard's clocks move on while it stays open" (150 → 30 minutes after 60 s), "…does not ask for the clocks without the incident role" | Fixed |

## Verified clean

- **Role gating** (`app/platform/roles.yaml` against `components/shell/PlatformShell.tsx` and
  the screens): every menu item's `anyOf` matches the permission its list endpoint needs;
  announcements, break-glass and the dashboard are `ANY_OPERATOR` in the API, so they are shown
  to everyone. Buttons check the same permissions as their endpoints (`useCan`).
- **Two-person flows other than WP-01**: incident confirmation and reopen (`confirmationOf`,
  `reopenOf` with `mine`) hide the second step from the first operator; break-glass withdraw is
  shown only to the requester; offboarding withdraw is allowed to any holder of
  `platform.tenants.offboard` (as the API does).
- **Dedicated mode**: `platformEnabled()` is checked in the platform layout (404), in the BFF
  proxy for `/api/v1/platform/*`, and in the operator sign-in handler; `marketingEnabled()` makes
  Features, Security, Pricing and About 404 and `/welcome` a plain sign-in page.
- **Usage screen, AZ-03**: no all-schools query longer than 31 days is sent
  (`usageRangeTooLong`, warning shown instead); one school keeps the API's 400-day range.
  FilterCard/FilterChips params (`from`, `to`, `school`) match what the page reads.
- **Public and parent pages**: one root layout without providers, session or staff fonts;
  `robots` noindex/nofollow and `no-referrer`; the proxy sets the strict public CSP, no-store and
  the other public headers; dead, revoked or withdrawn links are the uniform 404 expired page;
  `/p/{token}` keeps the token in the path, answers 503 with `Retry-After` when the API is down;
  forms POST to route handlers that check the CSRF token, same origin and the form content type;
  `/v/check` works without JavaScript. Client JS of these pages is the framework only (6 files,
  132 KB gzip, measured from the client-reference manifests of `next build`): no feature code.
- **375 px**: `public.css` is single-column up to 30rem, tables have a focusable scroll region,
  long values wrap (`overflow-wrap: anywhere`), money/dates are `nowrap` only on short figures.
- **Print**: A4 `@page`, named landscape page for registers, `data-print="hide"` on the shell,
  breadcrumbs, page and card actions, pagination, drawers and the command palette; table headers
  repeat (`thead { display: table-header-group }`), rows and figures do not split, tables wrap
  inside the page. The breach draft print page (D60) prints only the sheet. The parent pages hide
  buttons, forms and the UPI details in print.
- **Performance**: `motion` is loaded through `LazyMotion` with its features in a separate chunk;
  Lenis is imported after hydration on the home page only. Operator routes are 243–270 KB gzip
  of first-load JS, marketing pages 211–212 KB, the public pages 132 KB (framework only). Lists
  page with `usePagedList` and the Pager. Images have `width`/`height`; fonts are self-hosted
  through `@fontsource` (no external font or script CDN).
- **Baseline**: no non-Baseline JS API in client code; `scrollbar-gutter` and
  `text-wrap: pretty` are progressive enhancements that fall back to the default layout.

## Outside my area

- `/settings/billing` and the support screens on a dedicated host read the control plane
  through the API; whether those calls must be hidden there is for the school-console auditor.

## Verification

- `npx tsc --noEmit -p .`, `npx eslint . --quiet`, `npx prettier --check` on the touched files:
  clean.
- `npx vitest run src/features/platform src/features/public src/server/public src/i18n
  src/app/english-only.test.tsx`: green. Full `npx vitest run`: 152 files, 2324 passed, 3 skipped,
  1 failed: `english-only.test.tsx` `ask/verified` timed out (60 s) in the parallel run, a page
  outside this change; it passes when run on its own.
- After WP-04 and WP-05 (merged with `main` at `281f350a`): tsc, eslint and prettier clean;
  `npx next build` succeeds. Full `npx vitest run`: 154 files, 2340 passed, 3 skipped, 2 failed,
  both in `features/platform/actions.test.tsx` (the two announcement form tests: a 5 s timeout
  while typing under the parallel load, then a follow-on assertion); that file passes on its own
  (38/38). The app, i18n, platform and marketing suites (34 files, 485 tests) and the whole
  `english-only.test.tsx` (134) pass.
- `npx next build`: succeeds (route-size figures above come from this build; again after WP-04/05).
- e2e: no spec asserts the changed buttons or redirects (`grep` of `apps/web/e2e`); not run
  (the lead runs the browser suite after merging).

# SchoolOS 2026 UI redesign: research and design direction

| Field | Value |
|---|---|
| Date | 2026-10-18 |
| Status | Research input for the lead and the product owner. Nothing here changes `docs/17-ui-design-system.md` until an ADR or a docs/17 revision adopts it |
| Owner decisions already taken (2026-10-18) | Calm, professional SaaS look (Linear/Stripe-like). **Light theme only.** Keep the current fonts: PP Mori and Instrument Serif, self-hosted. Comfortable density by default with a compact toggle. Grouped collapsible sidebar plus a Ctrl/⌘K palette, with a drawer on phones. Covers every role and every surface. 2–3 accent options for the owner to pick from |
| Scope | `apps/web`: the school console (including teacher views), the operator console (`/platform`), the marketing site, and the public parent and verification pages |
| Companion | `skills-review.md` |
| Method | I read CLAUDE.md §10, docs/17 v0.9, `globals.css`, `components/ui`, `components/shell`, `lib/security-headers.ts` and the route tree, then did web research (sources in §11). Vendor and agency blogs are marked as opinion. MDN, W3C, web.dev, NN/g and GOV.UK are treated as primary sources |

## 1. Summary

The SchoolOS engineering rules are already modern:
- Tailwind v4 `@theme` tokens, with contrast pairs recomputed by a test;
- one sidebar with a compact mode;
- native `<dialog>` drawers and bottom sheets;
- stacked tables on phones;
- short, functional motion that respects reduced-motion settings;
- a strict nonce-based CSP.

What stops it feeling like a 2026 product is mostly how it looks and how much fits on screen:

1. **Too much decoration for a work tool.** The sky-to-aqua gradient canvas, white cards with large soft shadows, serif numbers on every KPI and mono uppercase eyebrows make the app feel like a marketing page. The reference products moved the other way: quieter chrome, warmer neutral greys, hairline structure, and colour used only to carry meaning ([Linear 2026 refresh](https://linear.app/blog/behind-the-latest-design-refresh); [AdminLTE 2026 review](https://adminlte.io/blog/25-best-dashboard-design-examples-for-2026-real-products-principles/)).
2. **Vertical space at 1366×768.** At 768px tall, about 650px of viewport is left after the browser's own chrome. A top bar plus a page-header card plus a list card pushes the first table row low on the screen.
3. **No fast way to move around.** The school sidebar has about 40 destinations in 8 groups, and there is no command palette or global search.
4. **Tables are capable but not "power tables" yet.** They have no user-facing density toggle, no column visibility, no row selection with a bulk bar, and no visible chips for the active filters.
5. **Figures do not align.** PP Mori has no ₹ sign and no tabular figures (`fonts/pp-mori/LICENSE-NOTE.md`), so fee amounts mix two fonts and columns of numbers wobble.

**Recommendation.** Direction A, "Quiet ledger" (§6.1), for both consoles. Direction B's paper-and-serif look is for marketing, the public pages and print. Deliver it primitive-first: there are about 76,000 lines of TSX under `features/`, so the change has to land in the tokens, `components/ui` and `components/shell`. Most screens should then pick it up without their own edits.

## 2. Users: needs per role, and how the shell adapts

Most users have limited digital confidence, internet is patchy, and many work on shared office PCs. Three rules follow for every role:
- Every icon has a text label (icon-only appears only in the compact sidebar, with tooltips).
- One clear primary action per screen, with plain verbs ("Record payment", not "Submit").
- If the network fails, the screen keeps what the user typed and offers to try again.

| Role | Device and context | What they need most | Home ("Today") | Navigation and shortcuts |
|---|---|---|---|---|
| Office clerk / data entry | Shared PC at 1366×768, keyboard, long sessions, interrupted often | Speed, density, keyboard flow, keeping their place, clear errors | Work queue: imports to check, register-photo items, findings assigned to them, change requests waiting | Full sidebar; the palette is their main way around. Compact density offered. Stable sort and filter state in the URL, so a page survives a reload |
| Accountant / fee counter | PC at the counter with a queue of parents; receipt printer | Find a student in under 2 s, large unambiguous amounts, never a duplicate receipt, printing | Fees today: collected by payment mode, pending approvals, dues follow-ups, day-close status | `Alt`-key shortcuts on the counter screen (shown on screen). Receipt Print button focused after a payment is recorded. Idempotent submit (ops idempotency keys already exist) with a "Recorded / not recorded" state that survives a network drop |
| Class teacher / subject teacher | Own Android phone, often on 3G or 4G; occasionally the staff-room PC | Attendance in under a minute, marks entry, only their own sections, Ask | Teacher console: today's classes and sections, an "Attendance not taken" call to action, marks due, circular tasks | **On phones, a bottom bar with Home, Attendance, Marks and Ask**; Menu stays in the top bar. Sections are already filtered by scope (invariant 3), so the UI never shows other classes. Large tap targets, with one tap per student and sticky "Save attendance" |
| Principal / owner / management | Phone and office PC; short visits | "Is everything okay?", approvals, portal readiness, money summary | Status strip (portal readiness %, open findings, approvals waiting, fees today vs expected), then the approvals inbox | Bottom bar on phones: Home, Approvals, Ask, Messages. Approval dialogs show the consequence line |
| Parent | Cheap Android phone, opened from a WhatsApp link; may prefer Telugu (dormant) | One task: see a receipt, a notice or dues; verify a certificate | No shell; a single-purpose public page | None. The page renders on the server and works without JavaScript for the core content. Large type, one action, and an expired link explains what to do next. Small weight budget (the full page under ~100 KB on 2G/3G) |
| Certificate verifier (employer, another school) | Any device | A clear verdict: valid, revoked or not found | Public `/v` page | None |
| SchoolOS operator | Desktop | Fleet health, billing, support, two-person actions | Platform dashboard | Distinct chrome so operators always know they are in the control plane. The palette is limited to platform commands |

**How the shell adapts.** There is one `AppShell` with one set of components:
- The sidebar groups and palette commands are filtered by permissions and features (already done for the sidebar).
- "Today" is one page whose sections appear according to permission, with the most important section first per role. There is no separate dashboard codebase per role.
- On phones the bottom bar appears only for roles with a phone-first job: a teacher permission set, or principal approvals.
- The teacher console is the same app with a reduced home page and navigation, not a separate product.

## 3. Where SchoolOS is today

| Area | Today | Keep | Change |
|---|---|---|---|
| Tokens | Hex values in `@theme`; `tokens.test.ts` recomputes contrast | Test-backed contrast, semantic names | OKLCH ramps behind the same names; teach `tokens.test.ts` to parse `oklch()` |
| Canvas | Gradient on dashboards and marketing; `canvas-neutral` on record screens (`components/shell/canvas.ts`) | The rule lives in one place | Neutral canvas across the whole console; gradient only on marketing |
| Cards | White, 12px radius, hairline border and a large soft shadow | | Hairline only; shadow reserved for overlays |
| Type | PP Mori for UI, Instrument Serif for numbers, JetBrains Mono for eyebrows | Owner decision: keep PP Mori and Instrument Serif | Add an "SOS Figures" face for digits and ₹ (§6.1); use the serif only for page-level hero figures; sentence-case group labels instead of mono uppercase in the app |
| Shell | Sidebar 272/72px; top bar with the bell; a page-header card on every page | One sidebar, drawer below lg, compact mode | 240/56px; breadcrumb, palette and help move into the top bar; plain header row |
| Tables | Density prop, numeric columns, sticky first column, stacked/reflow on phones | Stacked cards on phones, `TableScroll` | Toolbar, density toggle (comfortable by default), sticky header, column visibility, selection with a bulk bar, filter chips |
| Forms | Filled inputs, zod validation, inline `Alert live` | Inline results, the consequence line | Error summary, `:user-invalid`, sticky footer, stepper |
| Motion | docs/17 §5.5 | Everything | Small additions (§7.5) |
| Theme | Light only | Owner decision: light only | none |
| CSP | `style-src 'self'` in production | Must stay | Limits which libraries can be used (§4.9) |

## 4. Research findings (sourced)

### 4.1 What makes a B2B admin UI feel modern in 2025-2026
- **Linear, March 2026 refresh.** "Don't compete for attention you haven't earned": a dimmer sidebar, fewer and smaller icons, and no coloured icon backgrounds. "Structure should be felt, not seen": softer, rounded separators. The default greys moved from cool to warmer and less saturated ([Linear](https://linear.app/blog/behind-the-latest-design-refresh)).
- **Linear, 2024 redesign.** Themes are generated in LCH from 3 inputs (base, accent, contrast) instead of 98 variables, with blue limited in the neutrals. Inter Display is used for headings. The navigation structure was deliberately left unchanged, and the work focused on chrome density and alignment ([Linear](https://linear.app/now/how-we-redesigned-the-linear-ui)).
- **Vercel.** Restraint with colour: status colours carry the semantic weight ([AdminLTE](https://adminlte.io/blog/25-best-dashboard-design-examples-for-2026-real-products-principles/)). The Geist tokens are a neutral grey ramp (`#f2f2f2` to `#171717`) plus one blue scale ([vercel.com/design.md](https://vercel.com/design.md)).
- **Stripe.** Compact, sortable, filterable tables; 3–5 top metrics; advanced filters behind "More"; amounts right-aligned. These are third-party descriptions but they agree with each other ([AdminLTE](https://adminlte.io/blog/25-best-dashboard-design-examples-for-2026-real-products-principles/); [setproduct](https://www.setproduct.com/blog/effective-dashboard-design-principles)).
- **AI inside the record.** Attio's "Ask Attio" sits in the record view ([AdminLTE](https://adminlte.io/blog/25-best-dashboard-design-examples-for-2026-real-products-principles/)). SchoolOS should add "Ask about this student/finding/document" entry points.
- **Progressive disclosure on dashboards.** Answer "is everything okay?" first ([925studios](https://925studios.co/blog/saas-dashboard-design-examples-2026); [saasui.design](https://saasui.design/blog/7-saas-ui-design-trends-2026); both are opinion).

### 4.2 Shell and navigation
- **Command palette.**
  - Opens with Ctrl+K on Windows (⌘K on Mac) from one document-level listener that calls `preventDefault`.
  - Escape leaves a sub-level first, then closes the palette.
  - It is a combobox inside a modal dialog: focus stays in the input, the active option is tracked with `aria-activedescendant`, results are grouped and labelled, and a polite live count announces the number of results.
  - Commands have stable IDs, and searching is debounced.
  - Sources: [techinterview.org](https://www.techinterview.org/post/3233475212/build-command-palette-cmd-k/), [webfactory](https://www.webfactoryltd.com/command-palette-ux-speeding-up-saas/). Check the WAI-ARIA APG combobox pattern when building it.
- **Mobile.** A bottom bar suits 3–5 destinations; beyond that, use a drawer ([Android](https://developer.android.com/design/ui/mobile/guides/layout-and-content/layout-and-nav-patterns); [SAP Fiori](https://www.sap.com/design-system/fiori-design-android/v25-8/components/m3-standard-components/navigation-bar/usage)). For SchoolOS that means keeping the drawer and adding role-specific 4-slot bottom bars.

### 4.3 Data tables
- A sticky header is close to mandatory. The active filters and the bulk bar should stay pinned too ([saasui.design](https://saasui.design/blog/saas-data-table-ux-patterns)).
- Users choose which columns to show and the row density, and the choice is remembered ([Cloudscape](https://cloudscape.design/patterns/resource-management/view/table-view/)).
- The bulk bar appears only when rows are selected. The header checkbox selects the visible page only. Sorting, paging or changing filters clears the selection. Destructive bulk actions confirm with the row count ([Cloudscape](https://cloudscape.design/patterns/resource-management/view/table-view/)).
- Users must always see what they filtered and how to undo it ([setproduct](https://www.setproduct.com/blog/effective-dashboard-design-principles)).

### 4.4 Loading and performance
- NN/g timing guidance ([NN/g](https://www.nngroup.com/articles/skeleton-screens/)):
  - under 1 s: show nothing;
  - 2–10 s: a skeleton for a full page, a spinner for a single module;
  - over 10 s: a progress bar with an estimate.
  - Skeletons must mirror the real layout.
- The evidence that skeletons feel faster is mixed (a 2017 Viget study found the opposite). Their firm benefit is reserved layout space ([virtuslab summary](https://virtuslab.com/blog/frontend/ux-patterns-beyond-raw-performance)).
- INP is good at 200 ms or less at the 75th percentile and poor above 500 ms ([web.dev](https://web.dev/articles/optimize-inp)). QuintoAndar cut INP by 80% using RUM, yielding during long tasks, and React transitions ([web.dev case study](https://web.dev/case-studies/quintoandar-inp)).
  - For SchoolOS: filter inside transitions, debounce searches, abort stale requests, and never re-render a whole table on each keystroke.
- Optimistic UI (`useOptimistic`) reverts automatically on failure, but showing the error is still the app's job ([coddy.tech](https://coddy.tech/docs/react/useoptimistic); [SitePoint](https://www.sitepoint.com/react-useoptimistic-production-patterns-for-instant-ui-updates/)).
  - Use it only for low-stakes toggles. **Never** use it for official records, fees or maker-checker steps (invariants 6 and 7).

### 4.5 Colour
- Tailwind v4 is configured in CSS:
  - `@theme` generates the utilities;
  - `--color-*: initial` replaces the default palette (already done);
  - `@theme inline` is for tokens that point at other variables;
  - the default palette is written in `oklch()` ([Tailwind](https://tailwindcss.com/docs/theme)).
- `oklch()` is Baseline Widely available (since May 2023) ([MDN](https://developer.mozilla.org/docs/Web/CSS/color_value/oklch)). `color-mix()` is widely available according to secondary sources ([modern-css.com](https://modern-css.com/playground/css-color-mix)).
- `light-dark()` was Newly available in May 2024 ([MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/light-dark)); under the 30-month rule ([web.dev Baseline](https://web.dev/baseline)) it becomes Widely available around November 2026. It is not needed while the theme is light only.

### 4.6 Typography
- Inter (OFL-1.1) has variable weight 100–900 plus optical size, tabular figures at every weight, `ss02` disambiguation (O/0, I/l/1) and a slashed zero ([rsms.me/inter](https://rsms.me/inter/)).
- The repo already ships `@fontsource-variable/inter` 5.3.0:

  | File | Size | Note |
  |---|---|---|
  | `inter-latin-wght-normal.woff2` | 48,256 B | |
  | `inter-latin-ext-wght-normal.woff2` | 85,068 B | Contains ₹ (U+20B9); fee pages already download it as PP Mori's fallback |

- Geist is also OFL ([Geist README](https://cdn.jsdelivr.net/npm/geist@1.7.2/README.md)), but the owner chose to keep the current fonts.

### 4.7 Forms
- GOV.UK: validate on submit; error messages say what is wrong and how to fix it; an error summary at the top links to each field ([error message](https://design-system.service.gov.uk/components/error-message); [validation](https://design-system.service.gov.uk/patterns/validation)).
- CMS.gov: use instant validation only where answers are not obvious or the field is hard to fill ([CMS.gov](https://design.cms.gov/patterns/Forms/error-validation/)).
- `:user-invalid` is Widely available (since November 2023) ([MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/:user-invalid)). `text-wrap` is Widely available (since March 2024) ([MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/text-wrap)).

### 4.8 Accessibility (WCAG 2.2 AA)
- **2.5.8 Target Size:** at least 24×24px, with exceptions for spacing, an equivalent control, inline links, user-agent controls and essential cases ([W3C](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)). Compact table rows must keep their actions at 24px or more.
- **2.4.11 Focus Not Obscured:** sticky headers, bulk bars and sticky footers must never fully hide the focused element. Technique C43 (`scroll-padding`) is the fix ([W3C](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html)).
- **2.1.4 Character Key Shortcuts:** prefer Ctrl or Alt combinations over single keys.

### 4.9 Component libraries, licences and the CSP

| Option | Licence | Status | Fit |
|---|---|---|---|
| In-house primitives on native elements | own | today | Best fit for CSP and Baseline; weak on combobox, menu typeahead and date picker |
| React Aria Components | Apache-2.0 ([npm](https://npmjs.com/react-aria-components)) | stable 1.x | Most rigorous keyboard, screen-reader and i18n support; larger bundle |
| Base UI `@base-ui/react` | MIT | v1, early 2026, 35 components ([InfoQ](https://infoq.com/news/2026/02/baseui-v1-accessible/)) | Reported as shadcn's default base since mid-2026 ([pkgpulse](https://www.pkgpulse.com/guides/shadcn-ui-vs-base-ui-vs-radix-components-2026)) |
| Radix | MIT | Maintenance pace criticised ([designrevision](https://designrevision.com/alternatives/radix-ui)) | Fine, but no longer the obvious default |
| shadcn/ui | MIT (copied code) | | `tailwind-merge`/`cva` conflict with docs/17 rule 7, and it gives the generic shadcn look |

**CSP caution.**
- Headless libraries set positioning and visually-hidden styles through `style` props.
- At run time, styles set through the CSSOM are allowed under `style-src 'self'`. A `style` attribute in **server-rendered HTML** is blocked, and so is any injected `<style>`. That is why Sonner was rejected (docs/17 §5.5). See [MDN style-src](https://developer.mozilla.org/docs/Web/HTTP/Headers/Content-Security-Policy/style-src) and [centralcsp](https://centralcsp.com/docs/style-src).
- I found no library-specific CSP documentation. Spike each candidate under the production CSP in Playwright, check for violations, and record the result in an ADR.

### 4.10 Motion and not-yet-Baseline features
- docs/17 §5.5 already matches current guidance.
- **Popover API:** Newly available January 2025 ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Popover_API)), so Widely available around July 2027.
- **View Transitions:** same-document support became Newly available around October 2025. That is from general knowledge; MDN did not state it, so it is unverified. Do not build route transitions.

### 4.11 Hype versus durable

| Durable (adopt) | Hype, or wrong for SchoolOS (skip) |
|---|---|
| Neutral greys plus one accent; colour only for status | Glassmorphism / "Liquid Glass": `backdrop-filter` is costly on old hardware and hurts legibility ([uxdesign.cc](https://uxdesign.cc/apples-liquid-glass-isn-t-just-an-accessibility-blunder-it-s-an-environmental-one-too-08c593a87963); [Chameleon](https://www.chameleon.io/blog/liquid-glass)) |
| OKLCH ramps generated from a few inputs | Purple/indigo gradients, a centred hero plus three cards: the "AI slop" look ([prg.sh](https://prg.sh/ramblings/Why-Your-AI-Keeps-Building-the-Same-Purple-Gradient-Website); [925studios](https://www.925studios.co/blog/ai-slop-web-design-guide)). Warm cream with terracotta is the "second default" ([capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/)) |
| Command palette, with shortcuts shown in menus | Bento grids of decorative charts or invented numbers |
| Sticky headers, column control, density, bulk bars | Route transitions and scroll-driven animation inside the app |
| Validate after interaction, plus an error summary | Toasts for everything |
| Skeletons that mirror the layout, for waits over 1 s | Shimmer on sub-second loads |
| Tabular figures, right-aligned money | |

## 5. Constraints every option must respect

- **Baseline Widely available**, safe now: `oklch()`, `color-mix()`, `:has()`, `:user-invalid`, `text-wrap`, `<dialog>`, `dvh` after a `vh` fallback, `position: sticky`, `scroll-padding`, `accent-color`, reduced-motion and forced-colors media queries.
- **Not yet Widely available:** Popover, anchor positioning, View Transitions.
- **Re-check:** docs/17 still lists container queries as unused. Size queries have been Newly available since February 2023, so they are now Widely available. Confirm with the `modern-web-guidance` tool before changing that rule.
- **CSP** without `'unsafe-inline'` for styles in production.
- **Fonts** self-hosted only.
- **English first.** Telugu is dormant but must still work: no letter-spacing on data text, and keep the `:lang(te)` line height.
- **Layout and input:** 1366×768 as the baseline, usable keyboard-only, print as a first-class output.
- **No PII in `localStorage`** (office PCs are shared).

## 6. Three visual directions

All three keep the existing semantic token names, so screens change through the tokens, not through edits. I computed contrast from these OKLCH values with an OKLCH→sRGB conversion; `tokens.test.ts` must re-verify every pair.

### 6.1 Direction A, "Quiet ledger" (recommended)

```css
@theme {
  --color-canvas:         oklch(0.985 0.002 85);  /* #fbfaf9 */
  --color-surface:        oklch(1 0 0);
  --color-surface-muted:  oklch(0.97 0.003 85);   /* #f6f5f3 table header, filled input */
  --color-surface-hover:  oklch(0.955 0.004 85);
  --color-border:         oklch(0.92 0.004 85);   /* #e6e4e2 hairline */
  --color-border-control: oklch(0.62 0.006 85);   /* #888682 3.64:1 on white, 3.34:1 on muted */
  --color-ink:            oklch(0.22 0.006 85);   /* #1c1a17 17.3:1 */
  --color-ink-muted:      oklch(0.45 0.008 85);   /* #575550 7.4:1 */
  --color-ink-subtle:     oklch(0.53 0.008 85);   /* #6e6b67 5.3:1 white, 4.84:1 muted */
  --color-action:         oklch(0.22 0.006 85);   /* near-black primary button stays */
  /* Accent: owner picks one */
  --color-primary: oklch(0.50 0.13 258);   /* A1 ink blue  #2f62ac 6.06:1 */
  /* A2 school teal  oklch(0.50 0.085 175) #1c7360 5.73:1 (chroma kept in sRGB) */
  /* A3 indigo-violet oklch(0.48 0.15 285) #584cad 6.91:1 (closest to the "AI default"; least advised) */
  --color-success:     oklch(0.52 0.12 150);  /* #287c42 5.2:1 */
  --color-warning-ink: oklch(0.50 0.10 65);   /* #8a5619 6.2:1 */
  --color-danger:      oklch(0.53 0.19 27);   /* #c22826 5.8:1 */
  --radius-sm: 6px; --radius-md: 8px; --radius-lg: 10px; --radius-xl: 12px;
  --shadow-card: none;
  --shadow-popover: 0 1px 2px oklch(0 0 0 / 0.06), 0 8px 24px oklch(0 0 0 / 0.10);
}
/* Figures: PP Mori stays the UI face; digits, ₹ and separators come from self-hosted Inter
   with tabular figures, for numeric cells, KPIs and amounts only. */
@font-face { font-family: "SOS Figures"; src: url(<inter latin wght woff2>) format("woff2");
  font-weight: 100 900; unicode-range: U+0030-0039, U+002C, U+002E, U+20B9, U+2212, U+0025; }
.num { font-family: "SOS Figures", var(--font-sans); font-variant-numeric: tabular-nums; }
```

- **Accent choice:** with teal (A2), success green and the accent sit close together, so success pills need their icon. Blue (A1) is the safest.
- **Type scale:**
  - 14/20 UI;
  - 13/18 compact tables;
  - 16/24 reading;
  - 18/26 section headings;
  - 22/28 page title, PP Mori 600;
  - KPI 28/32 in `.num` at 600;
  - Instrument Serif only for one hero figure per dashboard.
- **Weights:** PP Mori's 400/600 rule is unchanged.
- **Group labels:** sentence-case 12px muted text instead of mono uppercase eyebrows. JetBrains Mono stays for IDs: admission number, PEN, APAAR and receipt number.
- **Risk:** it can look generic. Counter that with a confident accent, a simple distinctive wordmark, and real product detail (receipts, registers) instead of decoration.

### 6.2 Direction B, "Register paper" (marketing, public pages, print)

`--color-canvas: oklch(0.975 0.008 95)` (paper, `#f8f7f1`), ruled hairlines `oklch(0.89 0.012 95)`, ink `oklch(0.24 0.01 95)`, ledger-green accent `oklch(0.45 0.09 165)` (`#0e6549`, 7.1:1). Instrument Serif headlines, PP Mori body, mono IDs.
- It is distinctive and on-theme for schools, and it prints beautifully.
- In dense work screens, serif numerals slow scanning, and the cream look is a known "second default".

### 6.3 Direction C, "Bright campus"

This is today's look evolved: a tinted blue canvas `oklch(0.97 0.015 240)`, brand blue `oklch(0.55 0.17 255)`, tinted soft shadows, a 14px radius and coloured sidebar icons. It is the least work, but it reads as 2021–23, it is the look the owner wants to leave, and it is the least dense.

### 6.4 Comparison

| | A | B | C |
|---|---|---|---|
| 2026 SaaS feel | Yes | Editorial, distinctive | Dated |
| Density at 1366×768 | High | Medium | Low |
| Aligned figures and ₹ | Yes (SOS Figures) | With SOS Figures | With SOS Figures |
| Brand distinctiveness | Medium | High | Low |
| Migration cost | Medium | Medium | Low |
| Print | Good | Best | Good |

**Proposal:** A in both consoles; B on marketing, the public pages and printed outputs. The operator console stays visibly different: dark neutral chrome (`oklch(0.22 0.01 85)`) with an amber accent `#fcd34d` (already a token, 10.4:1 on the dark chrome) instead of violet. That is an owner decision.

## 7. Patterns

### 7.1 App shell

```
┌──────────┬───────────────────────────────────────────────────────────┐
│ S Wordmk │ Students / Ravi K.        [Search or jump to…  Ctrl K] 🔔 ? │ top bar 48px
│ School ▾ │───────────────────────────────────────────────────────────│
│ Today    │ Ravi K.  [Active]                  [Edit] [Issue TC] [⋯]   │ plain header row
│ Students │ Class 7-B · Adm. 2019/0412                                 │
│ …        │ Tabs: Details  Sources  Findings  Fees  Documents          │
│ ──────── │ content                                                    │
│ RK Clerk │                                                            │
└──────────┴───────────────────────────────────────────────────────────┘
 240px / compact 56px
```

- **Sidebar.**
  - Rows are 32px on fine pointers and 44px on coarse pointers.
  - Group headings are 12px, muted, sentence case.
  - The current item has a tint, a 2px accent bar and weight 600.
  - The sidebar sits on the canvas tone so it reads dimmer than the content.
  - Ctrl+\ toggles compact mode (this closes the docs/17 §8 open point).
- **Top bar.** Breadcrumb, palette trigger, bell and help. On phones: Menu, wordmark, a search icon (which opens the palette full screen) and the bell.
- **Page header.** A plain row with title, status, a one-line meta line and actions (one primary, the rest under "More"). It is optionally sticky on long record pages, with `scroll-padding-top`.
- **Command palette.** Native `<dialog>` with the APG combobox pattern. Groups:
  - Go to: the permission-filtered sidebar items.
  - Actions: Add student, Collect fee, Take attendance, New change request, Upload document, Ask.
  - Students: server search by name or admission number, scoped, with a 200 ms debounce and stale requests aborted.
  - Help.

  Recents store pages and actions only, **never record names**. Shortcuts are shown next to commands.

### 7.2 Tables (`DataTable` v2)

- **Density.** Comfortable (44px rows) by default; Compact (32px, 13px text) as a toggle remembered per table. Coarse pointers always get comfortable.
- **Header.** Sticky inside `TableScroll`, with `scroll-padding-top`.
- **Toolbar.** Search, 1–3 filter buttons, "More filters", active-filter chips with × and "Clear all", the result count, column visibility, density, and Export (step-up).
- **Selection.** Only on tables that have bulk actions. The bulk bar is pinned at the bottom of the card. The header checkbox selects the visible page. Sorting, paging or filtering clears the selection. Record-changing bulk actions go through `ActionDialog` with the row count and the consequence line.
- **Rows.** Whole-row link; a trailing "⋯" menu of at least 24px that is always visible on focus and touch; status pills with icons; money in `.num`, right-aligned, with ₹ in the header.
- **States.** Each is distinct:
  - first use: what this is, plus one action;
  - no results: show the filters and "Clear filters";
  - no permission: say who can grant access;
  - error: retry;
  - loading: skeleton only after 300–500 ms, a toolbar spinner on refetch.
- **Paging.** Server-side, 25/50/100 rows, "1–50 of 1,240", with the page number in the URL.

### 7.3 Forms

- Labels above fields; mark *optional* fields, not required ones.
- Validate on blur with `:user-invalid`. On submit, show an error summary that links to each field, and move focus to the summary.
- **Explicit save** for official records, fees, settings and structure, with a sticky footer showing "Changes not saved" (plus `scroll-padding-bottom`). **Autosave only drafts** (notices, circulars), with a "Saved 10:42" status, and never with PII in `localStorage`. Never autosave maker-checker changes.
- **Steppers** for imports, provisioning, promotions, certificate issue, change requests and messages.
- **Fee counter:** search the student (combobox), then the dues list in tab order, then the payment mode (segmented), then Enter records the payment, then the receipt opens with Print focused. Shortcut hints are on screen.

### 7.4 Dashboards ("Today")

- A status strip comes first, then at most 4 KPI cards, each linking to the filtered list behind it, then "Waiting for you", then recent activity. Sections appear by role (§2).
- KPI cards: a muted 13px label, a 28px `.num` value, a delta only when a real comparison exists, and a sparkline only when there is a real series. Never invent numbers.
- Platform dashboard: fleet health, invoices due and the support queue.

### 7.5 Motion (additions to docs/17 §5.5; every existing rule stays)

| Element | Motion |
|---|---|
| Command palette | Fade and scale 0.98→1, 150 ms in, 100 ms fade out, `--ease-out` |
| Bulk bar | Slide up 8px and fade, 150/100 ms |
| Menus | Fade and scale 0.97 from the trigger, 120 ms |
| Density, columns, sort, paging, route changes | None |

Optional, for the owner to decide: a CSS-only status region for background results (classes only, no injected `<style>`). The default stays inline `Alert live`.

### 7.6 Mobile

- **Keep:** the drawer, 44px coarse-pointer targets, bottom-sheet dialogs, stacked lists and reflowed fields.
- **Add:**
  - role bottom bars (§2);
  - a sticky bottom action bar on forms and record pages;
  - 16px inputs on coarse pointers, with `inputmode` and `enterkeyhint`;
  - a stacked or "key columns" view for the tables that still scroll sideways (registers, ledgers, plans, usage, audit).
- **Patchy networks:** offline and failed states keep what was typed. Pull-to-refresh is kept (no `overscroll-behavior: none` on `html`).

### 7.7 Component approach

1. Keep the in-house primitives on native elements.
2. Spike React Aria against Base UI for the Combobox, typeahead Menu, DatePicker (Indian date order) and Popover positioning. Measure bundle size and CSP violations, then write an ADR.
3. Do not adopt shadcn wholesale. Borrow its patterns, not its code.

### 7.8 Illustration

Use line-interior as the main style and teal-spot as the second; details are in `skills-review.md` §6. Serve them as `<img src="/illustrations/*.svg" alt="">` from `'self'`, or inline them with `aria-hidden`. They must have no `style` attributes, must survive print, and must be culturally neutral: Indian school-office props and clothing, no religious, festival or flag imagery.

## 8. Every page and what to change

The global changes reach every console page through the tokens, `PageHeader`, `Card`, `DataTable` and the shell. The notes below are only what each page needs beyond that. Paths are under `apps/web/src/app/`.

### 8.1 School: home and records (`[locale]/(school)/…`)

| Route | Change |
|---|---|
| `/` | "Today", role-aware (§2, §7.4); gradient removed |
| `/students` | Compact option; filters for class, section, status and has-findings; bulk export; shares the API with the palette's search |
| `/students/new` | Sectioned form, error summary, sticky footer |
| `/students/[studentId]` | Header with status and meta; tabs Details, Sources, Findings, Fees, Documents, Certificates; "Ask about this student" |
| `/students/[studentId]/certificates/new` | Stepper with a print-preview step |
| `/imports`, `/imports/[importId]`, `/imports/[importId]/sheet` | Stepper across the import; the sheet grid gets new tokens only (its keyboard model stays) |
| `/register-photos`, `/register-photos/[batchId]`, `/register-photos/[batchId]/next`, `/register-photos/items/[itemId]` | Split review view (image beside fields), keyboard next and previous, progress in the header |
| `/documents`, `/documents/new`, `/documents/[documentId]`, `/documents/[documentId]/sheet` | Type and date filters; upload dropzone with a progress bar; preview with a metadata side panel |
| `/certificates`, `/certificates/[certificateId]` | Status pills; detail page is the print view with a toolbar |
| `/registers` | Print-first: picker plus A4 preview; a phone view |

### 8.2 School: classroom and checks

| Route | Change |
|---|---|
| `/attendance` | Teacher, phone-first: one tap per student, "All present", sticky save, works on slow networks |
| `/marks` | Keyboard grid on desktop (sheet-grid model) and a per-student stepper on phones; per-cell validation; `.num` |
| `/flags`, `/flags/[flagId]` | Calm list with reason chips; evidence; a purpose-limit note |
| `/findings`, `/findings/[findingId]` | Filter chips, bulk assign, side-by-side values, decision with the consequence line |
| `/findings/readiness`, `/findings/readiness/[studentId]` | Readiness % as the page's main figure; one column per portal |
| `/findings/rules`, `/findings/runs/[runId]` | Settings-style list; run progress and result counts |
| `/change-requests`, `/change-requests/new`, `/change-requests/[changeRequestId]` | Inbox tabs (Waiting for me / Mine / All); before/after diff with evidence; stepper for new requests |
| `/apaar`, `/apaar/students/[studentId]` | Readiness table with mismatch pills; compare view |
| `/exports`, `/exports/new/precheck`, `/exports/new/student-list`, `/exports/[exportId]` | Stepper (pick, check, download); result counts; step-up download |

### 8.3 School: Ask

| Route | Change |
|---|---|
| `/ask`, `/ask/c/[conversationId]` | Keep the docs/17 §5.3 patterns; AI panel on an accent-tinted surface instead of the blue gradient; compact citation chips; entry points from records |
| `/ask/history`, `/ask/search`, `/ask/memory`, `/ask/verified` | Standard list treatment; history searchable from the palette |

### 8.4 School: work, messaging, fees

| Route | Change |
|---|---|
| `/tasks` | Inbox grouped by due date; quick complete (optimistic UI allowed only for non-record tasks) |
| `/circulars`, `/circulars/[documentId]`, `/notices`, `/notices/[noticeId]` | Draft autosave with a status line; publish through `ActionDialog` |
| `/messages`, `/messages/slips` | Template and audience stepper; delivery-status table |
| `/notifications` | Read/unread filter |
| `/fees` | Linked KPIs: today, dues, approvals |
| `/fees/collect` | Keyboard-first counter (§7.3) |
| `/fees/receipts`, `/fees/receipts/[receiptId]` | Compact table, date-range and payment-mode chips; receipt page as A5/A4 preview |
| `/fees/accounts/[accountId]` | Ledger with a running balance and a sticky totals row |
| `/fees/approvals` | Approvals inbox with consequence lines |
| `/fees/daily`, `/fees/dues` | Filter bar, totals strip, table, export |
| `/fees/setup` | Settings sections; fee heads as an editable table; sticky save |

### 8.5 School: settings and administration

| Route | Change |
|---|---|
| `/settings/school`, `/settings/retention`, `/settings/data-export`, `/settings/whatsapp`, `/settings/billing` | One settings layout: sub-navigation (tabs on phones), sections, sticky save; plan meters on billing |
| `/settings/structure`, `/settings/structure/promotions`, `/settings/structure/years/[yearId]/promotions` | Structure as a tree or table with inline add; promotions as a stepper with preview counts |
| `/settings/users`, `/settings/users/new`, `/settings/users/[userId]` | Role chips; permissions summary |
| `/settings/tally`, `/settings/tally/ledgers` | Mapping table with validation states |
| `/break-glass`, `/break-glass/[grantId]` | Danger tone; grant timeline |
| `/support`, `/support/[ticketId]` | Ticket list and thread view |
| `/audit`, `/audit/verify` | Compact log, mono timestamps, filter chips; verify shows progress and the result |

### 8.6 Operator console (`[locale]/platform/…`)

| Route | Change |
|---|---|
| `/platform` | Fleet and billing dashboard; dark neutral chrome with an amber accent |
| `/platform/schools`, `/platform/schools/[schoolId]` | Power table (plan, tier, status, heartbeat); school tabs |
| `/platform/provision` | Stepper with progress |
| `/platform/plans`, `/platform/subscriptions`, `/platform/invoices`, `/platform/invoices/[invoiceId]`, `/platform/usage` | Money in `.num`, status pills, invoice print view |
| `/platform/flags`, `/platform/fleet`, `/platform/announcements`, `/platform/support`, `/platform/support/[ticketId]`, `/platform/break-glass`, `/platform/operators`, `/platform/audit` | Table and toolbar pattern; two-person consequence dialogs; platform-only palette |

### 8.7 Public, marketing and session pages

| Route | Change |
|---|---|
| `/welcome` (marketing home, or dedicated-host sign-in), `/features`, `/pricing`, `/about`, `/security` | Keep the `.mk` scale; Direction B; real product screenshots in the new look; no centred hero with three cards; line-interior illustrations |
| `(public)/v`, `(public)/v/[code]` | Big clear verdict, server-rendered, works on 2G |
| `(public)/p/view`, `(public)/p/expired` | Phone-first, large type, one action, about 100 KB budget, Telugu-ready |
| `/choose-school`, `/no-access`, `/signed-out`, `/step-up-complete`, `[...rest]` (404) | `MinimalShell` restyle; each page says what to do next; teal-spot spot art |
| `/dev/sign-in`, `/dev/ui` | Dev only. `/dev/ui` becomes the living reference (density, palette, bulk bar, error summary, figures, illustrations, `?data=worst`) |

## 9. Rollout (each step can ship on its own)

The owner's timing: the design system, tokens, components, shell and palette now; page redesigns after R2-2D and R2-2E merge.

1. ADR plus a docs/17 v1.0 draft. It covers Direction A, OKLCH tokens, SOS Figures, operator chrome, and the headless-library spike result.
2. Tokens: OKLCH values behind the existing names; `tokens.test.ts` parses `oklch()`; the contrast table is regenerated.
3. Shell: 240px sidebar, top bar, plain `PageHeader`, role bottom bars; update the e2e responsive and axe suites.
4. Command palette: navigation and actions first, student search second.
5. `DataTable` v2.
6. Forms: error summary, `:user-invalid`, sticky footer, stepper.
7. Screens in order of daily use: fee counter and receipts, attendance and marks (teacher), students, findings and readiness, "Today", change requests, imports, the rest; operator console last.

## 10. Decisions for the product owner

1. Direction A for the consoles, B for marketing, public pages and print.
2. Accent: A1 ink blue (recommended), A2 teal, or A3 indigo-violet.
3. The SOS Figures face (Inter digits and ₹ inside PP Mori text) for aligned numbers.
4. Neutral canvas on the dashboards too.
5. Operator chrome: dark neutral with amber, instead of violet.
6. Ctrl+K as the palette shortcut. It overrides Chrome's own Ctrl+K on that page, as GitHub does.
7. Toasts: inline only, or a CSS-only status region.
8. Role bottom bars on phones for teachers and principals.
9. Illustrations: line-interior main, teal-spot second, either drawn by an agent with a review loop or commissioned once.

## 11. Sources

**Primary:**
- Linear: [How we redesigned the Linear UI](https://linear.app/now/how-we-redesigned-the-linear-ui), [Behind the latest design refresh](https://linear.app/blog/behind-the-latest-design-refresh), [A design reset](https://linear.app/blog/a-design-reset)
- Vercel: [Geist tokens](https://vercel.com/design.md), [Geist README](https://cdn.jsdelivr.net/npm/geist@1.7.2/README.md)
- [Tailwind theme variables](https://tailwindcss.com/docs/theme)
- MDN: [oklch](https://developer.mozilla.org/docs/Web/CSS/color_value/oklch), [light-dark](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/light-dark), [Popover API](https://developer.mozilla.org/en-US/docs/Web/API/Popover_API), [:user-invalid](https://developer.mozilla.org/en-US/docs/Web/CSS/:user-invalid), [text-wrap](https://developer.mozilla.org/en-US/docs/Web/CSS/text-wrap), [View Transition API](https://developer.mozilla.org/en-US/docs/Web/API/View_Transition_API), [CSP style-src](https://developer.mozilla.org/docs/Web/HTTP/Headers/Content-Security-Policy/style-src)
- web.dev: [Baseline](https://web.dev/baseline), [Optimize INP](https://web.dev/articles/optimize-inp), [QuintoAndar INP](https://web.dev/case-studies/quintoandar-inp), [Color themes with Baseline](https://web.dev/articles/baseline-in-action-color-theme)
- W3C: [2.5.8 Target Size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html), [2.4.11 Focus Not Obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html)
- [NN/g Skeleton screens](https://www.nngroup.com/articles/skeleton-screens/)
- GOV.UK: [Error message](https://design-system.service.gov.uk/components/error-message), [Validation](https://design-system.service.gov.uk/patterns/validation)
- [CMS.gov Error validation](https://design.cms.gov/patterns/Forms/error-validation/)
- [Cloudscape Table view](https://cloudscape.design/patterns/resource-management/view/table-view/)
- [Android navigation patterns](https://developer.android.com/design/ui/mobile/guides/layout-and-content/layout-and-nav-patterns)
- [SAP Fiori navigation bar](https://www.sap.com/design-system/fiori-design-android/v25-8/components/m3-standard-components/navigation-bar/usage)
- [Inter](https://rsms.me/inter/)
- [React Aria Components (npm)](https://npmjs.com/react-aria-components)
- [Base UI v1 (InfoQ)](https://infoq.com/news/2026/02/baseui-v1-accessible/)

**Secondary (opinion):**
- Dashboards and tables: [AdminLTE](https://adminlte.io/blog/25-best-dashboard-design-examples-for-2026-real-products-principles/), [saasui trends](https://saasui.design/blog/7-saas-ui-design-trends-2026), [saasui tables](https://saasui.design/blog/saas-data-table-ux-patterns), [925studios](https://925studios.co/blog/saas-dashboard-design-examples-2026), [setproduct](https://www.setproduct.com/blog/effective-dashboard-design-principles)
- Command palette: [techinterview.org](https://www.techinterview.org/post/3233475212/build-command-palette-cmd-k/), [webfactory](https://www.webfactoryltd.com/command-palette-ux-speeding-up-saas/)
- Component libraries: [pkgpulse](https://www.pkgpulse.com/guides/shadcn-ui-vs-base-ui-vs-radix-components-2026), [designrevision](https://designrevision.com/alternatives/radix-ui)
- Loading and optimistic UI: [virtuslab](https://virtuslab.com/blog/frontend/ux-patterns-beyond-raw-performance), [coddy.tech](https://coddy.tech/docs/react/useoptimistic), [SitePoint](https://www.sitepoint.com/react-useoptimistic-production-patterns-for-instant-ui-updates/)
- Liquid Glass: [uxdesign.cc](https://uxdesign.cc/apples-liquid-glass-isn-t-just-an-accessibility-blunder-it-s-an-environmental-one-too-08c593a87963), [Chameleon](https://www.chameleon.io/blog/liquid-glass)
- "AI slop" look: [prg.sh](https://prg.sh/ramblings/Why-Your-AI-Keeps-Building-the-Same-Purple-Gradient-Website), [925studios slop](https://www.925studios.co/blog/ai-slop-web-design-guide), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/)
- CSS and CSP: [modern-css.com](https://modern-css.com/playground/css-color-mix), [centralcsp](https://centralcsp.com/docs/style-src)


## 12. Decisions taken (2026-10-18)

The owner picked accent A1 (ink blue) and asked the lead to choose the remaining options without further questions. The lead took the recommended option in each case:

1. Direction A for both consoles; Direction B for marketing, public parent and verification pages, and print.
2. Accent A1, ink blue `oklch(0.50 0.13 258)`.
3. SOS Figures face (self-hosted Inter digits and ₹ inside PP Mori text) for aligned numbers.
4. Neutral canvas everywhere, dashboards included.
5. Operator console chrome: dark neutral with an amber marker, so operators always know they are in the control plane.
6. Ctrl/⌘+K opens the command palette.
7. Toasts: a CSS-only `role="status"` region (no injected styles, CSP-safe); errors stay inline on the form.
8. Phone bottom bars for teachers and principals.
9. Illustrations: line-interior style first, teal-spot second, drawn as inline SVG by an agent with a review loop.

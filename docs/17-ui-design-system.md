# 17 · UI Design System (web)

| Field            | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Version          | 1.2 · 2026-10-18 |
| Changes          | 1.2: mobile pass (§5.7 "Mobile pass as built"): the phone bottom bar steps aside for the on-screen keyboard; docked bars (`SaveBar`, the bulk bar, the Ask composer) stand on it (`--dock-offset`); WCAG 2.4.11 scroll padding for the console bars; menus and popovers stay on screen; the palette stays at the top on phones; `SaveBar` `placement`, `SegmentedControl` full width on phones, `Pagination` thumb row, `DataTableV2` selection on phones and `noResultsState`, controlled `ActionDialog`/`FormDialog`, `HeroFigure`, `printer` icon; Lenis off on touch screens; the blue AI gradient tokens removed (the Ask panel is the accent tint). 1.1: page wave (operator console, public, marketing, session pages): Direction B "Register paper" on the marketing site and the parent and verification pages (§5.6, §5.8); line-interior illustrations in `public/illustrations` with `Illustration` and `EmptyState illustration` (§5.6); Lenis smooth wheel scrolling on the home page only, with its dependency check (§5.6); operator console lists on `DataTableV2` with filters in the toolbar, SOS Figures for money and times, a 2px amber marker on the operator chrome (§5.2); plain `MinimalShell`; `/dev/ui` shows the v1.0 components, figures, Register paper and every illustration. 1.0: "Quiet ledger" (owner decisions 2026-10-18; docs/research/2026-10-ui/report.md §6.1, §12): OKLCH warm neutral tokens behind the same names, one ink-blue accent, fixed status colours, one flat canvas (no gradient), hairline cards with shadows only on overlays, SOS Figures (Inter digits and ₹, tabular) for numbers, sentence-case group labels, density toggle (§3.4), wordmark (§3.5); shell: 240/56px sidebar, 48px top bar with "Where you are", Ctrl/⌘+K command palette, preferences menu, Ctrl+\ compact sidebar, phone bottom bars for teachers and principals, dark neutral operator chrome with amber (§5.2, §5.9); plain PageHeader; new components Menu, Popover, Drawer, Toaster, Pagination, ErrorSummary, Steps, SaveBar, FilterChips, Kbd, DataTableV2, CommandPalette (§4); page recipe for the redesign wave (§5); contrast table recomputed from OKLCH (§7). 0.9: mobile and touch (§5.7): breakpoints, 44px touch targets on coarse pointers, stacked record lists and reflowing field tables below 640px, dialogs as bottom sheets, safe areas, keyboard-safe composer, tap to edit in the sheet grid, tap citations. 0.8: calmer app (design-research review, owner decisions): solid tinted status pills instead of gradients (§4, §7); a near-neutral canvas behind dense record screens, chosen once by the shell (§3.2); separate app and marketing radius scales (§3); "Ask on WhatsApp" beside "Talk to us" (`SOS_PUBLIC_WHATSAPP_NUMBER`) and the pricing page as a packaging explainer without prices (§5.6); consequence line in decision dialogs and numeric table columns (§4). 0.7: fixes from the review of the motion and marketing work: reveals use the Web Animations API with a root margin (never stuck hidden at high zoom, shown at once for keyboard focus), the phone menu closes when focus or a click leaves it, fading panels are `inert`, Motion features load after hydration, the marketing press feedback respects reduced motion and focus clears the sticky header; e2e for every public page. 0.6: §5.6 public marketing pages (layout, settings, reveal pattern, dedicated hosts); §5.5 motion (tokens shared with Motion, CSP-safe pattern, what moves and what never does, reduced motion), §3.1 type scale and weights for PP Mori, §2 rule 6, component states (Button loading, dialog exits). 0.5: §5.4 English first, Telugu hidden behind `SOS_TELUGU_ENABLED` (ADR-0036); §2 rules 2 and 8, §3 fonts. 0.4: §5.3 Ask chat patterns (anatomy, motion tokens, reduced motion, a11y), sidebar sub-lists (`sub`, `subActivePattern`). 0.3: §5.2 one sidebar (the icon rail and the separate list panel are gone): anatomy, compact mode, drawer, themes, a11y; §7 sidebar contrast pairs. 0.2: §5.1 responsive layout (spacing scale, menu drawer, TableScroll, dialogs, checks). 0.1: new document: tokens, fonts, primitives, shells, do/don't, contrast table (UI refresh foundation) |
| Requirements     | NFR-A11Y-001 (WCAG 2.2 AA), NFR-I18N-001 (English; Telugu hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036), SEC-010 (CSP, self-hosted assets)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Related          | 02-PRD §8 (UX principles), 13 §5 (TypeScript/Next.js standards), CLAUDE.md §10                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Code             | `apps/web/src/app/globals.css` (tokens), `apps/web/src/components/ui/` (primitives, exported from `index.ts`), `apps/web/src/components/console/` (CommandPalette, DataTableV2), `apps/web/src/components/shell/` (shells)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Living reference | `/dev/ui`: every primitive and variant with synthetic content. Local development only (same guard as `/dev/sign-in`: `next dev` + local stub issuer; a 404 everywhere else)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

---

## 1. The look in one paragraph

**Quiet ledger** (v1.0, owner decision 2026-10-18). One **flat warm near-white canvas**
(`oklch(0.985 0.002 85)`) everywhere, dashboards included; content sits on **white surfaces
with a hairline border** and no shadow. Shadows exist only on things that float: popovers,
menus, the command palette, dialogs, drawers and toasts. Greys are warm and low in chroma
(hue 85). There is **one accent, ink blue** `oklch(0.50 0.13 258)` (#2f62ac): links, focus,
the current navigation item, selection and info. Status colours are fixed and independent of
the accent (success green, warning amber, danger red), always with text or an icon. The
primary button stays **near-black** (one per view). Text is PP Mori (two weights, 400 and
600); **figures** (amounts, counts, KPIs, numeric cells) use **SOS Figures**, Inter's
tabular digits and ₹ mapped into the sans, so columns line up; Instrument Serif is kept for
one page-level hero figure or a marketing headline only. Group labels are sentence case,
12px, semibold; JetBrains Mono is for IDs only (admission number, PEN, receipt number).
Navigation is **one grouped sidebar** (240px, compact 56px) plus a **48px top bar** with
"Where you are", **Ctrl/⌘+K "Search or jump to"** and a preferences menu (row density). Every
page starts with a **plain header row** (title, status, one line, actions). The operator
console (`/platform`) has **dark neutral chrome with amber markers** so operators always know
they are in the control plane. Motion is subtle (150-200ms, transform and opacity) and off
under reduced motion. Light theme only.

## 2. Hard rules (never traded for looks)

1. **WCAG 2.2 AA.** Text 4.5:1 (3:1 for large text and UI boundaries). Every token pair a primitive
   uses is listed in §7 and recomputed by `components/ui/tokens.test.ts`; add a row there when you add
   a pair. Gradients are checked at their **lightest** stop. Focus is always visible (3px ring,
   `--color-focus`, the accent; amber on the operator chrome). Colour is never the only
   signal: pills carry text, rings carry a number, the switch knob moves.
2. **Telugu (only while it is switched on, §5.4).** Noto Sans Telugu is then the fallback of the
   sans and mono stacks, `:lang(te)` keeps the taller line height, and the serif display and mono
   eyebrow switch back to the sans with normal spacing in Telugu (`.font-display:lang(te)`,
   `.eyebrow:lang(te)`). Never set tight line heights or letter-spacing on text that may be
   Telugu: data (a name copied from a register) can still be in Telugu script.
3. **CSP (SEC-010).** No `style` attributes, no external resources. Colours come from classes;
   SVG geometry uses presentation attributes (`strokeDashoffset`, `points`), which CSP allows.
   Fonts are self-hosted (`@fontsource` packages and the licensed PP Mori WOFF2 files; `font-src 'self'`).
4. **Baseline Widely Available** only (CLAUDE.md §10). Used: `oklch()` colours, CSS gradients, `:has()` (segmented
   control, drawer scroll lock), `:modal`, native `<dialog>`, `<details>`, `accent-color`,
   `env()`, `dvh` (after a `vh` fallback), `@media (prefers-reduced-motion)`,
   `@media (forced-colors)`, `:where()` (the `collapsed:` variant), `inert` (via the modal
   dialog). Not used: `backdrop-filter` (slow office PCs), anchor positioning,
   container queries, `appearance: base-select`.
5. **Print.** Gradients, shadows and tints disappear; cards print as plain bordered boxes; chrome
   (`data-print="hide"`) is hidden; A4 margins; Telugu line height 1.8.
6. **Motion.** Few, short motions on transform and opacity only, from the shared tokens (§5.5):
   press feedback, dialog and drawer enter/exit, the command palette, menus and popovers, the
   bulk bar and toasts, the notification panel, live alerts, a changed KPI number, content
   replacing a skeleton, the skeleton shimmer and the Ask chat (§5.3).
   Never on page load, keyboard navigation, tables or high-frequency actions. Under
   `prefers-reduced-motion` nothing moves (Motion keeps opacity fades only). No `style`
   attribute is ever server-rendered (§5.5, CSP). The sidebar's compact switch is instant (no
   width animation). The public marketing pages (§5.6) add a one-time illustration entrance and
   scroll reveals on the same tokens and CSP pattern.
7. **`cn()` joins classes, it does not merge them.** A `className` can add spacing, width or
   layout, but it cannot reliably override a colour, padding or radius the component already sets
   (CSS order decides, not class order). Use the variant, size, `tone` or `padding` props instead,
   or ask for a new variant.
8. **English first (ADR-0036).** With `SOS_TELUGU_ENABLED` off (the default) a screen shows no
   Telugu at all: no language switch, no Telugu label, field, column, option, preview or font.
   Hide Telugu through `useTeluguEnabled()` (§5.4), never by deleting it.

## 3. Tokens (`globals.css`, Tailwind v4 `@theme`)

Use them as Tailwind utilities (`bg-surface`, `text-ink-muted`, `border-border`, `bg-accent-soft`,
`rounded-xl`, `num`). Values are `oklch()` (Baseline widely available); `tokens.test.ts`
converts each to sRGB and re-checks every pair in §7. Old names still work and point at the
new values (`primary` and `brand` are the accent, `canvas-from/via/to` and `canvas-neutral`
are the canvas).

| Group    | Tokens (value) | Use |
| -------- | -------------- | --- |
| Canvas   | `canvas` oklch(0.985 0.002 85) #fbfaf9; aliases `canvas-from`, `canvas-via`, `canvas-to`, `canvas-neutral` | The body; the school sidebar and top bar sit on it |
| Surfaces | `surface` white; `surface-muted` 0.97 0.003 85 (table header, filled inputs, dialog footers); `surface-sunken` 0.955 0.005 85 (segmented track, sidebar hover); `surface-hover` 0.955 0.004 85 (row hover) | Cards, panels, controls |
| Borders  | `border` 0.92 0.004 85 (hairline, decorative); `border-soft` 0.86 0.008 85 (secondary buttons); `border-control` 0.62 0.006 85 (inputs, checkboxes, switch: 3:1); `border-strong` (legacy) | |
| Ink      | `ink` 0.22 0.006 85 #1c1a17; `ink-muted` 0.45 0.008 85 #575550; `ink-subtle` 0.53 0.008 85 #6e6b67 | Body, secondary, notes and group labels |
| Action   | `action` (= ink), `action-hover`, `on-action` | The near-black primary button (one per view) |
| Accent   | `accent` 0.50 0.13 258 #2f62ac; `accent-strong` 0.40 0.11 258 (hover, selected text); `accent-soft` 0.95 0.025 258 (current nav item, selected rows, chips); `on-accent`. Aliases `primary`, `primary-hover`, `primary-soft`, `brand`, `brand-strong`, `focus`, `chart-1` | Links, focus ring, current item, selection, info. Never a second accent |
| Status   | `success` 0.52 0.12 150 (graphics), `success-ink` 0.40 0.10 150 on `success-soft` 0.95 0.04 150; `warning-ink` 0.50 0.10 65 on `warning-soft` 0.95 0.05 70; `danger` 0.53 0.19 27 on `danger-soft`; `info-ink`/`info-soft` (the accent); `positive-*` (= success); `violet-*`, `teal-*` (Review and Done pills only) | Pills, alerts, toasts, error summary |
| AI       | none of its own: `accent-soft` with an `info-border` hairline and `ink` text | The Ask panel (`AiPanel`), the Ask composer and the assistant mark are on the accent tint; no gradient |
| Charts   | `chart-1` accent, `chart-2` amber, `chart-3` teal, `chart-4` violet, `chart-track` | Series colours (3:1 on white) |
| Operator | `platform` 0.22 0.01 85 (dark neutral), `platform-hover`, `platform-ink`, `platform-muted`, `platform-accent` amber 0.86 0.15 88, `platform-accent-ink`, `platform-soft` | Control-plane chrome only |
| Radii    | App: `xs` 4, `sm` 6, `md` 8 (buttons, inputs), `lg` 10 (menus, alerts), `xl` 12 (cards, dialogs, palette), `2xl` 14, `3xl` 16, `full`. Marketing (`.mk`): unchanged larger scale | Same `rounded-*` names; the scope picks the scale |
| Shadows  | `shadow-card` none (kept so the class compiles); `shadow-raised` 1px (selected segment, buttons); `shadow-popover` (overlays only) | Hairlines carry structure |
| Fonts    | `font-sans` PP Mori; `font-figures` / `num` (SOS Figures then the sans, tabular); `font-display` / `font-serif` Instrument Serif; `font-mono` JetBrains Mono (IDs) | See §3.1 |
| Density  | `--row-height` 44px / 32px compact, `--cell-px`, `--cell-py`; variant `compact:` | §3.4 |

Component classes (in `@layer components`): `.eyebrow`, `.select-chevron`, `.skeleton`,
`.canvas-neutral` (§3.2), `.quote-gradient`, `.platform-chrome` (amber focus ring), `.pressable` (press feedback, §5.5).
Motion classes: `.dialog-motion`, `.alert-in`, `.value-tick`, `.content-in` (§5.5).

### 3.1 Type scale

Sizes are `@theme` tokens with their line heights (`--text-*--line-height`), used as the normal
Tailwind utilities. Hierarchy comes from **size and weight**, never from tracking: page titles
and table cells can hold Telugu-script data (a student's name), where letter-spacing breaks
shaping. Only the serif display figures get optical tracking (`-0.01em`, reset by
`:lang(te)`).

| Token  | Size / line height | Use                                                  |
| ------ | ------------------ | ---------------------------------------------------- |
| `xs`   | 12 / 18            | captions, table headers, badges, timestamps          |
| `sm`   | 14 / 20            | UI text: buttons, inputs, sidebar, tables, card body |
| `base` | 16 / 24            | reading text, page descriptions                      |
| `lg`   | 18 / 26            | card, dialog and section titles (`font-semibold`)    |
| `xl`   | 20 / 28            | page title on phones                                 |
| 22/28  | `sm:text-[1.375rem] sm:leading-7` | page title (`PageHeader` `h1`, `font-semibold`) |
| 28/32  | `text-[1.75rem] leading-8` | `KpiCard`, `StatCard` number (`num`, semibold) |
| `4xl`, `5xl` | 36/40, 48/48 | Instrument Serif hero figure (one per dashboard) and marketing only |

Weights: PP Mori has 200/400/600 only, so there are **two weights, default (400) and
`font-semibold` (600)**. `font-medium` renders as 400 and `font-bold`/`font-extrabold` snap to a
600 or a synthetic bold, so none of the three is used anywhere (`components/ui/font-weights.test.ts`
fails on them in every source file; it has no allowlist and must never get one). The public
pages may also use `font-extralight` (200, served by PP Mori) for the large `h1` (§5.6). Pick
on purpose:

- **Semibold:** headings, card/section/dialog titles, legends and field labels (including a label
  `<dt>` set in `text-ink`), the name that identifies a row (student, school, user, ledger, the
  row's link), standalone action links ("Open", "Review", "Manage", "Switch school"), buttons,
  chips, tabs and segmented options, badges and pills, error and warning lines, key numbers (a
  price, a progress figure, a count badge) and the value in a boxed fact (muted label over value).
- **Default:** body and helper text, muted labels (`text-ink-muted` `<dt>`), values in long
  label-beside-value lists (the certificate's lines, settings rows, a review summary), links inside
  prose (an answer's Markdown), live status and progress lines, the AI panel's large greeting
  (size carries it), and anything in `font-mono` (JetBrains Mono ships 500 only, so the default
  already renders 500 and semibold would be synthesised).

Numbers (v1.0): amounts, counts, KPIs and numeric table cells use the `num` utility
(`font-family: var(--font-figures)` + `tabular-nums`). **SOS Figures** is an `@font-face` in
`globals.css` whose `unicode-range` covers only digits, `+ , - .`, `%`, `−` and `₹`, pointing
at the self-hosted Inter variable files from `@fontsource-variable/inter` (OFL-1.1; the same
hashed files the layout loads for Inter, so no extra download). Every other glyph stays PP
Mori. `Td numeric`, `Th numeric`, `KpiCard`, `StatCard`, `Pagination` and `DataTableV2` use it.
KPI and stat values are 28/32 semibold figures; Instrument Serif is for one page-level hero
figure or a marketing headline only.

UI text: PP Mori (Pangram Pangram, commercial web licence; `src/app/fonts/pp-mori/`, `@font-face` in `globals.css`; weights 200/400/600, so `font-medium` renders as 400; no tabular figures). Open fonts (all OFL-1.1, pinned exact in `apps/web/package.json`): `@fontsource-variable/inter`
(variable weights), `@fontsource/instrument-serif` (400), `@fontsource/jetbrains-mono` (500),
`@fontsource/noto-sans-telugu` (400/600/700, Telugu subset). Every file declares
`unicode-range`, so a page downloads only the scripts it shows. Noto Sans Telugu is **not**
bundled: `globals.css` names no Telugu font, and the locale layout links
`/fonts/telugu/noto-sans-telugu.css` (faces plus the two stacks above) only while Telugu is on;
that route answers 404 while it is off (§5.4).

### 3.2 Canvas: one flat canvas

Since v1.0 every console page, the school home and the platform dashboard included, is on
the same flat `canvas` (owner decision 2026-10-18, report §12.4). The body has no gradient
(`tokens.test.ts` fails if one comes back). `components/shell/canvas.ts` keeps its API
(`canvasFor`, `ShellCanvas`, the shell's `canvas` prop) so older callers compile, but it always
answers `"neutral"`, which paints the same colour. Never paint a background per screen. The
marketing pages keep their own `.mk` bands (Direction B, a later wave).

### 3.3 Radii: two scales, one set of class names

The signed-in app uses the calm scale in
`@theme` (cards, dialogs and panels 10-14px; buttons and inputs a step smaller, 8px). The
public marketing pages keep their larger, softer corners: `.mk` (the `MarketingShell` root,
`features/marketing/marketing.css`) redefines `--radius-sm` … `--radius-2xl`, and Tailwind's
`rounded-*` utilities read those variables, so a component needs no marketing variant.
Write `rounded-xl` for a card in both places; never hard-code a pixel radius.
`tokens.test.ts` pins both ranges.

### 3.4 Density: comfortable or compact

Comfortable is the default (44px rows). The viewer can choose **compact** (32px rows, 13px
table text) from the top bar's preferences menu ("Your preferences" → Row density) or the
palette ("Use compact rows"). The choice is `<html data-density="compact">` (absent means
comfortable), remembered per viewer on this computer (`localStorage` `sos.density`, a UI
preference only, every access in try/catch, `lib/ui-prefs.ts`) and applied before the first
paint by the root layout's nonce'd head script (`SHELL_STATE_SCRIPT`, with the compact
sidebar), read in React through `useDensity()` (`components/shell/density.ts`,
`useSyncExternalStore`, so hydration never mismatches).

- CSS: the `compact:` variant applies while an ancestor carries `data-density="compact"` and
  no nearer ancestor says `comfortable`, **and only on fine pointers** (touch keeps 44px
  targets). Variables `--row-height`, `--cell-px`, `--cell-py` follow the same rule.
- `Table`, `Th`, `Td` and `DataTable` follow the viewer's choice unless a page pins
  `density`. `DataTableV2` also offers a per-table choice in its Columns panel, remembered
  under `sos.table.<id>.density`.

### 3.5 Wordmark and icons

`Wordmark` (`components/shell/Brand.tsx`): an inline SVG mark (a 22px rounded square in the
accent with two white "ledger lines"; amber with dark lines on the operator chrome) plus
"SchoolOS" (`common.appName`) in PP Mori semibold. The text is the accessible name; the mark
is `aria-hidden`. `BrandMark` is the mark alone. Used by the sidebar, the phone top bar, the
minimal shell and the marketing header, footer and dedicated sign-in card. `size="lg"` for
sign-in cards and marketing.

Icons stay the in-house inline SVG set in `ui/Icon.tsx` (24×24, 2px stroke, `aria-hidden`,
CSP-safe). **lucide-react was not added**: the existing set already covers the shell and the
new components, and a new dependency would need the licence, maintenance and CVE review of
CLAUDE.md §11 for no gain (lucide-react is ISC-licensed; revisit only if the page wave needs
many new icons, pinning an exact version at least two weeks old after `npm audit`). Add a
missing icon to `Icon.tsx` in the same style.

## 4. Components (`@/components/ui`)

Import from the files or from `@/components/ui` (index). All are Server-Component-safe unless
marked (client).

| Component                                                                         | Props (beyond children/className)                                                                                                                                                                                                              | Notes                                                                                                                                                                                                                                                                                                                     |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Button`                                                                          | `variant`: `primary` (near-black, default) · `primary-dark` (alias) · `brand` (blue) · `secondary` · `ghost` · `danger` · `inverse` (white, on dark/blue); `size`: `sm` · `md` · `lg`; `loading` (spinner + `aria-busy`); all `<button>` props | `type="button"` by default; press feedback (§5.5)                                                                                                                                                                                                                                                                         |
| `ButtonLink`, `buttonClasses(variant, size)`                                      | `href`, `variant`, `size`                                                                                                                                                                                                                      | Links that look like buttons (`buttonClasses` for plain `<a>` to BFF routes)                                                                                                                                                                                                                                              |
| `IconButton`, `iconButtonClasses`                                                 | `label` (required accessible name), `variant`: `secondary` · `ghost` · `primary` · `danger`; `size`: `sm` 32 · `md` 40 · `lg` 48; `dot`                                                                                                        | Round, hairline border. Put the unread count in `label` too                                                                                                                                                                                                                                                               |
| `Icon`, `ICON_NAMES`                                                              | `name`                                                                                                                                                                                                                                         | Decorative 24×24 stroke icons; a size class replaces the 20px default                                                                                                                                                                                                                                                     |
| `Card`                                                                            | `title`, `eyebrow`, `description`, `actions`, `headingLevel` 2/3, `padding` `none`·`sm`·`md`·`lg`, `tone` `default`·`muted`·`outline`                                                                                                          | `<section>` named by its title                                                                                                                                                                                                                                                                                            |
| `CardHeader`, `cardClasses({padding, tone})`                                      | as above, `headingId`                                                                                                                                                                                                                          | For cards that are not a `<section>`                                                                                                                                                                                                                                                                                      |
| `PageHeader`                                                                      | `title` (the page's one `h1`), `description`, `actions`, `badge`, `breadcrumb` (`Crumb[]`), `eyebrow`, `plain` (kept, no effect)                                                                                                              | v1.0: a plain row on the canvas, never a card. A `breadcrumb` of two crumbs ("Home › This page") is not shown: the top bar says where you are; three or more (a record under its list) show above the title |
| `Breadcrumb`                                                                      | `items: {label, href?}[]`, `label`                                                                                                                                                                                                             | Last crumb is `aria-current="page"`                                                                                                                                                                                                                                                                                       |
| `Eyebrow`                                                                         | `as` `p`·`span`·`div`·`h2`·`h3`, `tone` `muted`·`ink`·`brand`·`inverse`, `id`                                                                                                                                                                  | Mono uppercase label                                                                                                                                                                                                                                                                                                      |
| `Badge`                                                                           | `tone`: `neutral` · `info` · `success` · `warning` · `danger` · `platform` · `violet` · `teal`                                                                                                                                                 | Soft status label                                                                                                                                                                                                                                                                                                         |
| `Pill`                                                                            | `variant`: `progress` · `review` · `done` (solid tints: blue, violet, teal) · `dark` · `date` · `tag` · `sample` · `positive` · `negative` · `command`; `size` `sm`·`md`; `icon` (name, or `null`)                                             | Always with text. Status variants: soft tint, strong text, hairline and a decorative icon (clock, eye, check; `icon={null}` removes it), so meaning never rests on colour                                                                                                                                                 |
| `DeltaPill`                                                                       | `value` ("+2.7%"), `direction` `up`·`down`·`flat`, `label` (read instead of the value)                                                                                                                                                         | Dark pill with arrow                                                                                                                                                                                                                                                                                                      |
| `StatCard`                                                                        | `label`, `value` (string or null), `unavailableLabel`, `hint`                                                                                                                                                                                  | Inside a `<dl>`; 28px `num` figure |
| `KpiCard`                                                                         | `label`, `value`, `unavailableLabel`, `delta` (`{value, direction?, label?}`), `comparison`, `sparkline`, `aside`, `href` (the label links to the list behind the number)                                                                       | `role="group"` named by the label; 28px `num` figure; never invent numbers |
| `Sparkline`                                                                       | `values: number[]`, `label` (text alternative, required), `color` 1–4, `area`                                                                                                                                                                  | Inline SVG, `aria-hidden` + sr-only label                                                                                                                                                                                                                                                                                 |
| `ProgressRing`                                                                    | `value` 0–100, `label` (required), `size` `sm`·`md`·`lg`, `color` 1–4, `showValue`                                                                                                                                                             | `role="progressbar"` with `aria-valuetext`                                                                                                                                                                                                                                                                                |
| `Input`, `Textarea`, `TextField`, `TextAreaField`, `Field`, `controlClasses`      | unchanged                                                                                                                                                                                                                                      | Filled look (soft grey, 3:1 boundary)                                                                                                                                                                                                                                                                                     |
| `SearchInput`                                                                     | `label` (required), `labelVisible`, `icon`, `wrapperClassName`, input props                                                                                                                                                                    | `type="search"`; put it in `<form role="search">` with a "Filter" button                                                                                                                                                                                                                                                  |
| `Select`, `SelectField`                                                           | unchanged                                                                                                                                                                                                                                      | Native `<select>` with CSS chevron                                                                                                                                                                                                                                                                                        |
| `Toggle` (client)                                                                 | `label`, `description`, `checked`/`defaultChecked`, `onCheckedChange`, `disabled`, `name` (hidden input "on"/"off"), `labelFirst`                                                                                                              | `<button role="switch" aria-checked>`; green when on. For settings that apply at once                                                                                                                                                                                                                                     |
| `SegmentedControl` (client)                                                       | `legend` (required), `legendVisible`, `options` `{value,label,disabled?}[]`, `value`/`defaultValue`, `onValueChange`, `name`, `size` `sm`·`md`, `block` (`"phone"` default: full width below 640px; `true` always; `false` never)                                                                                        | Fieldset of native radios: arrows, form value, announcements for free                                                                                                                                                                                                                                                     |
| `Tabs` (client)                                                                   | `label`, `items`, `defaultTabId`, `variant` `segmented` (default) · `underline`                                                                                                                                                                | WAI-ARIA tabs; panels stay in the DOM                                                                                                                                                                                                                                                                                     |
| `TabNav`                                                                          | `label`, `items`, `activeId`, `variant`                                                                                                                                                                                                        | Tabs that are links (`aria-current`)                                                                                                                                                                                                                                                                                      |
| `Table`, `THead`, `TBody`, `Tr`, `Th`, `Td`                                       | `Table` `density` `comfortable` (≈56px rows) · `compact`; `stickyFirstColumn` (below md); `reflow` (§5.7); `Th`/`Td` `numeric`                                                                                                                 | Light header, dividers, hover. `numeric` (counts, amounts): right-aligned, tabular figures, no wrap; set it on the header and the cells                                                                                                                                                                                   |
| `TableScroll`                                                                     | `label` (required: names the region), `framed` (card frame)                                                                                                                                                                                    | Wrap every hand-built `Table` in it: the table scrolls sideways inside a focusable region, never the page                                                                                                                                                                                                                 |
| `DataTable`                                                                       | + `density`, `stickyFirstColumn`, `stacked` (§5.7); column `numeric`, `stack`                                                                                                                                                                  | Loading, error, empty states; renders `TableScroll framed`                                                                                                                                                                                                                                                                |
| `EmptyState`                                                                      | `title`, `body`, `action`, `icon`                                                                                                                                                                                                              | Say what to do next                                                                                                                                                                                                                                                                                                       |
| `LoadingState`, `Skeleton`                                                        | `label`, `rows`, `variant` `rows`·`cards`                                                                                                                                                                                                      | `role="status"`; shimmer off under reduced motion                                                                                                                                                                                                                                                                         |
| `Alert`                                                                           | unchanged (`tone`, `title`, `live`)                                                                                                                                                                                                            |                                                                                                                                                                                                                                                                                                                           |
| `Dialog` (client), `ActionDialog` (client)                                        | unchanged; `ActionDialog` `consequence`; `ActionDialog` and `FormDialog` take a controlled `open` + `onOpenChange` (leave out `triggerLabel` and no trigger renders), so a "More" menu item opens them; every close reports `onOpenChange(false)` and focus returns to the element that had it on opening                                                                                                                                                                                                        | Native `<dialog>`, restyled. `consequence`: one plain sentence of what confirming changes ("Nothing on the student's record changes."), shown just above the buttons and read as the confirm button's description; use it on consequential actions                                                                        |
| `Avatar`                                                                          | `name`, `size` `sm`·`md`·`lg`, `decorative`                                                                                                                                                                                                    | Initials only, never photos                                                                                                                                                                                                                                                                                               |
| `AvatarStack`                                                                     | `names`, `label` (required), `max`, `size`                                                                                                                                                                                                     | One image named by `label`, "+N"                                                                                                                                                                                                                                                                                          |
| `Timeline`                                                                        | `items` `{id, title, time?, body?, chips?, status? done·current·pending, statusLabel?}[]`, `label`                                                                                                                                             | Ordered list, mono times                                                                                                                                                                                                                                                                                                  |
| `AiPanel`                                                                         | `greeting` (its heading), `eyebrow`, `description`, `suggestions` `{id,label,href?                                                                                                                                                             | onSelect?}[]`, `suggestionsLabel`, children (question form)                                                                                                                                                                                                                                                               | For the Ask screens: a panel on the accent tint (`accent-soft`, `info-border` hairline, `ink` text, the sparkles icon in `accent`; the focus ring is the usual accent one). Answers go in normal cards **with source chips** (invariant 8) |
| `QuoteBlock`                                                                      | `label`, children                                                                                                                                                                                                                              | Pale green quote                                                                                                                                                                                                                                                                                                          |
| `SidebarNav` (client)                                                             | `label`, `items` **or** `sections` (`NavSection[]`: `{id, label, items}`; items `{href, label, icon?, exact?, nested?, activePattern?, sub?, subActivePattern?}`), `theme` `school`·`platform`, `id`                                           | One current page (`aria-current="page"` + accent bar); group headings name their lists; `collapsed:` compact styles. Used by the shell's `Sidebar`. `sub`: a client sub-list under an item (Ask: New chat, recents, All chats, Memory), hidden when compact; `subActivePattern`: paths whose current link is inside `sub` |
| `UsageMeter`, `LanguageSwitcher`, `Value`, `Label`, `SecretOnce`, `ApiErrorAlert` | unchanged | |
| `Menu` (client) | `label` (names the trigger and the menu), `entries` (`action` · `link` · `radio` · `separator` · `label`; `danger`, `disabled`, `icon`), `trigger` (default "⋯"), `variant` `icon`·`button`·`bare`, `align` | APG menu button: Enter/Space/Down open on the first item, Up on the last; arrows, Home/End, first-letter jump; Escape returns focus; Tab and a click outside close. Row menus, "More" actions, the top bar's preferences |
| `Popover` (client) | `trigger`, `label` (icon-only trigger), `title` (names the panel), `align` | Disclosure + non-modal `role="dialog"` panel: Escape returns focus, outside click and focus leaving close it. Column chooser, filter choices, help. (Native Popover API is not Baseline widely available until mid-2027) |
| `Drawer` (client) | `open`, `onClose`, `title`, `description`, `closeLabel`, `footer` | End-edge side sheet on the native modal `<dialog>` (focus trapped, page inert, Escape and backdrop close, focus returns). Record details, filter panels |
| `ToastProvider`, `useToast()` (client) | `show({title, body?, tone: success·info·warning})` | CSS-only toasts in one polite `role="status"` region (bottom end), max 3, 6s each, held on hover/focus, dismiss button. Mounted once by `AppShell`. Background results only; **form errors stay inline** |
| `Pagination` (client) | `from`, `to`, `total?`, `hasPrevious`, `hasNext`, `onPrevious`, `onNext`, `pageSize`, `pageSizes`, `onPageSizeChange` | "1–50 of 1,240" (polite), rows per page, Previous/Next; works with cursor APIs |
| `ErrorSummary` (client) | `errors` (`{fieldId, message}[]`), `title` ("There is a problem"), `attempt` | GOV.UK pattern: at the top of a form after a failed submit, takes focus, each message links to and focuses its field; the field still shows its own message |
| `Steps` | `steps` (`{id, label, href?}[]`), `current`, `label`, `failed` | Ordered list: done (check + "Done", may link back), current (`aria-current="step"`), upcoming. `failed`: the current step stopped (danger mark, "Stopped here", `ui.steps.failed`); the steps after it stay upcoming |
| `SaveBar` | `dirty`, children (Save, Discard), `savedLabel`, `placement` `inset` (default) · `page` | Sticky footer of an explicit-save form: "Changes not saved" / "No changes"; last child of the `<form>`. `inset` bleeds to the edges of the padded card it sits in; `page` is a framed bar of its own for a form laid on the page without a card. On phones it stands on the bottom bar (`--dock-offset`, §5.7) |
| `HeroFigure` | `label`, `value`, `labelId`, `size` `md` · `lg`, children | The one big figure of a page or card (readiness share, a saved receipt number): muted label, then the value in `num`, a step smaller on phones, wrapping anywhere |
| `FilterChips` | `chips` (`{id, label, value}[]`), `onRemove`, `onClear` | Active filters with named remove buttons ("Remove filter Class: 7-B") and "Clear all" |
| `Kbd` | children | A key as printed ("Ctrl", "K") |

**Console patterns** (`@/components/console`):

| Component | Props | Notes |
| --- | --- | --- |
| `DataTableV2` (client) | `caption`, `columns` (`ColumnV2`: `Column` + `name`, `hideable`, `defaultHidden`), `state` (`Loadable`), `rowKey`, `id` (remembers hidden columns and density), `toolbar`, `chips`/`onRemoveChip`/`onClearFilters`, `emptyTitle`/`emptyBody`/`emptyAction`, `noResultsTitle`, `onRetry`, `selection` (`rowLabel`, `actions(selected, clear)`), `footer` (Pagination), `stacked`, `density` | The record list (§4.3 below). Use it for new and redesigned lists; `DataTable` stays for existing callers |
| `CommandPalette` (client) | `open`, `onClose`, `items` (`{id, label, group, href? \| onRun?, hint, keywords, icon, shortcut}`), `groups` | Mounted by `AppShell` (§5.9); pages do not mount their own |

### 4.3 DataTable v2

- **Toolbar** (white card header): the page's search (`SearchInput` in a `<form role="search">`)
  and 1-3 filter controls in `toolbar`; the **Columns** button (popover: show/hide columns, the
  first stays; this table's row density) on the right.
- **Chips** under the toolbar for active filters, with "Clear all".
- **Header** sticky inside the table's own scroll box (`max-h` 70dvh, focusable, named by the
  caption), header tint `surface-muted`.
- **Rows**: 44px (32px compact); hover `surface-hover`; numeric columns `num`, right-aligned
  (put ₹ in the header, "Due (₹)"); a trailing `Menu` for row actions.
- **Selection** only when the table has bulk actions: a named checkbox per row, "Select all rows
  on this page" (indeterminate when some are), a **bulk bar** pinned to the bottom of the card
  with "n selected", the actions and "Clear selection". New rows (paging, sorting, filtering)
  clear the selection. Record-changing bulk actions confirm through `ActionDialog` with the
  count and the consequence line.
- **Row link**: a row's main link may stretch over the whole row (`after:absolute
  after:inset-0`): every `<tr>` is `position: relative`, so any other control in the row (a
  checkbox, a `Menu`, a second link) needs `relative z-[1]` to stay clickable. The stacked
  cards keep the stretched link; their field and action rows already lift links and buttons.
- **States**: loading (skeleton), error (with "Try again" when `onRetry`), unavailable, first use
  (`emptyTitle` + one action), no results while filters are active ("Clear filters").
  `noResultsState={false}` keeps the first-use state while chips show (chips that are views,
  like "Waiting for me", rather than filters).
- **Paging** server-side under the table (`footer` = `Pagination`, 25/50/100).
- No motion on rows, sorting, paging or density (§5.5). Below 640px `stacked` shows cards
  (inside the table's own frame, no second border); with `selection` each card gets a
  labelled "Select" checkbox, so bulk actions work on phones too. The bulk bar docks above the
  phone bottom bar (§5.7).

### 4.4 Forms

Labels above fields; mark *optional* fields, not required ones. Errors under the field
(`Field`) and, after a failed submit, an `ErrorSummary` at the top that takes focus. **Explicit
save** for official records, fees, settings and structure: a `SaveBar` as the form's last
child; never autosave official records or maker-checker changes (drafts such as notices may
autosave with a "Saved 10:42" line). Multi-step jobs show `Steps`. One primary button per
view.

Shells (`@/components/shell`): `SchoolShell` (`permissions`, `features`, `schoolName`,
`canSwitchSchool`, `account`, `topbarActions`, `languages`, `banner`) and `PlatformShell`
(`permissions`, `account`) render `AppShell` (client): skip link, **one `Sidebar`** (beside the
page from lg, in the menu drawer below lg), the top bar ("Where you are", "Search or jump to",
bell, language, preferences; below lg the "Menu" button and wordmark), `<main id="main">`, the
command palette, the phone bottom bar and the toast region. See §5.2 and §5.9. `MinimalShell`
(`wide` for tables) and `Wordmark` serve pages outside a school.

### 4.1 Sheet grid: editable tables (`@/features/sheets`)

`SheetGrid` (client) shows a spreadsheet page (an import's staged rows, an XLSX/CSV document) on
the `Table` primitives; use it for any table whose cells are edited in place, never `DataTable`.
Built for the import sheet (`/imports/{id}/sheet`, linked from the "Check rows" step and the
import page), the document sheet (`/documents/{id}/sheet`) and the read-only preview of the
first 10 rows on the document page (FR-IMP-008, FR-DOC-009..011).

| Part                     | Rule                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Layout                   | `TableScroll framed` with `max-h-[70dvh]`: the rows scroll sideways and down inside the labelled, focusable region, never the page (375px to 1366×768). Compact density on the neutral canvas; the row number column stays in view below md                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Header                   | Sticky (`top-0`, header tint): column letter (mono), the file's heading (semibold), what the column fills ("Fills: Date of birth") or "Not imported", and a lock for restricted (C3) columns                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Rows                     | Row number as the row header (`th scope="row"`, red when the row has errors); optional check column with a status pill (`Valid`, `Has errors`, `Added` …) or a warning `Badge` ("Has warnings"), then the problems in plain words                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Cells                    | Empty cells show "—" (read as "Empty"); restricted cells "Hidden" with a lock (never a value); formulas as mono text with "Formula, not run"; saved changes "Changed" (info tint); changes kept on the page or still being saved "Not saved yet" (warning tint, icon). Colour is never the only signal                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Keyboard (WAI-ARIA grid) | One tab stop (roving `tabindex`): Tab enters on the last active cell, Tab again leaves. Arrows move (at an edge the key is left to the browser so the region scrolls), Home/End and Ctrl+Home/Ctrl+End jump; the focused cell scrolls into view (`block/inline: nearest`, `scroll-pt` clears the sticky header)                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Editing                  | Enter or F2 (or double click) opens a one-line input with the value; Enter saves, Escape cancels and returns focus to the cell, Tab saves and leaves the grid (Shift+Tab: before it). Read-only cells carry `aria-readonly`; a read-only grid says why in an info `Alert` above it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Checks before sending    | NFC, trimmed, blank clears; one line, at most 1,000 characters; a full Aadhaar number is refused with how to fix it ("enter only the last 4 digits"). The API applies the same rules                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Announcements            | One polite `role="status"` line per screen for outcomes ("Row 3 saved. It has no problems now.", "Downloaded …"); "Saving…" is shown, not announced. Failures use `ProblemAlert` with the API's code, never cell values                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Find on this page        | `SearchInput` + "In column" `SelectField` above the grid filter the rows of the current page (restricted columns are never searched); a polite count says how many match. Paging (100 rows) stays server-side                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Unsaved work             | `useUnsavedChangesWarning`: an open editor with a changed value, or document changes not yet saved, make links inside SchoolOS ask first, the browser's Back and Forward buttons inside SchoolOS ask first, and the browser ask before the tab closes. Back/Forward (Baseline only, no Navigation API): the first warning pushes one guard history entry (same URL, the router's state kept); a capture-phase `popstate` listener holds the Next.js router back and asks; Cancel pushes the guard again (page and changes stay), OK goes on without a second "Leave site?". Once nothing is unsaved the entry stays but goes quiet, and the next Back skips it, so one press still leaves. Several warnings on a page share one entry; the hook removes its listeners on unmount |
| Conflicts (412)          | Imports: the sheet reloads and the person makes the change again. Documents: the changes stay on the page with "Download my changes (CSV)" and "Reload the sheet"; saving waits for the reload                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Downloads                | CSV (UTF-8 with BOM) and Excel buttons in the page header, through the BFF with the step-up prompt; the note under the grid says changes are included, Aadhaar-like numbers masked and formulas turned into text                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Motion                   | None (§2 rule 6: never on tables or keyboard navigation)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

## 5. Page recipe (for the page-redesign agents)

Redesign a page **inside** the shell; never add chrome. The shell already gives the sidebar,
the top bar with "Where you are" (section › page) and the palette.

```tsx
<div className="space-y-5">
  <PageHeader
    title={t("title")}                       // the page's one h1, 22px semibold
    badge={<Badge tone="success">{t("status.active")}</Badge>}  // record pages only
    description={t("description")}           // one line
    // breadcrumb only for records under a list (3+ crumbs): Home › Students › Sample student A
    actions={
      <>
        <ButtonLink href="/students/new">{t("add")}</ButtonLink>        {/* the one primary */}
        <Menu label={t("more")} variant="button" trigger={t("more")} entries={…} />
      </>
    }
  />
  <section aria-label={t("kpi.label")} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
    {/* ≤ 4 KpiCard, real numbers only, each with href to the list behind it */}
  </section>
  <DataTableV2
    id="students" caption={t("list.caption")} captionHidden
    columns={columns} state={rows} rowKey={(row) => row.id}
    toolbar={<form role="search" …><SearchInput label={t("search")} name="q" />…</form>}
    chips={chips} onRemoveChip={removeChip} onClearFilters={clearAll}
    emptyTitle={t("empty.title")} emptyBody={t("empty.body")} emptyAction={…}
    footer={<Pagination … />} stacked
  />
</div>
```

- **Spacing**: `space-y-5` between page blocks, `gap-3` between KPI cards, `gap-4` between
  cards; cards pad themselves (`Card` `md`). No shadows on cards; hairline only.
- **Headings**: one `h1` (PageHeader); sections `h2` (Card title, 16-18px semibold); cards
  inside cards `headingLevel={3}`.
- **Lists**: `DataTableV2` for record lists (selection only with bulk actions); `Table` +
  `TableScroll` for grids, ledgers and registers; `SheetGrid` for editable sheets (§4.1).
- **Numbers**: `num` on every amount, count and KPI; numeric columns `numeric`; ₹ in the
  header; Indian grouping from `lib/format`.
- **Status**: `Badge` for record states, `Pill` status tints for workflow states, always with
  text; colour never alone.
- **Forms** (§4.4): labels above, errors under the field plus `ErrorSummary` after a failed
  submit, `SaveBar` for explicit save, `Steps` for multi-step jobs, one primary button.
- **Empty states**: first use = what this is + one action; no results = "Clear filters"; no
  permission = who can grant access; error = "Try again".
- **Overlays**: `ActionDialog` (with `consequence`) for decisions, `Drawer` for details or
  filters, `Menu` for row and "More" actions, `Popover` for small choices, `useToast()` for
  background results (never for form errors).
- **Strings**: every new string in `en` (and `te` for parity; `i18n/messages.test.ts`); reuse the
  `ui` namespace for generic component text.
- **Do not** change BFF routes, API calls or query keys while restyling; do not add
  dependencies; no `style` attributes; keyboard and 1366×768 first, then phones (§5.7).
- **Checks** per page: its feature tests, `app/english-only.test.tsx`, `npm run lint`,
  `typecheck`, `test`, `build` (apps/web), and the e2e responsive and a11y suites.

### 5.1 Responsive layout

Every screen must work at **1366×768** (office PC, the design baseline) and **375×812** (phone),
in English (and in Telugu while it is switched on, §5.4): no horizontal page scroll, nothing past the screen or its card edge, no
clipped text (a decorative, `aria-hidden` sample-data illustration with a caption may be
cropped, §5.6), touch targets of at least 24×24 px (WCAG 2.5.8; inline links in a sentence and
well-spaced small targets are the exceptions). `e2e/responsive.spec.ts` checks all of it for
every screen (`make e2e` with `E2E_STAND_IN=1`, on every pull request); `e2e/audit/responsive.audit.ts`
sweeps ten viewports with screenshots (`make e2e-audit`, nightly).

- **Spacing scale** (`globals.css`): `--page-gutter` 16px on phones, 24px from md, 32px from lg
  (utility `px-page`, outside the consoles); `--shell-gutter` 16/24px (`shell-gutter`, inside
  the consoles); `--content-max` 108rem (`shell-frame`). Both gutter utilities add the
  safe-area insets (`viewport-fit=cover`), which are 0 on desktops.
- **Full height**: `min-h-viewport` / `h-viewport` write `100vh` first and `100dvh` second, so
  browsers without dynamic viewport units keep the `vh` value.
- **Shell**: one sidebar, beside the page from lg, in a modal drawer below lg (§5.2).
- **Cards** carry `min-w-0`, so in a grid or flex row they shrink to their track and a wide
  child scrolls inside instead of pushing the page wider.
- **Tables** always sit in `TableScroll` (or `DataTable`): `position: relative` keeps sr-only
  captions inside the scroll box. Use `stickyFirstColumn` when the first cell names the row.
- **Long strings**: `body` has `overflow-wrap: break-word`; `dd` values and the page title wrap
  anywhere; use `break-anywhere` for emails, IDs and URLs in flex rows.
- **Dialogs** are `w-[min(<n>rem,calc(100vw-2rem))]` and `m-auto` from 640px and bottom sheets below it (§5.7); padding tightens below sm
  and the footer buttons share the row on phones. The native modal keeps them inside the
  viewport and scrolls a tall body.
- **Buttons** wrap a long (Telugu) label below sm instead of overflowing.
- **Motion**: see §5.5. Buttons fade colour only on fine-pointer hover and only without
  `prefers-reduced-motion`; under it every transition and animation is cut to 0.01ms
  (`globals.css`). The drawer slides in (260ms, drawer curve) and out (150ms) only without
  reduced motion.
- **Print** is unchanged by all of this: chrome is hidden, scroll regions print in full, table
  headers wrap and A4 content stays inside the page (checked in `e2e/responsive.spec.ts`).

### 5.2 Shell: the one sidebar

There is exactly **one** navigation column. The icon rail and the separate list panel of 0.2
are gone (product owner, 2026-09-29). `components/shell/Sidebar.tsx` is rendered by `AppShell`
from the same props in two places: beside the page (`data-sidebar-mode="inline"`, lg and up) and
inside the menu drawer (below lg, only while open). Never both are visible or in the
accessibility tree.

**Anatomy, top to bottom**

| Part       | School                                                                                                                                               | Platform                                    |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Brand      | `Wordmark` (accent mark + "SchoolOS"), a link home; the collapse button at the right                                                                | Same, amber mark, link to `/platform`       |
| Context    | "Current school" + the school's name (GET `/me/schools`); "Switch school" for people in several schools                                              | The amber "Platform admin" badge            |
| Navigation | `<nav aria-label="Main">`, permission- and feature-filtered groups under sentence-case 12px headings; each item is icon + label in a 32px row (44px on touch); scrolls on its own | `<nav aria-label="Platform">`, same pattern |
| Account    | `<section aria-label="Your account">`: initials, name (heard as "Signed in as …"), role (system roles by name), "Lock now"                           | Same with "Sign out"                        |

The **top bar** (48px, sticky, on the canvas with a hairline under it) holds, from lg, **"Where
you are"** (`<nav aria-label="Where you are">`: the current item's section, then the item, a
link back to its list on a sub-page; computed with `activeHref` from the same `sections` the
sidebar renders, so it never names a page the menu hides), then **"Search or jump to…"**
(`Ctrl K`, opens the palette, §5.9), the notification bell, the language switch and the
**preferences** menu ("Your preferences": Row density, Search or jump to). Below lg: the "Menu"
button, the wordmark (text hidden below sm) and the same tools. Page titles stay in each
page's `PageHeader`. Nothing is shown twice: "Switch school", the signed-in name and "Lock now" moved
from the top bar into the sidebar; the idle-timeout warning is mounted once by the shell
(`AppShell` `session`), never inside the sidebar.

**States.** Item: muted text and icon; hover: filled row (`surface-muted`, platform
`platform-hover`) with full-contrast text; keyboard focus: the 3px focus ring (accent; amber on
the platform chrome); current page: `aria-current="page"`, `accent-soft` row, semibold text and a 2px
accent bar at the start edge (the accent; platform amber `platform-accent`), so the state
never relies on colour alone. Exactly one item is current (the most specific match;
`activePattern` for sub-pages).

**Breakpoints.**

- **lg (1024px) and up:** sticky, full-height sidebar (`h-viewport`), 240px wide (compact 56px);
  the menu scrolls inside it (`overflow-y: auto`, `overscroll-behavior: contain`) while the
  header and the account stay put. At 1366×768 the page keeps 1126px minus gutters.
- **Below lg:** the "Menu" button (44px) opens the same sidebar in the **modal `<dialog>`
  drawer** (`.drawer`, 20rem or the screen minus 48px): focus moves to its close button and
  cannot leave it (the page behind is inert and does not scroll), Escape, the close button, a
  tap on the dimmed page, following a link or widening the window to lg close it, and focus
  returns to the button. It renders its content only while open, so there is one "Main"
  landmark. It is never compact.

**Compact mode.** The collapse button ("Collapse menu" / "Expand menu") or **Ctrl+\\** switches the wide
sidebar to icons only. Labels, headings and the school name stay in the DOM as `sr-only`, so
every link keeps its accessible name; headings become thin dividers. On hover and keyboard focus
a label appears beside the item (`.sidebar-tip`: fixed, so the scrolling menu does not clip it;
`aria-hidden`, because it repeats the name; it stays while the pointer is on it and Escape hides
it, WCAG 1.4.13; placed through the CSSOM, never a `style` attribute). The choice is remembered
per viewer on this computer: `localStorage` `sos.sidebar` (a UI preference only, never tokens or
personal data), every access in try/catch. The root layout runs a tiny inline script in `<head>`
(with the CSP nonce) that sets `<html data-sidebar="collapsed">` before the body is parsed, so
the compact width is right on the first paint (no layout shift); the `collapsed:` Tailwind
variant (`globals.css`) styles only inside the inline sidebar. React reads the attribute with
`useSyncExternalStore` (server snapshot "expanded"), so hydration never mismatches. Without
storage the sidebar simply starts expanded.

**Themes.** School: the sidebar and top bar sit on the canvas tone (so they read quieter than
the white content) with hairlines. Operator console: dark neutral sidebar and top bar
(`platform-chrome`: amber focus ring), amber mark, badge and active bar, and (v1.1) a 2px amber
marker along the top edge of the top bar and the sidebar; the phone bottom bar uses the
platform colours (owner decision 2026-10-18). Operator lists are `DataTableV2` with their
filter form in the toolbar (`FilterCard bare`), per-table preferences under `platform.*` ids;
money, counts, rates and times use SOS Figures (`Figure`, `numeric` columns), mono only for
IDs (plan version, audit sequence). Pairs in §7.

**Telugu.** Every label is in `messages/*.json` (`shell` namespace plus the existing nav keys);
the language switch in the top bar shows only while Telugu is switched on (§5.4).
Labels wrap to a second line instead of being cut off (no `truncate` in the sidebar); names and
school names use `break-anywhere`.

**Print.** Sidebar, top bar, drawer and label carry `data-print="hide"`; A4 pages are unchanged.

**Checks.** `components/shell/AppShell.test.tsx`, `SchoolShell.test.tsx`,
`ui/primitives.test.tsx` (vitest); `e2e/responsive.spec.ts` (drawer on a phone, sidebar and
compact mode at 1366×768, en and te, no horizontal scroll) and `e2e/a11y.spec.ts` (axe clean
expanded and compact, one Main landmark).

Web-platform features used here and their status: `dvh` (Baseline 2022, with the `vh`
fallback), `env(safe-area-inset-*)`, `:has()` and `:modal` (Baseline widely available),
media query range syntax, `overscroll-behavior`, `overflow-wrap: anywhere`, native modal
`<dialog>` (inert background, Escape close request).

### 5.3 Ask chat patterns

Ask the school is a chat (FR-KB-005, FR-KB-008, FR-KB-012; code in `features/ask/`). It lives
inside the one shell: **no second sidebar**. Conversation history is the Ask item's sub-list in
the sidebar (§5.2) and the All chats page.

**Routes.** `/ask` (new chat), `/ask/c/{conversationId}` (a conversation, deep-linkable; `#m-{query
id}` scrolls to one message and highlights it), `/ask/history` (All chats), `/ask/memory` (Manage
memory), `/ask/search` and `/ask/verified` (unchanged). `/ask` and `/ask/c/{id}` share the
`(chat)` layout, which mounts one streaming controller (`AskChatProvider`) and the chat screen,
so a new chat keeps streaming (and keeps focus in the composer) while the URL moves to its
conversation on the answer's `meta`. Leaving the chat stops the answer (the API records it
`cancelled`).

**Anatomy, top to bottom** (a full-height white card, `.chat-canvas`):

| Part           | What it is                                                                                                                                                                                                                                                                                                 |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Header         | The chat's title as the page's `h1` (wraps, never cut off), "Chat options" (disclosure: pin, rename, delete, Manage memory, All chats) and the Ask tabs                                                                                                                                                    |
| Empty state    | Greeting with the first name ("Good morning, Lakshmi") and the school, the composer centred, four example cards (they only fill the box; nothing is sent until the member asks)                                                                                                                            |
| Thread         | `role="log"` with `aria-live="off"`, max 48rem wide. Each turn: the question as a right-aligned bubble (`surface-sunken`), then the answer (`<article aria-label="Answer">`) full-width beside the AI mark                                                                                                 |
| Answer         | Live status line → streamed preview ("Draft answer, not checked yet", caret) → the checked `final` text as restricted markdown with superscript citation chips → outcome alerts (search-only, not found, refused, error) → Sources (cards) → actions → memory notes → follow-up chips (latest answer only) |
| Composer       | Sticky at the bottom over a white fade (`.chat-dock`); auto-growing textarea (max 40vh, then scrolls), toolbar with the memory indicator, a counter from 800 of 1000 characters, and the send button that becomes Stop; hint and shortcuts below                                                           |
| Jump to latest | Floating pill above the composer when the reader scrolled up; a dot when new text arrived                                                                                                                                                                                                                  |

**Streaming semantics** (docs/06 §5.1, unchanged): `delta` is an unchecked preview; `final`
replaces it (crossfade) and later `token`s are ignored; `status` events drive the live line
(Understanding your question… · Searching school documents… 4 found · Searching your past chats… ·
Reading student records… · Writing the answer…), which folds into "Worked for 4 seconds · 3
sources" (a native `<details>` listing the steps). Events are folded as they arrive but painted at
most once per animation frame, and only the streaming turn re-renders (`ChatTurn` is memoised).
The preview holds back half-typed markdown (`**`, `[`, table rows, bare list markers) until it
closes, and is revealed word by word with `requestAnimationFrame` at a pace that grows with the
backlog (never splitting a Telugu word). The user's question appears at once and the status line
within a frame; a failed request puts the question back into the box. After Stop the partial
preview stays, marked "Stopped", with "Ask again".

**Markdown** (`markdown-parse.ts`, in-house, no dependency): paragraphs, bold lines for headings,
lists (one nested level), GFM tables (in a focusable `table-scroll` region), quotes, code,
bold/italic/strike, inline code. No HTML is ever rendered (tags are dropped as text); link targets
other than `sos://` are dropped and their label stays text; `sos://` links open the source's
screen. Only `[n]` markers of the answer's own citations become chips.

**Citations.** A chip is a link to its source card (`Source 1: <title>`); hover or keyboard focus
opens a popover (title, page, quote, "Open"), lazy-loaded, fixed-positioned by script through the
CSSOM, flipped above when there is no room, closed by Escape (focus stays on the chip), by
focus leaving or by scrolling (WCAG 1.4.13). Source cards: number, title link, page, the quote (at most 300 characters),
"Download version n" for documents. A source the member can no longer open reads "Source you can
no longer open", without text; the API then withholds that answer too (`answer_withheld`), which
reads "Answer hidden: … because you can no longer see one of its sources" with no copy or "Save as
verified answer". Past-chat sources (`sos://conversation/{id}#q{query}`) open that message.

**Stored answers still `streaming`** (being written in another window, or cut off unrecorded)
read "Answer not finished" with "Ask again", never as an error; while one is under five minutes
old the chat is read again every 10 seconds, so it appears when ready.

**Actions** (32px icon buttons with names and tooltips; always shown on the latest answer and on
touch screens, on hover and focus elsewhere; while streaming the row keeps its height but is
hidden so nothing jumps): copy (plain text with `[n]` and a numbered source list), ask again
(`regenerate_of`), helpful / not helpful (reason codes), "Save as verified answer"
(`kb.verified_answer.manage`), and "‹ 1 / 2 ›" between versions. The latest question can be
edited in place (`edit_of`; Enter sends, Escape cancels). "Answered from a recent identical
question" (`meta.cached`) offers "Get a fresh answer". "Earlier messages were summarised…" is a
quiet divider when the API says so.

**Memory.** "Memory updated · Manage memory" under an answer; a suggestion card ("Remember
this?") that saves only on Save (confirm) and deletes on Dismiss. The composer shows "Memory on"
(link to Manage memory, with a tooltip) when the school and the member have it on. Manage memory:
what memory is and is not, the switch (disabled with the reason when the school switched it
off in school settings, `ai_memory_enabled`), items with edit/delete and save/dismiss for
suggestions, add (1-200 characters), "Forget everything" (confirm). While memory is off for the
member, adding, editing and saving are disabled with the reason (deleting still works); at 30
items adding is disabled ("Memory is full"). Every refusal says why and what to do, from the
API's codes: a 422 field code (`memory_personal_number`, `memory_date`, `memory_long_number`,
`memory_too_long`, `memory_empty`, `memory_seen_record`, `memory_others`, `memory_unsure`: "This
can't be remembered" with the reason), 503 `memory_check_unavailable` (nothing saved, the text
stays in the box), 409 `memory_off` (the switch is read again) and `memory_full`, 404 for a
suggestion that expired after 24 hours. Renaming a chat refuses a full Aadhaar number before
sending and explains 422 `title_*`; a changed chat (412) says so; a question in a deleted chat
(404) and ask-again of a replaced answer (409 `message_superseded`, `message_not_revisable`) are
explained and the question stays in the box.

**Keyboard.** Enter sends, Shift+Enter adds a line, Ctrl+Enter always sends; never while an input
method composes (`isComposing`, keyCode 229: Telugu keyboards); on touch-first screens Enter adds
a line. `/` moves to the question box when not typing elsewhere (it only moves focus). Alt+N
starts a new chat (matched by `code`, so it works with the Telugu layout, and never with AltGr).
Chosen to avoid browser and AT shortcuts: Chrome/Edge use Ctrl+Shift+O (bookmarks/favourites) and
NVDA uses Ctrl+Alt+N (start NVDA).

**Scrolling.** The page scrolls (not an inner box). The thread follows new text while the reader
is within 96px of the end; wheel, touch or keys away from the end unpin it (our own scrolling
never does). Each conversation's position is remembered for the page view. Long threads render
the newest 30 turns ("Show earlier messages" adds more) and older turns use `content-visibility:
auto` where supported (progressive enhancement; the cap works everywhere).

**Motion tokens** (`globals.css`): `--motion-enter` 180ms (message, note and chat fade + 6px rise),
`--motion-quick` 120ms (popovers, dialogs, send/stop morph), `--motion-stagger` 50ms (follow-up
chips, example cards), `--ease-out` cubic-bezier(0.2, 0.8, 0.2, 1); the status shimmer is a 2s
gradient on one line of text; the caret blinks at 1s. Only transform and opacity animate (plus
the shimmer's background position). Under `prefers-reduced-motion: reduce` nothing moves: no
entrance, no shimmer (plain `ink-muted` text), no caret blink, streamed text appears at once and
scrolling is instant; a highlighted message keeps a still tint. Forced colours: the shimmer is
plain text and the caret `CanvasText`.

**Accessibility.** One polite status region announces the step names (not counts, not words) and
then "The answer is ready." with the checked answer as plain text once; the log itself is silent.
Everything is reachable by keyboard with the visible focus ring; popovers and menus close with
Escape and return focus. Colours reuse §7 pairs (`ink-muted` on white 7.45:1, `violet-ink` on
`violet-soft` 6.77:1, `primary` on `primary-soft` 5.22:1, `on-action` on `action` 17.36:1).
Telugu: the sans with `:lang(te)` line height; chat titles are the one place the sidebar
truncates (single line, `leading-relaxed` so glyphs are not clipped; full title in `title` and
the accessible name; the responsive check allows exactly this one-line ellipsis). Everything
else wraps: the chat title, source titles and quotes.

**Checks.** `features/ask/ask.test.tsx`, `chat.test.tsx`, `chat-units.test.tsx`,
`history-memory.test.tsx` (vitest); `e2e/ask.spec.ts` (keyboard journey, Stop, search-only, All
chats, memory, phone in Telugu, axe), the new pages in `e2e/responsive.spec.ts` and
`e2e/a11y.spec.ts`. Web-platform features used: `ResizeObserver`, `IntersectionObserver`,
`requestAnimationFrame`, `matchMedia`, `navigator.clipboard`, `background-clip: text`,
`content-visibility` (progressive), regex lookbehind (all Baseline widely available except
`content-visibility`, which has the DOM cap as its fallback).

### 5.4 Languages: English first, Telugu behind one switch (ADR-0036)

The product owner decided on 2026-09-30 to launch in English and hide Telugu everywhere for now.
Telugu is **hidden, not deleted**: catalogs, fields, previews, fonts and their tests stay in the
code, dormant, so it returns by configuration.

**Parent-facing surfaces are the exception (R2, ADR-0044, owner decision D9).** The public parent
pages and verification page (§5.8), WhatsApp templates and printed parent slips and forms follow
the school's `parent_language` (or the guardian's own choice), **not** this switch; they load Noto
Sans Telugu only when their language is Telugu. Everything staff see stays as described below.

**The switch.** `SOS_TELUGU_ENABLED` (default `false`), the same variable as the API's. The web app
reads it **only** in `src/i18n/languages.ts` (`teluguEnabled()`, `enabledLocales()`,
`uiLocale()`), at run time on the server: the proxy, layouts, pages, route handlers and the BFF.
It is never inlined into a browser bundle; one image serves both settings. Client components ask
`useTeluguEnabled()` / `useEnabledLocales()` from `src/i18n/LanguagesProvider.tsx`, which the
locale layout fills; without a provider only English is on. `app/client-boundary.test.ts` fails if
a client module imports the server reader or code reads the variable anywhere else.

**No locale in any URL** (product owner, 2026-09-30: "just remove the prefix system across
the site"). Every page has one address in every language: `/`, `/students`, `/ask/c/<id>`,
`/platform/schools`, `/dev/sign-in`, `/welcome`, `/features`, … next-intl runs with
`localePrefix: "never"` (`src/i18n/routing.ts`); the proxy (`src/proxy.ts`) rewrites each
request internally to `app/[locale]/…` (the folder stays, so layouts and pages keep their
`locale` param and nothing else moved). The language comes from the `NEXT_LOCALE` cookie, then
`Accept-Language`, among the switched-on locales only. There are no `hreflang` alternates by
path (no `Link` header). Old `/en/…` and `/te/…` links answer **308** to the same path without
the prefix (query kept, every security header set, leading slashes collapsed so
`/en//evil.example` stays on this site); while Telugu is on, an old `/te/…` link (and `/en/…`)
first stores its language in the cookie. Links go through `Link`/`useRouter`/`redirect` from
`@/i18n/navigation` or are plain prefix-less paths; never build `` `/${locale}/…` ``. Return
addresses (`next`) are prefix-less paths (`safeNext` drops an old prefix and still refuses
anything that is not a same-origin path). The language switcher (Telugu on only) is a pair of
buttons (`aria-pressed`, each name in its own `lang`) that write the cookie
(`src/i18n/locale-cookie.ts`: `path=/`, one year, `SameSite=Lax`, `Secure` over https) and
reload the same page: a full reload, because `<html lang>` and every cached API answer change
with it.

**With the switch off:**

- **Routing.** Only English is negotiated: `Accept-Language: te` and a stored
  `NEXT_LOCALE=te` cookie are ignored (and left alone for when Telugu returns: the proxy writes
  no language cookie while Telugu is off), an old `/te/…` link lands on the same prefix-less
  English page, and the locale layout 404s a `te` param that slipped past the proxy. The BFF asks
  the API for English only.
- **Messages.** The Telugu catalog is never loaded (`src/i18n/messages.ts`). English strings that
  talk about Telugu ("in English and Telugu", "Write in English, Telugu or a mix") are replaced by
  their English-only wording from `messages/en.telugu-off.json`: same keys, same ICU arguments
  (tested). Keys that name Telugu for a Telugu-only control (`*.te`, `*_te`, `*.telugu`,
  `teField`, `summaryTe`, …) stay in `en.json` and are rendered only while Telugu is on.
- **Screens.** No language switch (school, platform, public pages); no `*_te` input (class names,
  letterhead, notices, platform banners); no language choice where English is the only one
  (school settings, invite and profile language, export file language, document language,
  verified-answer language, owner language when provisioning); no Telugu column, list line,
  summary or preview (notice list and preview, circular summary, finding explanation, UI
  reference). Where the API still requires a Telugu value (`ClassCreate.display_te`,
  `AnnouncementIn.title_te/body_te`), the English text stands in, never asked for; where a stored
  Telugu value exists (letterhead, class name, a person's or a school's language), it is kept
  and sent back unchanged. Data is not UI: a Telugu-script name copied from a register still
  shows as data.
- **Fonts.** No Telugu `@font-face`, preload or font-family stack reaches the browser (§3).

**How to write a screen.** Show a Telugu field, column, option or preview only under
`const telugu = useTeluguEnabled()`; build the form schema for the fields you show (see
`classCreateSchemaFor`, `announcementSchemaFor`, `noticeEnglishSchema`) and fill a hidden
required value through the form's `extra`. Pure helpers that combine languages take
`{ telugu }` and default to English (`noticeText`, `noticeComplete`).

**Strings.** Every UI string exists in `en`. While the switch is off, `te` entries are
optional: keep the existing ones and add a `te` value when you add a key (the parity test in
`i18n/messages.test.ts` still pins en/te keys so the catalog is ready when Telugu returns); if you
add an English string that mentions Telugu, add its English-only wording to
`en.telugu-off.json`.

**Tests.** Default renders are English-only: `renderWithIntl(ui)` renders with Telugu off and
the `en.telugu-off` wording; a Telugu render (`"te"`) or `{ telugu: true }` switches it on
explicitly, and every Telugu test does so. `app/english-only.test.tsx` renders every page under
`app/[locale]` with the switch off and fails on Telugu script, the word "Telugu", `lang`/`hreflang`
te, `/te` links, a link, form or resource whose address names a locale (`/en/…`, `/te/…`),
`*_te` fields or a language switch (`app/no-locale-urls.test.tsx` checks the shells' links and
the switch with Telugu on); each feature's tests pin its forms and
bodies. e2e: the default project runs with the switch off (`e2e/english-only.spec.ts`: the
308 from old `/te` and `/en` URLs, no open redirect, a Telugu browser with a `te` cookie, the font
404, school and platform pages); `chromium-telugu` runs a second server from the same build with
the switch on and re-runs the tests tagged `@telugu`, choosing Telugu the way the switcher does,
through the `NEXT_LOCALE` cookie (`e2e/support/telugu.ts`: `selectLanguage`, `inTelugu`,
`openIn`), plus the switcher, cookie and Accept-Language checks in `e2e/smoke.spec.ts`.

**Bringing Telugu back.** Set `SOS_TELUGU_ENABLED=true` (API and web), review the Telugu catalog,
templates and prompts, run the `@telugu` e2e and the Telugu evals, and record it in a new ADR.

### 5.5 Motion

SchoolOS is a crisp work tool used all day on office PCs: motion is **rare, short and
functional** (feedback, spatial consistency, preventing a jarring change). Rules follow the
design-engineering skills in `.claude/skills/` (Emil Kowalski); this section wins on conflict.

**Tokens** (`globals.css` `:root`, mirrored in `src/lib/motion.ts`; `lib/motion.test.ts` fails
when they drift):

| Token               | Value                             | Use                                             |
| ------------------- | --------------------------------- | ----------------------------------------------- |
| `--ease-out`        | `cubic-bezier(0.23, 1, 0.32, 1)`  | everything that enters or exits                 |
| `--ease-in-out`     | `cubic-bezier(0.77, 0, 0.175, 1)` | movement on screen                              |
| `--ease-drawer`     | `cubic-bezier(0.32, 0.72, 0, 1)`  | the menu drawer                                 |
| `--duration-press`  | 140ms                             | button press (scale 0.97)                       |
| `--duration-quick`  | 150ms                             | popovers, every exit                            |
| `--duration-enter`  | 200ms                             | dialogs, alerts, a changed number, content fade |
| `--duration-drawer` | 260ms                             | drawer slide-in                                 |

Never `ease-in`, `transition: all` or `scale(0)`; UI motion stays under 300ms; exits are faster
than entrances. The Ask chat keeps its own `--motion-*` durations (§5.3) on the shared curve.

**What moves**

| Where                                                | Motion                                                                                                                | Tool                                                    |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `Button`, `IconButton`, top-bar controls             | `.pressable`: scale 0.97 while pressed; colour fades only on fine-pointer hover                                       | CSS                                                     |
| `Dialog`, `ActionDialog`, `FormDialog`, `FlagSwitch` | enter: fade 150ms + scale 0.97→1 200ms, centred, backdrop fades; exit: 150ms fade + scale 0.97, then `close()`        | CSS (`.dialog-motion`) + WAAPI (`lib/dialog-motion.ts`) |
| Menu drawer (below lg)                               | slides in 260ms on the drawer curve, leaves the same way in 150ms; backdrop fades                                     | CSS + WAAPI                                             |
| Notification panel                                   | grows out of the bell (origin top right): fade + scale 0.97, 150ms, `inert` while it fades out; Escape closes at once | Motion (`m`, `AnimatePresence`)                         |
| `Alert live`                                         | rises 4px while fading in, 200ms                                                                                      | CSS (`.alert-in`)                                       |
| `KpiCard` / `StatCard` value                         | a change from one real value to another rises in (`TickValue`); first value and "—"→value do not                      | CSS (`.value-tick`)                                     |
| `DataTable`, home work cards                         | content that replaces a skeleton fades in (`LoadFade`); cached/server data does not                                   | CSS (`.content-in`)                                     |
| Skeleton                                             | shimmer (flat under reduced motion)                                                                                   | CSS                                                     |
| Command palette                                      | fade + scale 0.98→1, 150ms; closes at once                                                                            | CSS (`dialog.palette[open]`)                            |
| `Menu`, `Popover` panels                             | fade + scale 0.97 from the trigger corner, 120ms                                                                      | CSS (`.menu-in`), only after a user action              |
| Bulk bar, toasts                                     | rise 8px + fade, 150ms                                                                                                | CSS (`.bar-in`)                                         |
| `Drawer` (end side sheet)                            | slides in from the end, 260ms drawer curve; leaves with the modal fade                                                | CSS + WAAPI                                             |

**Deliberately not animated** (the skills' frequency and keyboard gates): sidebar navigation,
active item and compact switch; tabs and segmented controls (arrow keys move the selection,
so a sliding indicator would animate keyboard input); `<details>` (often opened from the
keyboard; no Baseline height animation); table rows, sorting and keyboard movement in grids;
page and route loads; the Escape close of the notification panel; density, column, sort and
paging changes. **Toasts (v1.0, owner decision 2026-10-18):** a CSS-only `ToastProvider`
(classes only, no injected `<style>`, so the nonce CSP holds) for results of background work;
form results and errors stay inline (`Alert live`, `ErrorSummary`). Sonner stays rejected (it
injects a `<style>` element at run time).

**Reduced motion.** Every rule sits in `@media (prefers-reduced-motion: no-preference)`; the
reduce query still cuts any leftover transition to 0.01ms. `closeDialog()` closes at once.
`MotionProvider` sets `reducedMotion="user"`: transforms jump, opacity may still fade.

**Motion library and CSP (SEC-010).** `MotionProvider` (`components/shell/MotionProvider.tsx`)
is mounted once by `AppShell`: `LazyMotion features={loadMotionFeatures} strict` (the
`domAnimation` features load as their own chunk after hydration, `lib/motion-features.ts`, off
the critical path of every page; only the light `m.*` components; `motion.*` throws) and
`MotionConfig reducedMotion="user"` with the 200ms ease-out default. The CSP has no `'unsafe-inline'` for styles, and a Motion element writes its
`initial` values into a `style` **attribute** when it is server-rendered, which the browser
would block. The pattern: use `m.*` only for UI that exists after a user action (a panel that
opens, an item that appears), never in the server HTML; everything visible on first paint
animates through CSS classes or the Web Animations API, which set styles through the CSSOM
(allowed). Content is never hidden waiting for JavaScript. A panel that fades out stays in the
DOM for its exit: wrap it in `Presence` (`lib/presence.tsx`) and render it `inert` while it
leaves, so it takes no focus and no click meant for the page underneath.
`MotionProvider.test.tsx` renders the console to HTML and fails on any `style=` attribute.

**Checks.** `lib/motion.test.ts` (tokens, gating, no `ease-in`/`transition: all`/`scale(0)`),
`lib/dialog-motion.test.tsx`, `components/ui/motion-states.test.tsx`,
`components/shell/MotionProvider.test.tsx`, the bell tests in
`features/notifications/notifications.test.tsx` (including `inert` during the exit); the
CSP-violation checks of the e2e specs.

**Component states** (polished in this pass): `Button` `loading` (spinner + `aria-busy`, pair it
with `disabled` and a "Working…" label), press feedback, `aria-busy` cursor; dialogs share
`DialogCloseButton`, a muted footer and a semibold title; `Table` uses tabular figures;
`Alert` wraps long text (`min-w-0`).

### 5.6 Public marketing pages

The public site for prospects (FR-IAM-001 public entry): `/welcome` (home; signed-out visitors to
the bare school home land here), `/features`, `/security`, `/pricing` and `/about`. Code in
`features/marketing/`, routes in `app/[locale]/{welcome,features,security,pricing,about}` outside
the `(school)` group; no session needed. Links are locale-free paths through `Link` from
`@/i18n/navigation`. Strings are in the `marketing` namespace of `messages/en.json`.

**Layout.** `MarketingShell` (Server Component): skip link, `SiteHeader`, `<main id="main">`,
footer with every page link. `SiteHeader` (client) is sticky and transparent over the hero; once
the page scrolls it becomes a white bar with a hairline (`data-scrolled`, colour only). From lg
it shows the wordmark, the four pages (`aria-current="page"` plus a dot on the current one),
"Sign in" and "Talk to us"; below lg a 44px "Menu" button opens the same links as a disclosure
panel: focus moves to the first link, Escape or the button closes it and focus returns to the
button; following a link, widening to lg, a click outside the header or Tab moving focus out of
it closes it (the open panel covers the top of the page and must never hide the focused
control, WCAG 2.4.11); while it fades out it is `inert`. The root's scroll padding clears the
4rem sticky header, so anchors and keyboard focus (also Shift+Tab back up the page) never land
under it. Content column `max-w-7xl` inside
`px-page`; sections `py-20 md:py-28`; section intros `mb-12 md:mb-16`; grids `gap-4 md:gap-5`.
Every grid and flex child in `.mk` may shrink (`min-width: 0`), so a wide illustration never
pushes the page past a 360px screen.

**Direction B, "Register paper" (v1.1; report §6.2, decision §12.1).** `.mk` redefines the
semantic colour tokens for the marketing scope, so every utility draws the paper palette: canvas
paper `oklch(0.975 0.008 95)` #f8f7f1, sheet surface `oklch(0.995 0.004 95)`, ruled hairlines
`oklch(0.89 0.012 95)`, warm ink `oklch(0.24 0.01 95)` (15.3:1), ink-muted 6.9:1, ink-subtle
5.1:1, and ONE accent, ledger green `oklch(0.45 0.09 165)` #0e6549 (6.6:1; `primary`, `accent`,
`brand`, `focus`, `info` all point at it). The primary button stays near-black (warm ink).
Product mockups add `.mk-app`, which restores the app's Quiet ledger tokens: they show the real
UI. The hero has no glows or grid: `.mk-grid` draws ruled register lines with a faint
ledger-green margin line that fade out downwards; "How it works" is a ruled register of the three
steps (`.mk-ruled`) beside the office illustration; the dark band (`.mk-night`) is flat ledger
ink `oklch(0.27 0.035 165)` (white 14.9:1, `--mk-night-muted` 9.8:1) with a white focus ring.
`features/marketing/register-paper.test.ts` recomputes these pairs.

**Type.** Headlines are Instrument Serif 400 (`.mk-display` h1, `.mk-title` h2; slight optical
tracking, balanced wrapping); the home headline's accent phrase is the serif italic in ledger
green (latin italic file loaded by `MarketingShell` only). Under `:lang(te)` headings switch to
the sans, semibold, normal spacing and line height 1.5, and nothing is slanted. Body is PP Mori;
eyebrows are the sentence-case `.eyebrow` over a hairline.

**Calls to action (owner decisions).** "Talk to us" is a plain `mailto:` link, shown only when
`SOS_PUBLIC_CONTACT_EMAIL` is set; there is no form and no lead data. "Sign in" goes to
`/bff/auth/login`. Without an address "Sign in" becomes the primary button and the home hero
offers "See how it works". **"Ask on WhatsApp"** (`WhatsAppLink`) is a secondary button beside
"Talk to us" (after "Sign in" when there is no address), shown only when
`SOS_PUBLIC_WHATSAPP_NUMBER` is set: in the header bar from lg (labelled from xl, 1280px;
between 1024 and 1279px a 40px square icon button, `iconOnly`, whose accessible name is still
"Ask on WhatsApp (opens WhatsApp)" and whose tooltip is the label, so the bar never wraps or
crowds the page links), in the phone menu below lg (1024px), the hero, both plan cards on
/pricing and the closing bands. `welcome.spec.ts` (@contact) checks the header stays one 64px
row with every control inside the screen and no sideways scroll from 320 to 1920px. It is a plain link
to `https://wa.me/<digits>?text=<encoded>` with the fixed greeting "Hello, I'd like to see
SchoolOS for our school." (`marketing.cta.whatsappMessage`; never student or other personal
data), `rel="noopener noreferrer"`, same tab, and an accessible name that starts with the
visible label and adds "(opens WhatsApp)" (WCAG 2.5.3). Unset or invalid: nothing is shown, no
placeholder number. wa.me is the only address outside the site a public page may link to.

**Settings** (read on the server at request time by `features/marketing/settings.ts`, never in a
browser bundle; unset or invalid means that part is not shown, no placeholder anywhere):

| Variable                     | Default | Use                                                                                                                                                                                           |
| ---------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SOS_PUBLIC_CONTACT_EMAIL`   | unset   | "Talk to us" mailto in the header, hero, plans, closing band, footer and About. Must be a plain address (no `?`, `,` or spaces), else ignored                                                 |
| `SOS_PUBLIC_COMPANY_NAME`    | unset   | Footer "© {company}" line and the About contact block (≤ 200 characters)                                                                                                                      |
| `SOS_PUBLIC_COMPANY_ADDRESS` | unset   | About contact block; lines split on `\|` or `\n`                                                                                                                                              |
| `SOS_PUBLIC_WHATSAPP_NUMBER` | unset   | "Ask on WhatsApp" (wa.me link). An international number of 8-15 ASCII digits with an optional leading `+`, first digit 1-9 (e.g. `+91XXXXXXXXXX`); spaces, dashes or anything else mean unset |

**Dedicated hosts.** With `SOS_DEPLOYMENT_MODE=dedicated` (`platformEnabled()`) `/features`,
`/security`, `/pricing` and `/about` answer 404 and `/welcome` is a branded sign-in card only: a
dedicated host is one school's own address, not a sales site.

**Line illustrations (v1.1).** Five drawings in the line-interior style (skills-review.md §5),
drawn for SchoolOS: `office-register` (a clerk in a saree writes in the admission register),
`fee-counter` (an accountant hands a receipt to a parent and her daughter), `certificate-check`
(a principal holds a certificate; a clerk checks its code on her phone), `timetable-board` (a
teacher at the class timetable with two students), `records-almirah` (a clerk files a folder in
a locked steel almirah). Indian clothing, mixed genders and ages, four skin tones; no
religious, festival, flag or caste-coded imagery. One 1.6px warm-ink line, paper, wood and steel
fills, the ledger green and amber as small accents only; no gradients, filters, opacity or text.
Files are static SVG in `apps/web/public/illustrations/` (480×360 viewBox, a `<title>`,
presentation attributes only, 7-12 KB each), served as `<img>` from `'self'` by
`components/ui/Illustration` (decorative `alt=""` by default; `alt` only when the picture adds
meaning), lazy unless `priority`. Decorative art is hidden under `forced-colors: active`
(`.sos-illustration[alt=""]`, globals.css) and always prints. `EmptyState` takes
`illustration` for first-use and session pages (keep the icon for no-results and in-table
states). Used on the home page ("How it works"), the hero of /features, /pricing, /about and
/security (from lg), the not-found and no-access pages and `/dev/ui`. The Docker image copies
`public/` into the standalone output. `components/ui/illustrations.test.ts` lints every file
(registered in `ILLUSTRATIONS`, at most 30 KB, no script, style, class, handlers, `href`,
`url()`, `<image>`/`<use>`/`<foreignObject>`, gradients or opacity; colours only from the
illustration palette). The MengTo kit scripts were not run; their lint rules were ported.

**Product mockups.** HTML, CSS and inline SVG only (`mockups.tsx`): crisp at any DPI, no image
requests, no style attributes. Each carries a "Sample data" tag, uses made-up values ("Sample
student A"), sits in a `<figure>` whose picture is `aria-hidden` and whose `figcaption` (sr-only)
describes it, and keeps AA contrast like real UI. Mockups and the home tile vignettes are
cropped on purpose (truncated lines, a fixed-height tile window): the responsive e2e skips
its clipped-text check inside `aria-hidden` content only; page overflow and screen edges are
still checked.

**Lenis smooth scrolling (home page only, owner request 2026-10-18).** `SmoothScroll`
(`features/marketing/SmoothScroll.tsx`, client) is mounted by `HomeView` only: never in the staff
app, the operator console or the parent and verification pages. It imports `lenis` after
hydration (its own chunk), smooths the wheel only (`smoothWheel`, touch native, `anchors` off),
is never created under `prefers-reduced-motion: reduce` (and destroyed if that is switched on,
and on unmount). Keyboard and focus scrolling stay native. A pointer click on an in-page link
glides there with `scrollTo` (honouring `scroll-margin-top` and the root's `scroll-padding-top`),
then moves focus to the target and updates the address bar; keyboard activation and the skip
link keep the browser's instant jump. Lenis writes styles only through the CSSOM
(`element.style.setProperty`) and classes on `<html>`; its dist has no `<style>` injection, so
`style-src 'self'` holds; the rules we need from `lenis/dist/lenis.css` live in
`marketing.css`. Dependency check (CLAUDE.md §11, 2026-10-18): **lenis 1.3.26**, MIT
(darkroomengineering/lenis, actively released: 1.3.27 on 2026-10-15, 2.0 in pre-release), pinned
exactly; 1.3.26 was published 2026-08-05 (1.3.27 is younger than two weeks, so not taken); no
runtime dependencies, React/Vue/Nuxt peers optional; `npm audit`: no advisory for lenis (the
five existing high findings are the dev-only eslint-config-next chain). Test:
`features/marketing/smooth-scroll.test.tsx`.

**Motion** (design-engineering skills in `.claude/skills/`; purpose first, transform and opacity
only, strong ease-out `cubic-bezier(0.23, 1, 0.32, 1)`):

| Where                   | What                                                                                                                            | Why                        | How                                                                                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Hero illustration       | Three layers settle in once (700ms, 120ms apart); the mismatch row glows once                                                   | Explanation, first visit   | CSS keyframes (`.mk-settle`, `.mk-flag`); runs without JS and always ends visible; the headline and CTAs never animate |
| Sections below the fold | Fade + 16px rise once when their top passes 85% of the viewport (600ms, 60ms stagger); keyboard focus inside shows them at once | Orientation on a long page | `Reveal` (client): Web Animations API + IntersectionObserver with a root margin (`0px 0px -15% 0px`), no Motion import |
| Phone menu              | Scales in from the button's corner (200ms), out in 150ms                                                                        | Spatial consistency        | Motion `LazyMotion` + `domAnimation` + `m` + `AnimatePresence`, `MotionConfig reducedMotion="user"`                    |
| Buttons, tiles          | Press `scale(0.97)` (140ms); tiles lift 2px and arrows nudge on hover                                                           | Feedback                   | CSS; hover only under `(hover: hover) and (pointer: fine)`; nothing under reduced motion                               |
| Header                  | White bar after scrolling (200ms colour)                                                                                        | State                      | CSS transition on `data-scrolled`                                                                                      |

**CSP-safe animation pattern (SEC-010).** The nonce CSP has no `'unsafe-inline'` for styles, and
Motion's `initial`/`animate` props server-render a `style` attribute (a violation, and invisible
content if the style never lands). So: never give a server-rendered Motion component
`initial`/`animate`; render content **visible** on the server; after hydration hold start states
with a Web Animations API animation (no style attribute is written; cancelling it leaves the
element visible), and only for content that is still below the fold, so nothing on screen
blinks. Reveal on a root margin, never on a share of the block's own area (`amount`): a block
taller than the viewport at 400% zoom would never reach it and stay hidden (WCAG 1.4.10). Components that exist only after a user action
(the phone menu) may use `m` with `initial`/`exit`, because they are never server-rendered. Do
not use `whileHover`/`whileTap` on non-interactive elements (Motion adds `tabindex="0"`). Under
reduced motion (also switched on while the page is open), without JavaScript, the Web Animations
API or IntersectionObserver nothing is hidden; print resets revealed content (`marketing.css`). `app/marketing.test.tsx` renders every page to a
string and fails on any `style=` attribute.

**Pricing page: a packaging explainer, never a price.** /pricing says, in order: the two
plans; what the fee is based on (school size, campuses, Shared or Dedicated, the AI question
bundle); what every school gets; the comparison table; the one-time **implementation and data
verification** fee and what it covers (data audit and cleaning, historical import, board and
UDISE+ mapping, certificate templates, admin training, go-live reconciliation, custom domain
for Dedicated); AI as monthly **question bundles** (Lite, Standard, High, plus extra answers)
described in words, never "unlimited" and never tokens; billing monthly or annually in advance
with GST shown separately on every tax invoice (docs/16 §5.8); and the four steps after a
school gets in touch (reply, demo on sample data, written quote, set-up and verification).
Dedicated is always "a managed, isolated SchoolOS environment with your own domain, a
dedicated database and a documented data export"; never "your own server", and nothing
implies the school owns the code.

**Honest claims.** Only what docs/01, 07, 08, 14 and 16 say: no prices, logos, testimonials,
ratings, customer counts or certifications, no "unlimited", no tokens, no "own server" (tests
scan the catalog); certificates and registers show "Planned" until the owner confirms them for
schools.

**Checks.** `app/marketing.test.tsx` (vitest: headings, landmarks, CTA hrefs, contact shown only
when set, WhatsApp link hidden when unset or invalid and correctly encoded when set, the
pricing explainer's sections, dedicated 404s and sign-in page, no style attributes in DOM or server HTML, no external
resources, honest-claims scan, invalid settings), `features/marketing/marketing-motion.test.tsx`
(reveal: below the fold only, stagger, root margin, reduced motion, focus, cleanup; phone menu:
focus, Escape, outside click, Tab out, `inert` exit), the page scan in
`app/english-only.test.tsx`; e2e `e2e/welcome.spec.ts` (home) and `e2e/marketing.spec.ts` (every
page at 1366×768 and 375 px, with and without reduced motion: CSP and console errors, axe, no
sideways scroll, nothing left hidden also at 320 × 256 px, focus not under the sticky header,
links, the phone menu by keyboard, print). The dedicated-host variant is checked in vitest only
(the e2e servers run the shared mode).

### 5.7 Mobile and touch (owner request 2026-10-06)

Every screen works from 360px phones to 1920px screens; 1366×768 stays the design baseline.

**Breakpoints.** `sm` 640px (tables stack, dialogs become sheets), `md` 768px (gutters, sticky
first column), `lg` 1024px (sidebar beside the page; below it the menu drawer, §5.2). Touch is
a media feature, not a width: `pointer-coarse:` (Tailwind) / `@media (pointer: coarse)`, so an
office PC with a mouse keeps its density at any width.

**Touch targets.** Under `(pointer: coarse)` every primary control is at least 44×44px:
`Button` (all sizes), `IconButton`, `DialogCloseButton`, inputs, selects, segmented options,
tabs, sidebar items, breadcrumb links, the bell, the Ask controls and any label that wraps a
checkbox or radio (globals.css). Fine pointers keep 40px controls (WCAG 2.5.8 needs 24px).
Inline links in a sentence are the exception. Citation chips get a larger invisible hit area.

**Tables: one rule per kind.**

| Kind                                                          | Below 640px                                                                           | Use                                                                                                           |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Record lists people scan and open (one row = one named thing) | Cards: `DataTable stacked` (or `StackedRows` + `NarrowSwitch` for a hand-built table) | Students, findings, documents, imports, users, certificates, tasks, platform schools, subscriptions, invoices |
| A record's fields (label, value, notes)                       | `Table reflow`: each row a block                                                      | The student's details and their sources                                                                       |
| Grids, ledgers, comparisons, registers, the sheet grid        | `TableScroll` (focusable named region, sticky first column)                           | Everything else                                                                                               |

`StackedRows`: one framed card, a list named by the caption; the title column (the first by
default, `stack: "title"`) is the first line, other columns are `<dl>` label/value pairs,
columns with an empty header (or `stack: "actions"`) are a row of actions (44px links on
touch), `stack: "wide"` puts a long value (a reason, a note) under its label across the whole
card instead of a narrow value column (invoice payments: a reversal's reason),
`stack: "hidden"` drops a repeat. A stretched row link still covers the card. Only one
layout is in the DOM (`NarrowSwitch`, `useMediaQuery`); the server and hydration render the
table, phones switch in the first browser render (before the rows usually load).

**Dialogs are bottom sheets below 640px** (every modal `<dialog>` except the drawer;
globals.css): full width, bottom edge, at most the screen minus 2.5rem (`dvh` after a `vh`
fallback), top corners rounded, one scrolling box with the footer stuck to its bottom
(`.dialog-footer`, also on desktop) clear of the home indicator. They rise on the drawer
curve (260ms) and leave downwards (150ms, `lib/dialog-motion.ts`); instant under reduced
motion. No swipe to dismiss: Escape, the close button and Cancel close them.

**Forms.** One column below `sm`, labels above fields, errors under the field (`Field`).
Every text input, select and textarea is 16px (`text-base`, no iOS zoom on focus); type and
`inputMode`/`autoComplete` match the value (`email`, `tel`, `numeric` for IDs and counts,
`decimal` for amounts; never `type="number"` for IDs).

**Safe areas and height.** `viewport-fit=cover`; the gutters, the body, the drawer, sheet
footers and the Ask composer add `env(safe-area-inset-*)`. Full-height layouts use
`min-h-viewport` / `h-viewport` (100vh, then 100dvh). The viewport asks for
`interactive-widget=resizes-content`, so docked controls (the Ask composer) stay above the
on-screen keyboard on Android; Safari scrolls the focused field into view itself.

**Ask on phones.** The conversation list lives in the menu drawer (the Ask sub-list, §5.3)
and on All chats; the composer is docked above the keyboard; the first tap on a citation
opens its preview, the second follows it, a tap outside closes it.

**Sheet grid on touch.** One tap (never a scroll) opens the cell editor; on touch screens the
editor is 16px and 44px high with Save and Cancel buttons (phones have no Escape key) and the
help line says so. Keyboard behaviour is unchanged (§4.1).

**Polish rules (all widths).** One primary button per view (near-black; `secondary` for the
rest); numbers in tables are `numeric` (tabular figures, right-aligned); long names wrap
(`break-anywhere`), and a one-line truncation always carries the full text in `title`; every
list has loading (skeleton), empty (what to do next) and error states; hover only on fine
pointers, focus ring always; motion per §5.5.

**Mobile pass as built (v1.2, 2026-10-18).**

- **On-screen keyboard.** `useOnScreenKeyboard()` (`components/shell/keyboard.ts`) says the
  keyboard is up when the visual viewport loses more than a quarter of the tallest height seen
  at that width, or when a text field has focus on a touch screen. The phone bottom bar is
  `[hidden]` meanwhile, so it never covers the field, a SaveBar or the Ask composer.
- **Docked bars.** Below md, while the bottom bar shows, `--dock-offset` is its height (56px +
  1px + the home-indicator inset) and `--dock-safe` is 0; otherwise 0 and the inset. `SaveBar`,
  the `DataTableV2` bulk bar and the Ask composer use `bottom: var(--dock-offset)` and
  `padding-bottom: max(…, var(--dock-safe))`, and carry `data-dock`.
- **Focus not obscured (WCAG 2.4.11).** In the consoles `html` keeps `scroll-padding-top` of
  the top bar plus 1rem and `scroll-padding-bottom` of the bottom bar plus 1rem (5rem more
  while a docked bar is on the page), so Tab and anchor jumps never land under a bar.
- **Top bar.** Phones: Menu, the mark, then icon buttons (search, bell, preferences), each at
  least 44px on touch; the page's own heading says where you are. Tablets (md to lg): the
  current page name on one line (truncated, full text in `title`); lg and up: section › page.
- **Overlays.** Menus and popovers measure themselves when they open and move sideways back
  inside the screen (8px margin; CSSOM `translate`, no style attribute). The command palette
  stays at the top on phones (full width less 8px), never a bottom sheet, so the keyboard
  cannot cover its results.
- **Forms.** On touch screens any text field, select or textarea that a screen set to
  `text-sm`/`text-xs` is 16px (globals.css), so iOS never zooms on focus. `SearchInput` fields
  keep `type="search"`; the marks stepper uses `enterKeyHint` next/done.
- **Small wins.** `SegmentedControl` fills the width below 640px; `Pagination` puts Previous and
  Next side by side across the width; more record lists stack (fee receipts, register-photo
  pages, import rows, operator tickets, operators, break-glass grants, fleet deployments).
- **Lenis** (§5.6) is not created on a touch screen (`pointer: coarse`) or under reduced
  motion, and a scroll key pressed during a wheel glide ends the glide first, so the key's
  native scroll is never overwritten.
- **Never**: a zoom lock (`maximum-scale`, `user-scalable=no`), hover-only controls, a
  server-rendered style attribute.

**Checks.** `components/ui/responsive.test.tsx` (stacked lists, `NarrowSwitch`, reflow, sheet
exit), `features/sheets/sheets.test.tsx` (tap to edit), `features/ask/citations-touch.test.tsx`;
e2e `responsive.spec.ts` "key screens" at 360, 390, 768 (touch) and 1366: no sideways scroll,
stacked lists below 640px only, 44px touch targets, dialogs that fit (sheets on phones), axe.

### 5.8 R2 screens (planned): fees, messaging, parent pages, verification, Ask usage

**Status: planned (R2; docs/02 C20-C25).** Same shell, tokens, components and rules as the rest of the app; everything below must work at 1366×768 and keyboard-only (WCAG 2.2 AA), print on A4 where printed, and keep personal data out of URLs (searches are `POST`).

**Sidebar.** A "Fees" item (visible with `fee.collect`, `finance.read` or `fee.manage`; hidden when fees are off or the school runs alongside its ERP) and a "Messages" item (`message.read`); WhatsApp and payments settings live under Settings.

#### Fees screens

| Route | Screen | Key interaction notes |
|---|---|---|
| `/fees/collect` | **Counter** (the most used R2 screen) | One column, no scrolling at 1366×768 for a typical student. Focus starts in the search box (`/` or `F2` focuses it from anywhere in Fees). Typing a name or admission number shows up to 8 matches as a listbox (arrow keys, Enter); the chosen student's dues table appears below (instalment, head, due date, due, late fee, paid) with the total; focus moves to **Amount**, pre-filled with the total due and selected so typing replaces it. **Mode** is a radio group with mnemonic keys (C cash, Q cheque, B bank transfer, U UPI QR); choosing cheque, bank or UPI reveals only its fields (cheque number/bank/date; reference; UTR, 12 digits, `inputmode="numeric"`). Allocation shows below as read-only lines (oldest first) with "Change allocation" (opens editable amounts; totals must match before saving). **Save receipt** = `Ctrl+Enter`; the button shows the number it will not know until saved ("Save and print"). After saving: a success panel with the receipt number in large type, "Print" (focused; `P`), "Send on WhatsApp" (if a guardian is opted in; `W`), "Next student" (`N`, returns focus to search). Errors say what to fix next to the field ("This UTR is already on receipt RCT/2026-27/000118 — open it"). No modal dialogs in the happy path. Amounts in Indian grouping (₹1,25,000.00) |
| `/fees/receipts` | Receipts list | Filters (date, mode, collector, status) as query params of ids/codes only; cancelled receipts struck through with "Cancelled on …"; row actions: print, download, request cancellation, refund |
| `/fees/receipts/{id}` | Receipt detail | Frozen content, status, cancellation and refunds history, messages sent, parent link status |
| `/fees/accounts/{id}` | Student fee account | Statement with running balance; tabs: dues, statement, concessions, payment links, messages; actions: collect, send payment link, request concession |
| `/fees/approvals` | Approvals inbox | Concessions, adjustments, cancellations, refunds waiting for me (never my own requests); approve requires step-up (the usual `428` flow), reject requires a reason |
| `/fees/daily` | Daily collection report | Date picker (default today), totals by mode and collector, cash and cheque to hand over; Print (A4) and CSV (step-up); "Close day" with a confirmation showing the totals |
| `/fees/dues` | Dues list | Class/section filters; sort by oldest due; bulk "Send reminder" and "Send payment link" with a preview of recipients and skips; CSV (step-up) |
| `/fees/reconcile` | Bank and online reconciliation | Tabs: Bank statement (upload, proposed matches with checkboxes, "Confirm N matches"), UPI exceptions, Online (yesterday's report; "Mark explained" with a note) |
| `/fees/setup` | Heads, plans, late-fee rules | Plan editor as a sheet grid (§4.1) of heads × instalments with live totals; "Apply to class" shows a dry-run summary before the job; an active plan in use is read-only with "New version" |
| `/settings/payments` | Razorpay connection | Owner only; plain statement "Money goes to the school's Razorpay account and bank. SchoolOS never holds it. Razorpay's fees apply."; Connect (redirect), status, token expiry, rotate webhook secret, disconnect (confirm) |
| `/settings/upi` | UPI QR | Upload the bank's QR image, payee name, UPI ID; preview as parents will see it |

**Receipt print.** A5 portrait (default) or A4 half-page with a counterfoil line; school letterhead, receipt number and date prominent, student name, class, admission number, table of heads and instalments, amount in figures and words, mode details (UTR last 4 shown in full on the receipt only, since the parent needs it), "Education services: exempt from GST", collector's name, "Computer-generated receipt; verify at <public link>" only when the parent link exists. A cancelled receipt reprints with a diagonal "CANCELLED" mark.

**Fees as built (R2-1B, 2026-10-18; `apps/web/src/features/fees/`).** The fee screens above are built except `/fees/reconcile` and `/settings/payments` (R2-2E, R2-2D) and the WhatsApp actions (R2-3G). The sidebar has a "Fees" group (Collect fees, Receipts, Fee approvals, Daily collection, Fee dues, Fee setup) shown while `GET /fees/status` answers 200 for someone holding a fee permission; the Tally dues page stays at `/fees`. Counter: the search is a WAI-ARIA combobox (POST body only), `/` or `F2` returns to it, the mode radios take C/Q/B/U while one has focus, `Ctrl+Enter` saves with one `Idempotency-Key` per student, the success panel focuses Print (`P`) and offers Next student (`N`); amounts are added as whole paise in the browser (never floating-point rupees). The plan editor is a heads × instalments grid with live totals whose annual amount is the row total (so instalments always add up). CSV downloads go through the API client so the step-up prompt can run first, then save a file; nothing is kept in browser storage.

**UPI as built (R2-2E, 2026-10-18; `features/fees/UpiSettingsScreen.tsx`, `ReconcileScreen.tsx`, `UpiCounter.tsx`, `upi.ts`).** `/settings/upi` ("UPI QR" in the admin group, for any fee reader; editing needs `fee.manage`): the plain statement "SchoolOS never holds money", payee name, UPI ID and confirmation window, and a preview of the generated static QR with the payee name and UPI ID under it (an `<img>` of `/bff/api/v1/fees/upi-settings/qr.svg` with an alt text naming the payee); no image is uploaded. `/fees/reconcile` ("Reconcile UPI" in the Fees group, `fee.reconcile` only) has two tabs: **Bank statement** (bank format, "Find automatically" by default, file input; the note that the file is deleted once read and account numbers are never kept; the imported statements; "Review" opens the credits table with a checkbox for each proposal, exact UTR matches ticked and amount-and-date matches left for the person, the reason a credit needs a person, the receipt next to it (a conflicting receipt is marked "never corrected"), "Match receipt" (issued UPI receipts of the same amount) and "Not a fee" (reason code) per credit, and "Confirm N matches" with the consequence in the dialog) and **UPI exceptions** (confirmed vs not-yet-seen counts for 30 days, UPI receipts not seen in the bank after the window with days waiting, credits needing a person, "Find matches again"). The Online tab is R2-2D's. At the counter, choosing UPI shows "Show the school's UPI QR" (folded, so the counter still fits 1366×768) and checks a 12-digit UTR as it is typed (POST body), warning with a link when another receipt holds it and noting when the bank already reported it. The receipt page shows a "Seen in the bank statement on …" or "Not yet seen in a bank statement" pill for UPI receipts.

**Online payments as built (R2-2D, 2026-10-18; `apps/web/src/features/payments/`).** `/settings/payments` (sidebar "Online payments" under the school's admin items, `payments.configure` or `finance.read`, shown while fees are on): before connecting, the merchant statement as a four-point notice (own account, never held by SchoolOS, Razorpay's fees, cash and UPI QR unchanged); "Connect with Razorpay sign-in" (only when the partner application is configured) and "Connect with API keys" (pilot switch) in a step-up dialog whose result panel shows the webhook URL, the events to tick and the secret through `SecretOnce` (copy, "I have added it"); once connected, status, mode, environment, account, open links, waiting messages, webhook URL, "Test connection", "New webhook secret" (shown once, old one valid 7 days) and "Disconnect" (danger, step-up). `/settings/payments/callback` finishes Razorpay sign-in (the code and state leave the address before the request). The student's fee account has a fifth tab "Payment links": instalment checkboxes with their collectable due, the link in a read-only field with Copy, a table of links (code, instalments, amount, status pill, works until, receipt link, Cancel for open ones). `/fees/reconcile/online` (sidebar "Online reconciliation", `finance.read` or `fee.manage`): today's counts and last daily check, status and kind filters, a table of items with kind, plain reason, Razorpay payment id, receipt or account link, amounts (expected when it differs) and "Explain" (note 10-500 characters) for `fee.manage` holders; R2-2E adds the bank tab at `/fees/reconcile`. A receipt paid online offers "Request refund" back to the payer only. Nothing is kept in browser storage; no personal data in URLs.

#### Messages screens

| Route | Screen | Key interaction notes |
|---|---|---|
| `/settings/whatsapp` | Connect WhatsApp | Embedded Signup button (Meta's popup; the only page that loads Meta's SDK), or the manual token form for pilots until SchoolOS is a Meta Tech Provider (D13); checklist after connecting: business verification, INR billing before 31 Dec 2026, template approvals (status chips), quality rating and limit; switches per purpose, absence alert time, quiet hours, office phone for auto-replies; Resume button when paused, with the reason |
| `/messages` | Broadcasts and history | Recent broadcasts with live delivery bars (sent, delivered, read, failed); search history by student/guardian (POST); failure reasons in plain words ("Not on WhatsApp", "Number blocked the school", "Meta limit reached, will retry") |
| `/notices/{id}` (existing) | "Send on WhatsApp" panel | Section picker → preview card: recipients, skipped by reason (each a link to the list), languages, estimated Meta cost incl. GST ("paid by the school to Meta"); Confirm sends; the panel then shows live progress |
| `/students/{id}` → Guardians (existing) | Opt-in per guardian | Three category toggles show the current state and method; changing opens a small form (method, date, evidence upload for a signed form); "Opted out by parent (STOP) on …" is shown read-only with "Record a new signed opt-in"; language: English / Telugu / school default |
| `/messages/slips` | Join slips | Pick a section → print A4 slips (two per page) in the parent language with the QR and the text "Send JOIN to … to get school messages on WhatsApp. Send STOP any time to stop." |

**As built (R2-1C, 2026-10-17; `apps/web/src/features/messaging/`).** `/settings/whatsapp` (owner and principal change; `message.read` holders see the state): the checklist before connecting (Meta paid by the school, INR before 31 Dec 2026, 250 a day until verification, opt-in only), "Connect WhatsApp" (Embedded Signup, shown when `SOS_META_APP_ID` and the configuration id are set) and "Connect with a token" (pilots), then the connected number with status pill, quality, limit (with SchoolOS's cap), billing currency, Resume (when paused, with the reason in words) and Disconnect (both step-up dialogs), the template table (status pill, "Paused by SchoolOS", Telugu review state) and the settings form (four switches as `Toggle`s, absence time, quiet hours, cap, office phone). The guardian opt-in panel is a card under the guardians table of `/students/{id}` (Guardians tab): per guardian the three categories as pills with method and date, "Stopped by the parent", "The number changed after this", "Record a decision" (category, decision, date, signed-form document id) and "Message language" (school's choice / English / Telugu; the existing guardian PATCH). `/messages` is the delivery log (POST search; purpose and status filters; last four digits only; failure and skip reasons in words); `/messages/slips` picks a section and categories and opens the A4 slips in a new tab. Sidebar items "Messages" and "WhatsApp" appear only where messaging is offered (`GET /messaging/settings` 200). Broadcast panels and live delivery bars are wave R2-3G.

**As built (R2-3G, 2026-10-18; `features/messaging/SendNoticePanel.tsx`, `BroadcastScreens.tsx`).** Approved notices get a "Send on WhatsApp" card (only for `message.send` holders where WhatsApp is offered): section checkboxes ("Choose all sections"), "Check who will get it" (secondary), then a preview region with messages to send, guardians and the estimated Meta cost as figures, the GST note, languages and fallbacks, "Not sent to" with plain reasons and counts, a warning with a settings link when nothing can be sent, "Send again" with a required reason, and the one primary button "Send to N parents" described by its consequence line; afterwards a live `<progress>` ("290 of 412 delivered") asked again every 5 s until done, with earlier sends listed. `/messages` shows "Notice broadcasts" (DataTableV2, link per notice to its report, live delivery) and "Automatic messages" (switch state and time per kind, messages waiting, recent runs) above the message history; both hide for a scoped reader. `/messages/broadcasts/{id}` is the delivery report (progress, by class and section, reasons in words). WhatsApp settings add "Days without automatic messages" (DD/MM/YYYY list). `useApiQuery` takes `refetchInterval` (number or a function of the last answer) for live counts; no inline styles (native `<progress>`).

#### Parent pages (public origin; ADR-0046)

Mobile-first (designed at 360 px, scales up), no sidebar, no sign-in, the school's name at the top and "From <school>, sent through SchoolOS" at the bottom. One column, 16 px base text, 44 px touch targets, system font stack plus self-hosted Noto Sans Telugu only on Telugu pages. No third-party scripts, analytics or external fonts. Text in the parent language (ADR-0044); numbers and dates in Indian formats.
- **Notice:** title, date, body, "Open the PDF" button.
- **Receipt:** a receipt card (number, date, child, class, amounts, mode) and "Save as PDF"; cancelled receipts show a red "Cancelled on …" banner above the card.
- **Dues:** list of instalments due with totals; primary button "Pay ₹X online" (when connected) that leaves to Razorpay; secondary "Pay by UPI" showing the school's QR and payee name; "Paid already? The school will send your receipt." Thank-you page after Razorpay returns.
- **Expired/unknown:** one calm page: "This link has expired or is not valid. Ask the school office, or reply to the school's WhatsApp message, for a new one." No school name.
- Every page footer: "Anyone with this link can see this page. Do not forward it."

**As built (v1.1, Direction B).** `app/(public)/public.css`: paper `#f8f7f1`, sheet `#fefdfa`,
ruled hairlines, warm ink, ledger green `#0e6549` for links, focus and the one button (the OKLCH
tokens written as hex, because old Android WebViews may not parse `oklch()`). The school's name
is the masthead in Instrument Serif (latin file only, about 21 KB, `font-display: swap`) over a
double rule; the page heading is serif; Telugu pages keep the sans (no serif glyphs) with the
self-hosted Telugu font. Each page is one ruled sheet with a faint ledger-green margin line; the
forwarding warning in the footer is semibold. Still server-rendered with no client JavaScript,
no Lenis or motion library, 44-52px controls, designed at 360px. Contrast pairs are recomputed
in `features/marketing/register-paper.test.ts`.

#### Certificate verification (public origin; ADR-0045)

On the separate verification host (`SOS_VERIFY_BASE_URL`, for example `verify.schoolos.in`; D29), `/v` shows a single code box (16 characters, auto-groups as typed, paste-friendly) and "Check"; `/v/{code}` shows a status banner (green "Valid", amber "Duplicate copy n", red "Cancelled on …") and, for valid ones, the printed values as a two-column definition list in the certificate's order, with "This page shows only what the certificate prints." Works without JavaScript (server-rendered form).

**As built (R2-1A, 2026-10-17).** `/v` is a server-rendered form (one monospace code box, 44 px controls, `autocomplete="off"`, the hint "16 letters and numbers … Dashes and spaces do not matter", and the line that certificates are found only by code) posting to `/v/check`, which redirects (303) to `/v/{code}` or back with a field error; a code typed with dashes or in lower case is redirected to its canonical form. `/v/{code}` shows the status banner in words and colour (green "Valid: issued by <school> on <date>", amber "Duplicate copy n of serial …", red "Cancelled on …"), the school, serial, date of issue and code, then the printed lines as a definition list in the certificate's order ("Left blank on the certificate" for blank lines), "This page shows only what the certificate prints." and "Anyone with this code can see what the certificate shows."; unknown codes get one "We could not find a certificate with this code. Check the code or contact the school that issued it." page (404). English only (no school or language is known before the code is found). Not built: grouping the code while typing (needs client JavaScript). Staff side: the certificate detail has an **Online check** card (code in four groups, verification link, "Checked n times", last checked, how the code was made; principal: "Add verification code" for certificates from before R2, "Withdraw code" with a reason, both step-up); school settings has a **Transfer certificate format** card (current format, an "unconfirmed layout" warning while `verified` is false, switch buttons, and the religion-and-caste switch with its explanation); the issue screen shows "Format: Andhra Pradesh TC" with the same warning, each preview line's origin ("You type it", "Worked out from the record", "Printed only when verified", "Restricted") and "Typed below", "Not printed" or "Blank, filled by hand" in place of a value, and the format's inputs with the API's labels (working days as `inputmode="numeric"`).

**Verdict (v1.1).** The status banner has a 6px start border, its own icon (check, copies,
cross; `features/public/PublicIcon.tsx`, inline SVG, `aria-hidden`) and the words; the facts
and printed lines are ruled register rows; blank lines read "Left blank on the certificate" in
muted italics. The masthead is "SchoolOS" with "Certificate check for receiving schools,
offices and employers".

#### Ask usage (C25)

On the Ask page header for `tenant.billing.read` holders: a small meter "312 of 1,000 answers this month" (no colour alarm below 80%; at ≥ 80% amber text "Near your monthly answers"). For everyone, when answers are search-only: a dismissible banner above the composer with the reason and what to do ("The school's AI answers for this month are used up. You still get matching passages. Ask the principal about a bigger bundle."). Each search-only answer repeats the reason in its outcome alert (§5.3).

**As built (R2.4, 2026-10-17).** `features/ask/UsageNotices.tsx` (`AskUsageMeter` in the chat header, `AskUsageBanner` above the composer) over `useAskUsage` (`features/ask/usage.ts`, `GET /knowledge/usage`, refetched when an answer finishes). The meter shows only when the API sent numbers: "312 of 1,000 answers this month" with a native `<meter>` (`aria-label` "AI answers used this month"), or "312 answers this month" without a bundle; "Near your monthly answers" in `warning-ink` text from `near_limit`. The banner (`Alert`, warning for search-only, info for near-limit; dismiss button with an accessible name, keyed by month and state so a new state shows again) gives the reason (`kbErrors.*`) and the next step per reason; billing readers are pointed to SchoolOS support for a bigger bundle, other staff to the principal; near-limit only for billing readers. Stored answers keep their reason after a reload (`search_only_reason`); a search-only answer without one reads "could not be checked" (`kbErrors.unchecked`), not "unavailable". A `sos://` source of a kind this build does not know opens through `GET /knowledge/resolve` (`ResolveSource.tsx`; only app-relative paths are followed) and reads "Source you can no longer open" on 404. Checks: `features/ask/usage.test.tsx` (vitest), `e2e/ask.spec.ts` (meter, near-limit dismissed by keyboard at 1366×768, search-only banner and the stored reason after reload, axe).

### 5.9 Command palette, shortcuts and phone bottom bars

**Palette** (`components/console/CommandPalette.tsx`, mounted once by `AppShell`). **Ctrl+K**
(⌘K on macOS; it overrides the browser's own Ctrl+K on these pages, owner decision) or the
top bar's "Search or jump to…" opens a native modal `<dialog class="palette">` near the top of
the screen: focus goes to the search box (WAI-ARIA combobox with `aria-activedescendant`),
Up/Down move (wrapping), Ctrl+Home/End jump, Enter runs, Escape or a backdrop click closes,
the result count is announced politely after typing stops. Matching is a fuzzy subsequence
ranker (word starts first; adapted from interior.dev, MIT, `components/console/LICENSE-interior`).
Groups:

- **Go to**: exactly the items of the sidebar (`sections` after the permission and feature
  filter), with the section as a hint. A page the menu hides is never offered.
- **Actions**: quick actions passed by the console. School: `components/shell/school-actions.ts`
  (`SCHOOL_ACTIONS`: Add a student, Collect fees, Take attendance, Enter marks, New change
  request, Upload a document, Ask the school, Download a student list, Invite a staff member),
  each an existing page with its permission (and feature), filtered by the same `allowed()`
  as the menu. Operators: Set up a new school (`platform.tenants.provision`).
- **Preferences**: Use compact / comfortable rows, Collapse or expand the menu (Ctrl+\\).

Recents and student search are not built yet (student search needs a scoped server query;
recents must store pages and actions only, never record names).

**Shortcuts**: Ctrl/⌘+K palette, Ctrl+\\ compact sidebar (both `aria-keyshortcuts` on their
buttons), plus the existing Ask (`/`, Alt+N) and fee counter keys. Never single-key shortcuts
outside a focused widget.

**Phone bottom bars** (below md, `AppShell` `bottomBar`, `bottomBarFor()` in
`school-actions.ts`): teachers (`class_teacher`, `teacher`) get Home, Attendance, Marks, Ask;
principals and owners get Home, Approvals (change requests), Ask, Messages. Only destinations
their permission-filtered sidebar shows are kept; with fewer than two, there is no bar. It is a
`<nav aria-label="Quick links">` of 56px tap targets above the home indicator; the page gets
bottom padding so nothing hides under it. Menu stays in the top bar.

## 6. Do and don't

| Do                                                                                      | Don't                                                                                                         |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Use the near-black `primary` for the main action of a view and `secondary` for the rest | Put two primary buttons on one view, or use the accent as a second brand colour                               |
| Put figures in `num`; align numeric columns end                                          | Use Instrument Serif for table or KPI numbers                                                                 |
| Keep cards hairline (`border-border`), shadows only on overlays                          | Add shadows or gradients to cards or the canvas                                                               |
| Use `KpiCard` only for numbers the API returns; show "—" (`value={null}`) when missing  | Invent or estimate statistics, even on the welcome page                                                       |
| Give every icon-only control a `label`; give charts a text alternative                  | Rely on colour alone (always text in pills, a number in rings)                                                |
| Use `SegmentedControl` for a small choice, `Tabs` for panels, `TabNav` for pages        | Build tab-like controls from `div`s                                                                           |
| Keep Telugu in the sans (automatic with `:lang(te)`)                                    | Apply `font-display`, `tracking-*` or `leading-none` to text that may be Telugu without the `:lang(te)` guard |
| Pick a variant/size/tone prop                                                           | Override colours or padding with `className` (§2.7)                                                           |
| Use Avatar initials                                                                     | Show photos of children or staff                                                                              |
| Keep print in mind: chrome gets `data-print="hide"`                                     | Hide information only in print                                                                                |

## 7. Contrast table (recomputed by `tokens.test.ts`)

Every pair below is computed from the `oklch()` values in `globals.css` (converted to sRGB as
the browser shows them) by `components/ui/tokens.test.ts`. `canvas-from`, `canvas-to` and
`canvas-neutral` are aliases of `canvas` and `primary`/`brand`/`focus` of `accent` since v1.0;
their rows are kept so older components stay covered.

| Foreground | Background | Ratio | Needs | Used for |
| --- | --- | --- | --- | --- |
| `ink` #1c1a17 | `surface` #ffffff | 17.36:1 | 4.5:1 | body text on cards |
| `ink` #1c1a17 | `canvas-from` #fbfaf9 | 16.65:1 | 4.5:1 | text on the canvas |
| `ink-muted` #575550 | `surface` #ffffff | 7.45:1 | 4.5:1 | secondary text on cards |
| `ink-muted` #575550 | `canvas-from` #fbfaf9 | 7.14:1 | 4.5:1 | secondary text on the canvas |
| `ink-muted` #575550 | `canvas-to` #fbfaf9 | 7.14:1 | 4.5:1 | secondary text on the canvas |
| `ink-muted` #575550 | `surface-muted` #f6f5f3 | 6.83:1 | 4.5:1 | table header, filled input |
| `ink-muted` #575550 | `surface-sunken` #f2f0ec | 6.54:1 | 4.5:1 | unselected segment |
| `ink-subtle` #6e6b67 | `surface` #ffffff | 5.30:1 | 4.5:1 | comparison line on KPI cards |
| `ink-subtle` #6e6b67 | `canvas-from` #fbfaf9 | 5.08:1 | 4.5:1 | notes on the canvas |
| `ink-subtle` #6e6b67 | `canvas-to` #fbfaf9 | 5.08:1 | 4.5:1 | notes on the canvas |
| `ink-subtle` #6e6b67 | `surface-muted` #f6f5f3 | 4.86:1 | 4.5:1 | placeholder in filled inputs |
| `ink` #1c1a17 | `surface-muted` #f6f5f3 | 15.94:1 | 4.5:1 | typed text in filled inputs |
| `primary` #2f62ac | `surface` #ffffff | 6.06:1 | 4.5:1 | links on cards |
| `primary` #2f62ac | `canvas-from` #fbfaf9 | 5.82:1 | 4.5:1 | links on the canvas |
| `primary` #2f62ac | `primary-soft` #e4efff | 5.22:1 | 4.5:1 | active nav item |
| `primary` #2f62ac | `info-soft` #e4efff | 5.22:1 | 4.5:1 | date chip |
| `on-primary` #ffffff | `primary` #2f62ac | 6.06:1 | 4.5:1 | legacy brand fill |
| `white` #ffffff | `brand` #2f62ac | 6.06:1 | 4.5:1 | brand button |
| `white` #ffffff | `brand-strong` #1d4682 | 9.33:1 | 4.5:1 | brand button hover |
| `on-action` #ffffff | `action` #1c1a17 | 17.36:1 | 4.5:1 | primary (near-black) button, delta pill |
| `on-action` #ffffff | `action-hover` #35332e | 12.62:1 | 4.5:1 | primary button hover |
| `white` #ffffff | `danger` #c22826 | 5.79:1 | 4.5:1 | danger button |
| `danger` #c22826 | `danger-soft` #ffece9 | 5.08:1 | 4.5:1 | danger alert, negative chip |
| `danger` #c22826 | `surface` #ffffff | 5.79:1 | 4.5:1 | error text |
| `warning-ink` #8a5619 | `warning-soft` #ffe9cb | 5.19:1 | 4.5:1 | warning alert |
| `success-ink` #115629 | `success-soft` #dcf7e1 | 7.72:1 | 4.5:1 | success alert |
| `positive-ink` #115629 | `positive-soft` #dcf7e1 | 7.72:1 | 4.5:1 | positive chip |
| `info-ink` #1d4682 | `info-soft` #e4efff | 8.04:1 | 4.5:1 | info alert |
| `violet-ink` #5d4099 | `violet-soft` #efebff | 6.77:1 | 4.5:1 | violet chip |
| `teal-ink` #00635b | `teal-soft` #d9f6f1 | 6.27:1 | 4.5:1 | teal chip |
| `ink` #1c1a17 | `canvas-neutral` #fbfaf9 | 16.65:1 | 4.5:1 | text on the neutral canvas |
| `ink-muted` #575550 | `canvas-neutral` #fbfaf9 | 7.14:1 | 4.5:1 | secondary text on the neutral canvas |
| `ink-subtle` #6e6b67 | `canvas-neutral` #fbfaf9 | 5.08:1 | 4.5:1 | notes on the neutral canvas |
| `primary` #2f62ac | `canvas-neutral` #fbfaf9 | 5.82:1 | 4.5:1 | links and breadcrumbs on the neutral canvas |
| `focus` #2f62ac | `canvas-neutral` #fbfaf9 | 5.82:1 | 3:1 | focus ring on the neutral canvas |
| `platform-ink` #ffffff | `platform` #1d1a15 | 17.34:1 | 4.5:1 | platform sidebar and top bar text |
| `platform-ink` #ffffff | `platform-hover` #312d27 | 13.68:1 | 4.5:1 | platform sidebar hover and active row |
| `platform-muted` #d4d1ca | `platform` #1d1a15 | 11.37:1 | 4.5:1 | platform sidebar items, headings, role line |
| `platform-accent` #faca4b | `platform` #1d1a15 | 11.24:1 | 3:1 | platform focus ring and active marker |
| `platform-accent-ink` #1d1a15 | `platform-accent` #faca4b | 11.24:1 | 4.5:1 | platform badge |
| `platform` #1d1a15 | `platform-soft` #fdf5df | 15.94:1 | 4.5:1 | active platform nav item |
| `primary` #2f62ac | `primary-soft` #e4efff | 5.22:1 | 3:1 | sidebar active bar on the active row |
| `primary` #2f62ac | `surface-muted` #f6f5f3 | 5.56:1 | 4.5:1 | "Switch school" link on the school block |
| `platform-accent` #faca4b | `platform-hover` #312d27 | 8.87:1 | 3:1 | platform sidebar active bar |
| `border-control` #888682 | `surface` #ffffff | 3.63:1 | 3:1 | input and switch boundary on cards |
| `border-control` #888682 | `surface-muted` #f6f5f3 | 3.34:1 | 3:1 | input boundary against its fill |
| `border-strong` #7c7a75 | `surface` #ffffff | 4.29:1 | 3:1 | legacy control boundary |
| `focus` #2f62ac | `surface` #ffffff | 6.06:1 | 3:1 | focus ring on cards |
| `focus` #2f62ac | `canvas-from` #fbfaf9 | 5.82:1 | 3:1 | focus ring on the canvas |
| `success` #287c42 | `surface` #ffffff | 5.18:1 | 3:1 | switch on, timeline check |
| `chart-1` #2f62ac | `surface` #ffffff | 6.06:1 | 3:1 | chart series 1 |
| `chart-2` #be7100 | `surface` #ffffff | 3.78:1 | 3:1 | chart series 2 (amber) |
| `chart-3` #1b8278 | `surface` #ffffff | 4.66:1 | 3:1 | chart series 3 (teal) |
| `chart-4` #6b4cae | `surface` #ffffff | 6.42:1 | 3:1 | chart series 4 (violet) |
| `ink` #1c1a17 | `canvas` #fbfaf9 | 16.65:1 | 4.5:1 | text on the flat canvas |
| `ink-muted` #575550 | `canvas` #fbfaf9 | 7.14:1 | 4.5:1 | secondary text on the flat canvas |
| `ink-subtle` #6e6b67 | `canvas` #fbfaf9 | 5.08:1 | 4.5:1 | notes and group labels on the canvas |
| `ink-subtle` #6e6b67 | `surface-sunken` #f2f0ec | 4.66:1 | 4.5:1 | notes on the sunken track |
| `accent` #2f62ac | `surface` #ffffff | 6.06:1 | 4.5:1 | links, breadcrumb links on white |
| `accent` #2f62ac | `canvas` #fbfaf9 | 5.82:1 | 4.5:1 | links and the top bar breadcrumb on the canvas |
| `accent` #2f62ac | `accent-soft` #e4efff | 5.22:1 | 4.5:1 | current nav item, selected row text |
| `accent-strong` #1d4682 | `accent-soft` #e4efff | 8.04:1 | 4.5:1 | selected chip text |
| `on-accent` #ffffff | `accent` #2f62ac | 6.06:1 | 4.5:1 | selection count badge, accent fill |
| `on-accent` #ffffff | `accent-strong` #1d4682 | 9.33:1 | 4.5:1 | accent fill hover |
| `accent` #2f62ac | `accent-soft` #e4efff | 5.22:1 | 3:1 | current nav item marker on its tint |
| `focus` #2f62ac | `canvas` #fbfaf9 | 5.82:1 | 3:1 | focus ring on the flat canvas |
| `focus` #2f62ac | `surface-muted` #f6f5f3 | 5.56:1 | 3:1 | focus ring on table headers and filled inputs |
| `ink` #1c1a17 | `surface-hover` #f1f0ed | 15.24:1 | 4.5:1 | row text on hover |
| `ink-muted` #575550 | `surface-hover` #f1f0ed | 6.53:1 | 4.5:1 | secondary row text on hover |
| `border-control` #888682 | `canvas` #fbfaf9 | 3.49:1 | 3:1 | checkbox boundary on the canvas |
| `platform-muted` #d4d1ca | `platform-hover` #312d27 | 8.97:1 | 4.5:1 | operator sidebar muted text on hover |
| `ink` #1c1a17 | `accent-soft` #e4efff | 14.96:1 | 4.5:1 | current sidebar item, palette option text |
| `ink-muted` #575550 | `accent-soft` #e4efff | 6.41:1 | 4.5:1 | filter chip label |
| `warning-ink` #8a5619 | `surface` #ffffff | 6.13:1 | 4.5:1 | "Changes not saved" on the save bar |

Secondary buttons and round icon buttons use the decorative `border-soft`: a text label or the
icon itself identifies the control, so WCAG 1.4.11 does not require a 3:1 boundary there. Inputs
and the switch have no text inside when empty, so they use `border-control` (3:1).

## 8. Open points

- v1.1 page wave (operator console, public, marketing, session pages), left open: the
  operator forms (provision, plans, announcements, subscriptions) keep their inline errors and
  existing buttons; they do not yet use `ErrorSummary` + `SaveBar` + `Steps` (the provision
  stepper still shows its own progress); the operator lists keep their URL filter forms, so no
  `FilterChips` there yet; the dev-mode render of `notFound()` under the `(public)` root layout
  is blank in `next dev` (production renders the uniform page; unchanged by this wave); the
  illustrations were drawn and reviewed by an agent, an owner or illustrator review is still
  worthwhile (figures are simple, faces minimal).
- v1.0 foundation (2026-10-18), for the page-redesign wave: screens still use their own layouts
  on the new tokens; each list screen moves to `DataTableV2` + `Pagination` + `FilterChips`,
  each form to `ErrorSummary` + `SaveBar` (+ `Steps`), per §5. Not built yet: palette student
  search and recents, sticky record page headers, marketing in Direction B, illustrations, a
  `/dev/ui` section for the new components. Pages that pass a two-crumb `breadcrumb` no
  longer show it (the top bar does); pages with deeper trails still show theirs, so the
  record trail and the top bar overlap slightly until each page is redesigned.

- Motion (§5.5), for the product owner: the owner asked for a sliding tab/segmented indicator,
  an animated accordion and toasts; they were left out on purpose (keyboard-driven, no Baseline
  height animation, Sonner blocked by the CSP). The e2e axe checks run without waiting for
  animations; a fade is at most 200ms, but a slow CI machine could catch one mid-fade.
- Weights (§3.1): swept everywhere (2026-10-04); `font-weights.test.ts` has no allowlist left.
  On the public pages the former `font-medium` text (nav links, chips, the FAQ questions, the
  four sources, the dark-band points) is now semibold, which reads a little heavier than before
  (the old 500 rendered as 400); the product owner may ask for any of them to go back to the
  default weight.

- English first (§5.4, ADR-0036), for the product owner: while Telugu is off, a new class and a
  platform banner store their English text in the Telugu fields because the API still requires
  them (drop the requirement in the API, or accept that these need a Telugu pass when Telugu
  returns); notifications the API created in Telugu before the switch still show their stored
  text; the school's `languages` setting is kept but not shown.

- Sidebar (§5.2), for the product owner: below lg "Lock now" sits inside the drawer (one tap
  more on phones and tablets); the school's name costs a GET `/me/schools` on each full page
  load (a school name on GET `/me` would save it); there is no keyboard shortcut for the
  compact mode.

- Screens still use `PageHeader`, `Card` and the other primitives in their old arrangement; each
  screen adopts breadcrumbs, KPI rows and filter bars in its own change.
- Ask chat (§5.3), for the product owner: the recents show only while an Ask page is open (the
  menu stays short elsewhere; one click more from other pages); example cards fill the box
  instead of sending; `/` is a single-character shortcut (WCAG 2.1.4 asks for a way to turn it
  off; it only moves focus); a stream keeps going when you open another conversation but stops
  when you leave Ask. The conversation and memory endpoints use the generated client; the SSE
  event payloads, which OpenAPI does not describe, are typed in `features/ask/sse.ts` from
  `knowledge/domain.py` and must be changed together with it.
- Calmer app (0.8), for the product owner: between 1024 and 1279px "Ask on WhatsApp" in the
  header bar is an icon button (§5.6; the full label did not fit beside the page links without
  crowding them), which a visitor may not recognise as quickly as the labelled button; the
  neutral canvas covers the screens
  named in §3.2 only (Ask, notices, attendance and the other school pages keep the gradient
  until someone adds them to `NEUTRAL_CANVAS_PATHS`); the consequence line is used on the
  change-request and finding decisions so far; other dialogs adopt it in their own changes.
  The pricing page's fee factors (campuses) and the implementation fee are the owner's
  packaging decisions; docs/16 does not model campuses or a one-time fee yet.
- Mobile pass v1.2 (§5.7), for the product owner: the keyboard check is a heuristic (a
  hardware keyboard on a tablet hides the bottom bar while a field has focus); the fees "More"
  menus still open their dialogs through a hidden trigger (`features/fees/parts.tsx`) and can
  move to the controlled `open` prop; the remaining sideways-scrolling tables are grids and
  ledgers by design.
- Mobile (§5.7), for the product owner: dialogs are bottom sheets on phones without swipe to
  dismiss (a drag gesture needs custom pointer code; Escape, × and Cancel close them); other
  tables (registers, ledgers, plans, usage, audit) still scroll sideways on phones; touch
  controls are 44px only under a coarse pointer, so a touch laptop with a trackpad keeps
  desktop density; `interactive-widget` is not supported by Safari (it uses its own keyboard
  scrolling).
- `Sparkline` and `ProgressRing` cover small inline charts only; a chart library decision (if
  full charts are needed) needs an ADR (licence, bundle size, CSP).

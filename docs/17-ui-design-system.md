# 17 · UI Design System (web)

| Field | Value |
|---|---|
| Version | 0.2 · 2026-09-29 |
| Changes | 0.2: §5.1 responsive layout (spacing scale, menu drawer, TableScroll, dialogs, checks). 0.1: new document: tokens, fonts, primitives, shells, do/don't, contrast table (UI refresh foundation) |
| Requirements | NFR-A11Y-001 (WCAG 2.2 AA), NFR-I18N-001 (English and Telugu), SEC-010 (CSP, self-hosted assets) |
| Related | 02-PRD §8 (UX principles), 13 §5 (TypeScript/Next.js standards), CLAUDE.md §10 |
| Code | `apps/web/src/app/globals.css` (tokens), `apps/web/src/components/ui/` (primitives, exported from `index.ts`), `apps/web/src/components/shell/` (shells) |
| Living reference | `/[locale]/dev/ui`: every primitive and variant with synthetic content. Local development only (same guard as `/dev/sign-in`: `next dev` + local stub issuer; a 404 everywhere else) |

---

## 1. The look in one paragraph

Content sits on **white cards** (20px radius, 1px hairline border, soft large shadow) laid on a
**very soft sky-blue to pale-aqua gradient canvas**. Text is a clean neo-grotesk sans (Inter);
**big numbers** use a light serif (Instrument Serif); small **eyebrow labels** are mono uppercase
(JetBrains Mono). The primary button is **near-black**; brand blue is for links, focus and the one
brand call to action. Status uses soft chips and a few **gradient pills** (In progress, Review, Done).
Navigation is a slim **icon rail** plus a **grouped list panel**; every page starts with a
**page-header card** (breadcrumb, title, actions). The platform admin panel keeps a **dark violet**
rail and top bar so operators always know they are in the control plane.

## 2. Hard rules (never traded for looks)

1. **WCAG 2.2 AA.** Text 4.5:1 (3:1 for large text and UI boundaries). Every token pair a primitive
   uses is listed in §7 and recomputed by `components/ui/tokens.test.ts`; add a row there when you add
   a pair. Gradients are checked at their **lightest** stop. Focus is always visible (3px ring,
   `--color-focus`; yellow on platform chrome; white on the blue AI panel). Colour is never the only
   signal: pills carry text, rings carry a number, the switch knob moves.
2. **Telugu.** Noto Sans Telugu is the fallback of every font stack, `:lang(te)` keeps the taller
   line height, and the serif display and mono eyebrow switch back to the sans with normal spacing
   in Telugu (`.font-display:lang(te)`, `.eyebrow:lang(te)`). Never set tight line heights or
   letter-spacing on text that may be Telugu.
3. **CSP (SEC-010).** No `style` attributes, no external resources. Colours come from classes;
   SVG geometry uses presentation attributes (`strokeDashoffset`, `points`), which CSP allows.
   Fonts are self-hosted via `@fontsource` (`font-src 'self'`).
4. **Baseline Widely Available** only (CLAUDE.md §10). Used: CSS gradients, `:has()` (segmented
   control, drawer scroll lock), `:modal`, native `<dialog>`, `<details>`, `accent-color`,
   `env()`, `dvh` (after a `vh` fallback), `@media (prefers-reduced-motion)`,
   `@media (forced-colors)`. Not used: `backdrop-filter` (slow office PCs), anchor positioning,
   container queries, `appearance: base-select`.
5. **Print.** Gradients, shadows and tints disappear; cards print as plain bordered boxes; chrome
   (`data-print="hide"`) is hidden; A4 margins; Telugu line height 1.8.
6. **Motion.** Only colour transitions and the skeleton shimmer; the shimmer is a flat block under
   `prefers-reduced-motion`.
7. **`cn()` joins classes, it does not merge them.** A `className` can add spacing, width or
   layout, but it cannot reliably override a colour, padding or radius the component already sets
   (CSS order decides, not class order). Use the variant, size, `tone` or `padding` props instead,
   or ask for a new variant.

## 3. Tokens (`globals.css`, Tailwind v4 `@theme`)

Use them as Tailwind utilities (`bg-surface`, `text-ink-muted`, `border-border`, `rounded-xl`,
`shadow-card`, `font-display`). Old names still work (`primary`, `border-strong`, `canvas`, …).

| Group | Tokens | Use |
|---|---|---|
| Canvas | `canvas` (flat), `canvas-from`, `canvas-via`, `canvas-to` | Body background gradient (set globally; do not repeat it) |
| Surfaces | `surface` (white), `surface-muted` (filled inputs, table header), `surface-sunken` (segmented track), `surface-hover` (row hover) | Cards and controls |
| Borders | `border` (hairline, decorative), `border-soft` (secondary buttons), `border-control` (inputs, switch: 3:1), `border-strong` (legacy) | |
| Ink | `ink`, `ink-muted`, `ink-subtle` | Body, secondary, "1,157 last month" lines |
| Action | `action`, `action-hover`, `on-action` | Near-black primary button, dark pills |
| Brand | `primary` (links, focus, active nav), `primary-soft`, `brand`, `brand-strong` | |
| Status | `info-*`, `success`, `success-*`, `positive-*`, `warning-*`, `danger*`, `violet-*`, `teal`, `teal-*` | Alerts, chips, badges |
| Pills | `pill-blue-*`, `pill-violet-*`, `pill-teal-*` | Gradient status pills (classes `.pill-gradient-*`) |
| AI | `ai-from`, `ai-to` | `.ai-gradient` panel |
| Charts | `chart-1` blue, `chart-2` amber, `chart-3` teal, `chart-4` violet, `chart-track` | Series colours (3:1 on white) |
| Platform | `platform`, `platform-hover`, `platform-ink`, `platform-muted`, `platform-accent`, `platform-accent-ink`, `platform-soft` | Control-plane chrome only |
| Radii | `xs` 4, `sm` 8, `md` 10 (buttons, inputs), `lg` 14, `xl` 20 (cards), `2xl` 24, `full` | |
| Shadows | `shadow-card`, `shadow-raised` (buttons, selected segment), `shadow-popover` (dialogs, menus) | |
| Fonts | `font-sans` (Inter → Noto Sans Telugu), `font-display` / `font-serif` (Instrument Serif), `font-mono` (JetBrains Mono → Noto Sans Telugu) | |

Component classes (in `@layer components`): `.eyebrow`, `.select-chevron`, `.skeleton`,
`.pill-gradient-blue|violet|teal`, `.ai-gradient`, `.ai-chrome` (white focus ring),
`.quote-gradient`, `.platform-chrome` (yellow focus ring).

Fonts (all OFL-1.1, pinned exact in `apps/web/package.json`): `@fontsource-variable/inter`
(variable weights), `@fontsource/instrument-serif` (400), `@fontsource/jetbrains-mono` (500),
`@fontsource/noto-sans-telugu` (400/600/700). Every file declares `unicode-range`, so a page
downloads only the scripts it shows.

## 4. Components (`@/components/ui`)

Import from the files or from `@/components/ui` (index). All are Server-Component-safe unless
marked (client).

| Component | Props (beyond children/className) | Notes |
|---|---|---|
| `Button` | `variant`: `primary` (near-black, default) · `primary-dark` (alias) · `brand` (blue) · `secondary` · `ghost` · `danger` · `inverse` (white, on dark/blue); `size`: `sm` · `md` · `lg`; all `<button>` props | `type="button"` by default |
| `ButtonLink`, `buttonClasses(variant, size)` | `href`, `variant`, `size` | Links that look like buttons (`buttonClasses` for plain `<a>` to BFF routes) |
| `IconButton`, `iconButtonClasses` | `label` (required accessible name), `variant`: `secondary` · `ghost` · `primary` · `danger`; `size`: `sm` 32 · `md` 40 · `lg` 48; `dot` | Round, hairline border. Put the unread count in `label` too |
| `Icon`, `ICON_NAMES` | `name` | Decorative 24×24 stroke icons; a size class replaces the 20px default |
| `Card` | `title`, `eyebrow`, `description`, `actions`, `headingLevel` 2/3, `padding` `none`·`sm`·`md`·`lg`, `tone` `default`·`muted`·`outline` | `<section>` named by its title |
| `CardHeader`, `cardClasses({padding, tone})` | as above, `headingId` | For cards that are not a `<section>` |
| `PageHeader` | `title` (the page's one `h1`), `description`, `actions`, `badge`, `breadcrumb` (`Crumb[]`), `eyebrow`, `plain` | Card by default; `plain` inside other cards |
| `Breadcrumb` | `items: {label, href?}[]`, `label` | Last crumb is `aria-current="page"` |
| `Eyebrow` | `as` `p`·`span`·`div`·`h2`·`h3`, `tone` `muted`·`ink`·`brand`·`inverse`, `id` | Mono uppercase label |
| `Badge` | `tone`: `neutral` · `info` · `success` · `warning` · `danger` · `platform` · `violet` · `teal` | Soft status label |
| `Pill` | `variant`: `progress` · `review` · `done` (gradients) · `dark` · `date` · `tag` · `sample` · `positive` · `negative` · `command`; `size` `sm`·`md` | Always with text |
| `DeltaPill` | `value` ("+2.7%"), `direction` `up`·`down`·`flat`, `label` (read instead of the value) | Dark pill with arrow |
| `StatCard` | `label`, `value` (string or null), `unavailableLabel`, `hint` | Inside a `<dl>`; serif number |
| `KpiCard` | `label`, `value`, `unavailableLabel`, `delta` (`{value, direction?, label?}`), `comparison`, `sparkline`, `aside` | `role="group"` named by the label; serif number; never invent numbers |
| `Sparkline` | `values: number[]`, `label` (text alternative, required), `color` 1–4, `area` | Inline SVG, `aria-hidden` + sr-only label |
| `ProgressRing` | `value` 0–100, `label` (required), `size` `sm`·`md`·`lg`, `color` 1–4, `showValue` | `role="progressbar"` with `aria-valuetext` |
| `Input`, `Textarea`, `TextField`, `TextAreaField`, `Field`, `controlClasses` | unchanged | Filled look (soft grey, 3:1 boundary) |
| `SearchInput` | `label` (required), `labelVisible`, `icon`, `wrapperClassName`, input props | `type="search"`; put it in `<form role="search">` with a "Filter" button |
| `Select`, `SelectField` | unchanged | Native `<select>` with CSS chevron |
| `Toggle` (client) | `label`, `description`, `checked`/`defaultChecked`, `onCheckedChange`, `disabled`, `name` (hidden input "on"/"off"), `labelFirst` | `<button role="switch" aria-checked>`; green when on. For settings that apply at once |
| `SegmentedControl` (client) | `legend` (required), `legendVisible`, `options` `{value,label,disabled?}[]`, `value`/`defaultValue`, `onValueChange`, `name`, `size` `sm`·`md`, `block` | Fieldset of native radios: arrows, form value, announcements for free |
| `Tabs` (client) | `label`, `items`, `defaultTabId`, `variant` `segmented` (default) · `underline` | WAI-ARIA tabs; panels stay in the DOM |
| `TabNav` | `label`, `items`, `activeId`, `variant` | Tabs that are links (`aria-current`) |
| `Table`, `THead`, `TBody`, `Tr`, `Th`, `Td` | `Table` `density` `comfortable` (≈56px rows) · `compact`; `stickyFirstColumn` (below md) | Light header, dividers, hover |
| `TableScroll` | `label` (required: names the region), `framed` (card frame) | Wrap every hand-built `Table` in it: the table scrolls sideways inside a focusable region, never the page |
| `DataTable` | unchanged + `density`, `stickyFirstColumn` | Loading, error, empty states; renders `TableScroll framed` |
| `EmptyState` | `title`, `body`, `action`, `icon` | Say what to do next |
| `LoadingState`, `Skeleton` | `label`, `rows`, `variant` `rows`·`cards` | `role="status"`; shimmer off under reduced motion |
| `Alert` | unchanged (`tone`, `title`, `live`) | |
| `Dialog` (client), `ActionDialog` (client) | unchanged | Native `<dialog>`, restyled |
| `Avatar` | `name`, `size` `sm`·`md`·`lg`, `decorative` | Initials only, never photos |
| `AvatarStack` | `names`, `label` (required), `max`, `size` | One image named by `label`, "+N" |
| `Timeline` | `items` `{id, title, time?, body?, chips?, status? done·current·pending, statusLabel?}[]`, `label` | Ordered list, mono times |
| `AiPanel` | `greeting` (its heading), `eyebrow`, `description`, `suggestions` `{id,label,href?|onSelect?}[]`, `suggestionsLabel`, children (question form) | For the Ask screens. Answers go in normal cards **with source chips** (invariant 8) |
| `QuoteBlock` | `label`, children | Pale green quote |
| `SidebarNav` (client) | `label`, `items` **or** `sections` (`NavSection[]`: `{id, label, icon, items}`), `theme` `school`·`platform` | One current page; group headings name their lists |
| `UsageMeter`, `LanguageSwitcher`, `Value`, `Label`, `SecretOnce`, `ApiErrorAlert` | unchanged | |

Shells (`@/components/shell`): `SchoolShell` and `PlatformShell` (props unchanged) render
`AppShell` (client): skip link, icon rail (md and up; one round button per non-empty group,
current group dark-filled), list panel (lg and up; below lg the "Menu" button opens the same
list in a drawer, see §5.1), top bar card (bell, session controls, "Switch school", language)
and `<main id="main">`.
`MinimalShell` (`wide` for tables) and `Wordmark` serve pages outside a school.

## 5. Page recipe (for screen agents)

```tsx
<PageHeader
  title={t("title")}
  description={t("description")}
  breadcrumb={[{ label: t("nav.home"), href: "/" }, { label: t("title") }]}
  actions={<ButtonLink href="/students/new">{t("add")}</ButtonLink>}
/>
<div className="grid gap-4 md:grid-cols-3">{/* KpiCard × n, only real numbers */}</div>
<Card title={t("list.title")} actions={<SearchInput label={t("search")} name="q" />}>
  <DataTable caption={t("list.caption")} captionHidden … />
</Card>
```

- One `h1` per page (PageHeader); sections are `h2` (Card default); cards inside cards use `headingLevel={3}`.
- Grids: `gap-4` between cards, `space-y-6` between page blocks; cards pad themselves.
- Filters: `<form role="search">` with `SearchInput` + `Select` + `Button variant="secondary"` "Filter".
- Status: `Badge` for record states (active/suspended), `Pill` gradients for workflow states
  (in progress/review/done), `Pill positive/negative` for matches/mismatches.

### 5.1 Responsive layout

Every screen must work at **1366×768** (office PC, the design baseline) and **375×812** (phone),
in English and Telugu: no horizontal page scroll, nothing past the screen or its card edge, no
clipped text, touch targets of at least 24×24 px (WCAG 2.5.8; inline links in a sentence and
well-spaced small targets are the exceptions). `e2e/responsive.spec.ts` checks all of it for
every screen (`make e2e` with `E2E_STAND_IN=1`, on every pull request); `e2e/audit/responsive.audit.ts`
sweeps ten viewports with screenshots (`make e2e-audit`, nightly).

- **Spacing scale** (`globals.css`): `--page-gutter` 16px on phones, 24px from md, 32px from lg
  (utility `px-page`, outside the consoles); `--shell-gutter` 16/24px (`shell-gutter`, inside
  the consoles); `--content-max` 108rem (`shell-frame`). Both gutter utilities add the
  safe-area insets (`viewport-fit=cover`), which are 0 on desktops.
- **Full height**: `min-h-viewport` / `h-viewport` write `100vh` first and `100dvh` second, so
  browsers without dynamic viewport units keep the `vh` value.
- **Shell**: rail from md, list panel from lg. Below lg the "Menu" button opens the list in a
  **modal `<dialog>` drawer** (`.drawer`): focus moves to its close button and cannot leave it
  (the page behind is inert and does not scroll), Escape, the close button, a tap on the dimmed
  page, following a link or widening the window to lg close it, and focus returns to the
  button. The drawer renders its navigation only while open, so there is one "Main" landmark.
- **Cards** carry `min-w-0`, so in a grid or flex row they shrink to their track and a wide
  child scrolls inside instead of pushing the page wider.
- **Tables** always sit in `TableScroll` (or `DataTable`): `position: relative` keeps sr-only
  captions inside the scroll box. Use `stickyFirstColumn` when the first cell names the row.
- **Long strings**: `body` has `overflow-wrap: break-word`; `dd` values and the page title wrap
  anywhere; use `break-anywhere` for emails, IDs and URLs in flex rows.
- **Dialogs** are `w-[min(<n>rem,calc(100vw-2rem))]` and `m-auto`; padding tightens below sm
  and the footer buttons share the row on phones. The native modal keeps them inside the
  viewport and scrolls a tall body.
- **Buttons** wrap a long (Telugu) label below sm instead of overflowing.
- **Motion**: buttons fade colour only on hover and only without `prefers-reduced-motion`;
  under it every transition and animation is cut to 0.01ms (`globals.css`). The drawer opens
  without animation.
- **Print** is unchanged by all of this: chrome is hidden, scroll regions print in full, table
  headers wrap and A4 content stays inside the page (checked in `e2e/responsive.spec.ts`).

Web-platform features used here and their status: `dvh` (Baseline 2022, with the `vh`
fallback), `env(safe-area-inset-*)`, `:has()` and `:modal` (Baseline widely available),
media query range syntax, `overscroll-behavior`, `overflow-wrap: anywhere`, native modal
`<dialog>` (inert background, Escape close request).

## 6. Do and don't

| Do | Don't |
|---|---|
| Use the near-black `primary` for the main action of a card and `secondary` for the rest | Put two `brand` (blue) buttons on one screen |
| Use `KpiCard` only for numbers the API returns; show "—" (`value={null}`) when missing | Invent or estimate statistics, even on the welcome page |
| Give every icon-only control a `label`; give charts a text alternative | Rely on colour alone (always text in pills, a number in rings) |
| Use `SegmentedControl` for a small choice, `Tabs` for panels, `TabNav` for pages | Build tab-like controls from `div`s |
| Keep Telugu in the sans (automatic with `:lang(te)`) | Apply `font-display`, `tracking-*` or `leading-none` to text that may be Telugu without the `:lang(te)` guard |
| Pick a variant/size/tone prop | Override colours or padding with `className` (§2.7) |
| Use Avatar initials | Show photos of children or staff |
| Keep print in mind: chrome gets `data-print="hide"` | Hide information only in print |

## 7. Contrast table (recomputed by `tokens.test.ts`)

| Foreground | Background | Ratio | Needs | Used for |
|---|---|---|---|---|
| `ink` #111827 | `surface` #ffffff | 17.74:1 | 4.5:1 | body text on cards |
| `ink` #111827 | `canvas-from` #dbe9fa | 14.40:1 | 4.5:1 | text on the canvas |
| `ink-muted` #4b5563 | `surface` #ffffff | 7.56:1 | 4.5:1 | secondary text on cards |
| `ink-muted` #4b5563 | `canvas-from` #dbe9fa | 6.14:1 | 4.5:1 | secondary text on the canvas |
| `ink-muted` #4b5563 | `canvas-to` #dcf2f0 | 6.48:1 | 4.5:1 | secondary text on the canvas |
| `ink-muted` #4b5563 | `surface-muted` #f3f5f8 | 6.92:1 | 4.5:1 | table header, filled input |
| `ink-muted` #4b5563 | `surface-sunken` #eef1f5 | 6.67:1 | 4.5:1 | unselected segment |
| `ink-subtle` #5b6576 | `surface` #ffffff | 5.89:1 | 4.5:1 | comparison line on KPI cards |
| `ink-subtle` #5b6576 | `canvas-from` #dbe9fa | 4.78:1 | 4.5:1 | notes on the canvas |
| `ink-subtle` #5b6576 | `canvas-to` #dcf2f0 | 5.05:1 | 4.5:1 | notes on the canvas |
| `ink-subtle` #5b6576 | `surface-muted` #f3f5f8 | 5.39:1 | 4.5:1 | placeholder in filled inputs |
| `ink` #111827 | `surface-muted` #f3f5f8 | 16.24:1 | 4.5:1 | typed text in filled inputs |
| `primary` #1d4ed8 | `surface` #ffffff | 6.70:1 | 4.5:1 | links on cards |
| `primary` #1d4ed8 | `canvas-from` #dbe9fa | 5.44:1 | 4.5:1 | links on the canvas |
| `primary` #1d4ed8 | `primary-soft` #e8efff | 5.82:1 | 4.5:1 | active nav item |
| `primary` #1d4ed8 | `info-soft` #eff4ff | 6.08:1 | 4.5:1 | date chip |
| `on-primary` #ffffff | `primary` #1d4ed8 | 6.70:1 | 4.5:1 | legacy brand fill |
| `white` #ffffff | `brand` #2563eb | 5.17:1 | 4.5:1 | brand button |
| `white` #ffffff | `brand-strong` #1e40af | 8.72:1 | 4.5:1 | brand button hover |
| `on-action` #ffffff | `action` #111827 | 17.74:1 | 4.5:1 | primary (near-black) button, delta pill |
| `on-action` #ffffff | `action-hover` #1f2937 | 14.68:1 | 4.5:1 | primary button hover |
| `white` #ffffff | `danger` #b42318 | 6.57:1 | 4.5:1 | danger button |
| `danger` #b42318 | `danger-soft` #fef3f2 | 6.05:1 | 4.5:1 | danger alert, negative chip |
| `danger` #b42318 | `surface` #ffffff | 6.57:1 | 4.5:1 | error text |
| `warning-ink` #7a2e0e | `warning-soft` #fffaeb | 9.05:1 | 4.5:1 | warning alert |
| `success-ink` #05603a | `success-soft` #ecfdf3 | 7.26:1 | 4.5:1 | success alert |
| `positive-ink` #166534 | `positive-soft` #f0fdf4 | 6.81:1 | 4.5:1 | positive chip |
| `info-ink` #1e40af | `info-soft` #eff4ff | 7.91:1 | 4.5:1 | info alert |
| `violet-ink` #5b21b6 | `violet-soft` #f3efff | 7.96:1 | 4.5:1 | violet chip |
| `teal-ink` #115e59 | `teal-soft` #e6f7f5 | 6.86:1 | 4.5:1 | teal chip |
| `white` #ffffff | `pill-blue-from` #1d4ed8 | 6.70:1 | 4.5:1 | In progress pill (dark stop) |
| `white` #ffffff | `pill-blue-to` #2563eb | 5.17:1 | 4.5:1 | In progress pill (light stop) |
| `white` #ffffff | `pill-violet-from` #4f46e5 | 6.29:1 | 4.5:1 | Review pill (dark stop) |
| `white` #ffffff | `pill-violet-to` #7c3aed | 5.70:1 | 4.5:1 | Review pill (light stop) |
| `white` #ffffff | `pill-teal-from` #0f766e | 5.47:1 | 4.5:1 | Done pill (dark stop) |
| `white` #ffffff | `pill-teal-to` #0e7a6f | 5.21:1 | 4.5:1 | Done pill (light stop) |
| `white` #ffffff | `ai-from` #1e3a8a | 10.36:1 | 4.5:1 | AI panel text (dark stop) |
| `white` #ffffff | `ai-to` #2563eb | 5.17:1 | 4.5:1 | AI panel text (light stop) |
| `platform-ink` #ffffff | `platform` #3b0764 | 15.00:1 | 4.5:1 | platform rail text |
| `platform-ink` #ffffff | `platform-hover` #581c87 | 10.88:1 | 4.5:1 | platform rail hover |
| `platform-muted` #e9d5ff | `platform` #3b0764 | 11.02:1 | 4.5:1 | platform rail secondary text |
| `platform-accent` #fcd34d | `platform` #3b0764 | 10.40:1 | 3:1 | platform focus ring and active marker |
| `platform-accent-ink` #3b0764 | `platform-accent` #fcd34d | 10.40:1 | 4.5:1 | platform badge |
| `platform` #3b0764 | `platform-soft` #f5f0ff | 13.42:1 | 4.5:1 | active platform nav item |
| `border-control` #7b8494 | `surface` #ffffff | 3.77:1 | 3:1 | input and switch boundary on cards |
| `border-control` #7b8494 | `surface-muted` #f3f5f8 | 3.45:1 | 3:1 | input boundary against its fill |
| `border-strong` #6b7280 | `surface` #ffffff | 4.83:1 | 3:1 | legacy control boundary |
| `focus` #1d4ed8 | `surface` #ffffff | 6.70:1 | 3:1 | focus ring on cards |
| `focus` #1d4ed8 | `canvas-from` #dbe9fa | 5.44:1 | 3:1 | focus ring on the canvas |
| `success` #15803d | `surface` #ffffff | 5.02:1 | 3:1 | switch on, timeline check |
| `chart-1` #2563eb | `surface` #ffffff | 5.17:1 | 3:1 | chart series 1 |
| `chart-2` #d97706 | `surface` #ffffff | 3.19:1 | 3:1 | chart series 2 (amber) |
| `chart-3` #0d9488 | `surface` #ffffff | 3.74:1 | 3:1 | chart series 3 (teal) |
| `chart-4` #7c3aed | `surface` #ffffff | 5.70:1 | 3:1 | chart series 4 (violet) |

Secondary buttons and round icon buttons use the decorative `border-soft`: a text label or the
icon itself identifies the control, so WCAG 1.4.11 does not require a 3:1 boundary there. Inputs
and the switch have no text inside when empty, so they use `border-control` (3:1).

## 8. Open points

- Screens still use `PageHeader`, `Card` and the other primitives in their old arrangement; each
  screen adopts breadcrumbs, KPI rows and filter bars in its own change.
- The Ask screens have not adopted `AiPanel` yet.
- `Sparkline` and `ProgressRing` cover small inline charts only; a chart library decision (if
  full charts are needed) needs an ADR (licence, bundle size, CSP).

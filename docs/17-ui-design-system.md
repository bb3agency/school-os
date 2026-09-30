# 17 · UI Design System (web)

| Field            | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Version          | 0.8 · 2026-10-01                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Changes          | 0.8: calmer app (design-research review, owner decisions): solid tinted status pills instead of gradients (§4, §7); a near-neutral canvas behind dense record screens, chosen once by the shell (§3.2); separate app and marketing radius scales (§3); "Ask on WhatsApp" beside "Talk to us" (`SOS_PUBLIC_WHATSAPP_NUMBER`) and the pricing page as a packaging explainer without prices (§5.6); consequence line in decision dialogs and numeric table columns (§4). 0.7: fixes from the review of the motion and marketing work: reveals use the Web Animations API with a root margin (never stuck hidden at high zoom, shown at once for keyboard focus), the phone menu closes when focus or a click leaves it, fading panels are `inert`, Motion features load after hydration, the marketing press feedback respects reduced motion and focus clears the sticky header; e2e for every public page. 0.6: §5.6 public marketing pages (layout, settings, reveal pattern, dedicated hosts); §5.5 motion (tokens shared with Motion, CSP-safe pattern, what moves and what never does, reduced motion), §3.1 type scale and weights for PP Mori, §2 rule 6, component states (Button loading, dialog exits). 0.5: §5.4 English first, Telugu hidden behind `SOS_TELUGU_ENABLED` (ADR-0036); §2 rules 2 and 8, §3 fonts. 0.4: §5.3 Ask chat patterns (anatomy, motion tokens, reduced motion, a11y), sidebar sub-lists (`sub`, `subActivePattern`). 0.3: §5.2 one sidebar (the icon rail and the separate list panel are gone): anatomy, compact mode, drawer, themes, a11y; §7 sidebar contrast pairs. 0.2: §5.1 responsive layout (spacing scale, menu drawer, TableScroll, dialogs, checks). 0.1: new document: tokens, fonts, primitives, shells, do/don't, contrast table (UI refresh foundation) |
| Requirements     | NFR-A11Y-001 (WCAG 2.2 AA), NFR-I18N-001 (English; Telugu hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036), SEC-010 (CSP, self-hosted assets)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Related          | 02-PRD §8 (UX principles), 13 §5 (TypeScript/Next.js standards), CLAUDE.md §10                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Code             | `apps/web/src/app/globals.css` (tokens), `apps/web/src/components/ui/` (primitives, exported from `index.ts`), `apps/web/src/components/shell/` (shells)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Living reference | `/[locale]/dev/ui`: every primitive and variant with synthetic content. Local development only (same guard as `/dev/sign-in`: `next dev` + local stub issuer; a 404 everywhere else)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

---

## 1. The look in one paragraph

Content sits on **white cards** (12px radius in the app, 1px hairline border, soft large shadow)
laid on a **very soft sky-blue to pale-aqua gradient canvas** on the dashboards and a
**near-neutral flat canvas** on dense record screens (§3.2). Text is PP Mori, a licensed neo-grotesk (Inter as fallback for ₹ and missing glyphs);
**big numbers** use a light serif (Instrument Serif); small **eyebrow labels** are mono uppercase
(JetBrains Mono). The primary button is **near-black**; brand blue is for links, focus and the one
brand call to action. Status uses **solid tinted pills** (soft tint, strong text, hairline, icon);
colour appears only where it means something.
Navigation is **one calm sidebar** (brand, school, grouped menu, signed-in account) that can be
made compact (icons only); every page starts with a **page-header card** (breadcrumb, title,
actions). The platform admin panel keeps a **dark violet** sidebar and top bar so operators always
know they are in the control plane.

## 2. Hard rules (never traded for looks)

1. **WCAG 2.2 AA.** Text 4.5:1 (3:1 for large text and UI boundaries). Every token pair a primitive
   uses is listed in §7 and recomputed by `components/ui/tokens.test.ts`; add a row there when you add
   a pair. Gradients are checked at their **lightest** stop. Focus is always visible (3px ring,
   `--color-focus`; yellow on platform chrome; white on the blue AI panel). Colour is never the only
   signal: pills carry text, rings carry a number, the switch knob moves.
2. **Telugu (only while it is switched on, §5.4).** Noto Sans Telugu is then the fallback of the
   sans and mono stacks, `:lang(te)` keeps the taller line height, and the serif display and mono
   eyebrow switch back to the sans with normal spacing in Telugu (`.font-display:lang(te)`,
   `.eyebrow:lang(te)`). Never set tight line heights or letter-spacing on text that may be
   Telugu: data (a name copied from a register) can still be in Telugu script.
3. **CSP (SEC-010).** No `style` attributes, no external resources. Colours come from classes;
   SVG geometry uses presentation attributes (`strokeDashoffset`, `points`), which CSP allows.
   Fonts are self-hosted (`@fontsource` packages and the licensed PP Mori WOFF2 files; `font-src 'self'`).
4. **Baseline Widely Available** only (CLAUDE.md §10). Used: CSS gradients, `:has()` (segmented
   control, drawer scroll lock), `:modal`, native `<dialog>`, `<details>`, `accent-color`,
   `env()`, `dvh` (after a `vh` fallback), `@media (prefers-reduced-motion)`,
   `@media (forced-colors)`, `:where()` (the `collapsed:` variant), `inert` (via the modal
   dialog). Not used: `backdrop-filter` (slow office PCs), anchor positioning,
   container queries, `appearance: base-select`.
5. **Print.** Gradients, shadows and tints disappear; cards print as plain bordered boxes; chrome
   (`data-print="hide"`) is hidden; A4 margins; Telugu line height 1.8.
6. **Motion.** Few, short motions on transform and opacity only, from the shared tokens (§5.5):
   press feedback, dialog and drawer enter/exit, the notification panel, live alerts, a changed
   KPI number, content replacing a skeleton, the skeleton shimmer and the Ask chat (§5.3).
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

Use them as Tailwind utilities (`bg-surface`, `text-ink-muted`, `border-border`, `rounded-xl`,
`shadow-card`, `font-display`). Old names still work (`primary`, `border-strong`, `canvas`, …).

| Group    | Tokens                                                                                                                                                                                                                                                                                                                 | Use                                                       |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Canvas   | `canvas` (flat), `canvas-from`, `canvas-via`, `canvas-to`, `canvas-neutral`                                                                                                                                                                                                                                            | Body gradient (set globally; do not repeat it); `canvas-neutral` behind dense record screens (§3.2) |
| Surfaces | `surface` (white), `surface-muted` (filled inputs, table header), `surface-sunken` (segmented track), `surface-hover` (row hover)                                                                                                                                                                                      | Cards and controls                                        |
| Borders  | `border` (hairline, decorative), `border-soft` (secondary buttons), `border-control` (inputs, switch: 3:1), `border-strong` (legacy)                                                                                                                                                                                   |                                                           |
| Ink      | `ink`, `ink-muted`, `ink-subtle`                                                                                                                                                                                                                                                                                       | Body, secondary, "1,157 last month" lines                 |
| Action   | `action`, `action-hover`, `on-action`                                                                                                                                                                                                                                                                                  | Near-black primary button, dark pills                     |
| Brand    | `primary` (links, focus, active nav), `primary-soft`, `brand`, `brand-strong`                                                                                                                                                                                                                                          |                                                           |
| Status   | `info-*`, `success`, `success-*`, `positive-*`, `warning-*`, `danger*`, `violet-*`, `teal`, `teal-*`                                                                                                                                                                                                                   | Alerts, chips, badges                                     |
| Pills    | the status tokens: `info-soft`/`info-ink`, `violet-soft`/`violet-ink`, `teal-soft`/`teal-ink`                                                                                                                                                                                                                          | Solid tinted status pills (no gradients, no pill tokens)  |
| AI       | `ai-from`, `ai-to`                                                                                                                                                                                                                                                                                                     | `.ai-gradient` panel                                      |
| Charts   | `chart-1` blue, `chart-2` amber, `chart-3` teal, `chart-4` violet, `chart-track`                                                                                                                                                                                                                                       | Series colours (3:1 on white)                             |
| Platform | `platform`, `platform-hover`, `platform-ink`, `platform-muted`, `platform-accent`, `platform-accent-ink`, `platform-soft`                                                                                                                                                                                              | Control-plane chrome only                                 |
| Radii    | App: `xs` 4, `sm` 6, `md` 8 (buttons, inputs, selects), `lg` 10 (alerts, segmented track), `xl` 12 (cards, dialogs, panels, drawer), `2xl` 14 (large panels), `3xl` 16, `full`. Marketing (`.mk`): `sm` 8, `md` 10, `lg` 14, `xl` 20, `2xl` 24, `3xl` 32                                                                                  | Same `rounded-*` class names; the scope picks the scale   |
| Shadows  | `shadow-card`, `shadow-raised` (buttons, selected segment), `shadow-popover` (dialogs, menus)                                                                                                                                                                                                                          |                                                           |
| Fonts    | `font-sans` (PP Mori, self-hosted WOFF2 in `src/app/fonts/pp-mori/`, commercial web licence (see `LICENSE-NOTE.md`); falls back to Inter for ₹ and missing glyphs, → Noto Sans Telugu while Telugu is on), `font-display` / `font-serif` (Instrument Serif), `font-mono` (JetBrains Mono; → Noto Sans Telugu while on) |                                                           |

Component classes (in `@layer components`): `.eyebrow`, `.select-chevron`, `.skeleton`,
`.canvas-neutral` (§3.2), `.ai-gradient`, `.ai-chrome` (white focus ring),
`.quote-gradient`, `.platform-chrome` (yellow focus ring), `.pressable` (press feedback, §5.5).
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
| `2xl`  | 24 / 32            | page title (`PageHeader` `h1`, `font-semibold`)      |
| `4xl`  | 36 / 40            | `StatCard` number (serif)                            |
| `5xl`  | 48 / 48            | `KpiCard` number (serif)                             |

Weights: PP Mori has 200/400/600 only, so **emphasis is `font-semibold`** (`font-medium`
renders as 400; do not use it for emphasis). Numbers: tables, KPI and stat cards and the
unread badge set `tabular-nums` (PP Mori itself has no tabular figures; the serif display,
mono and Inter fallback do).

UI text: PP Mori (Pangram Pangram, commercial web licence; `src/app/fonts/pp-mori/`, `@font-face` in `globals.css`; weights 200/400/600, so `font-medium` renders as 400; no tabular figures). Open fonts (all OFL-1.1, pinned exact in `apps/web/package.json`): `@fontsource-variable/inter`
(variable weights), `@fontsource/instrument-serif` (400), `@fontsource/jetbrains-mono` (500),
`@fontsource/noto-sans-telugu` (400/600/700, Telugu subset). Every file declares
`unicode-range`, so a page downloads only the scripts it shows. Noto Sans Telugu is **not**
bundled: `globals.css` names no Telugu font, and the locale layout links
`/fonts/telugu/noto-sans-telugu.css` (faces plus the two stacks above) only while Telugu is on;
that route answers 404 while it is off (§5.4).

### 3.2 Canvas: gradient or near-neutral

The gradient canvas is for places where people orient themselves: the **school home
dashboard**, the **platform dashboard** and the **public marketing pages**. **Dense record
screens** get the flat `canvas-neutral` (#f4f6f9), so the only colour on the page is colour
that means something (a status pill, an error):

- school: students (list and profile), findings, change requests, imports, register photos,
  exports, documents, audit and settings (every page under these paths);
- platform: every page except the dashboard (`/platform`).

The rule lives in one place, `components/shell/canvas.ts` (`canvasFor(theme, path)`,
`NEUTRAL_CANVAS_PATHS`); `AppShell` applies it to its root (`data-canvas`, class
`.canvas-neutral`, which covers the body gradient). A page that needs the other canvas passes
`canvas="neutral" | "gradient"` to the shell; never paint a background per screen. A new dense
record screen is added to `NEUTRAL_CANVAS_PATHS`. Text pairs on the neutral canvas are in §7;
print is white either way.

### 3.3 Radii: two scales, one set of class names

The signed-in app uses the calm scale in
`@theme` (cards, dialogs and panels 10-14px; buttons and inputs a step smaller, 8px). The
public marketing pages keep their larger, softer corners: `.mk` (the `MarketingShell` root,
`features/marketing/marketing.css`) redefines `--radius-sm` … `--radius-2xl`, and Tailwind's
`rounded-*` utilities read those variables, so a component needs no marketing variant.
Write `rounded-xl` for a card in both places; never hard-code a pixel radius.
`tokens.test.ts` pins both ranges.

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
| `PageHeader`                                                                      | `title` (the page's one `h1`), `description`, `actions`, `badge`, `breadcrumb` (`Crumb[]`), `eyebrow`, `plain`                                                                                                                                 | Card by default; `plain` inside other cards                                                                                                                                                                                                                                                                               |
| `Breadcrumb`                                                                      | `items: {label, href?}[]`, `label`                                                                                                                                                                                                             | Last crumb is `aria-current="page"`                                                                                                                                                                                                                                                                                       |
| `Eyebrow`                                                                         | `as` `p`·`span`·`div`·`h2`·`h3`, `tone` `muted`·`ink`·`brand`·`inverse`, `id`                                                                                                                                                                  | Mono uppercase label                                                                                                                                                                                                                                                                                                      |
| `Badge`                                                                           | `tone`: `neutral` · `info` · `success` · `warning` · `danger` · `platform` · `violet` · `teal`                                                                                                                                                 | Soft status label                                                                                                                                                                                                                                                                                                         |
| `Pill`                                                                            | `variant`: `progress` · `review` · `done` (solid tints: blue, violet, teal) · `dark` · `date` · `tag` · `sample` · `positive` · `negative` · `command`; `size` `sm`·`md`; `icon` (name, or `null`)                                              | Always with text. Status variants: soft tint, strong text, hairline and a decorative icon (clock, eye, check; `icon={null}` removes it), so meaning never rests on colour |
| `DeltaPill`                                                                       | `value` ("+2.7%"), `direction` `up`·`down`·`flat`, `label` (read instead of the value)                                                                                                                                                         | Dark pill with arrow                                                                                                                                                                                                                                                                                                      |
| `StatCard`                                                                        | `label`, `value` (string or null), `unavailableLabel`, `hint`                                                                                                                                                                                  | Inside a `<dl>`; serif number                                                                                                                                                                                                                                                                                             |
| `KpiCard`                                                                         | `label`, `value`, `unavailableLabel`, `delta` (`{value, direction?, label?}`), `comparison`, `sparkline`, `aside`                                                                                                                              | `role="group"` named by the label; serif number; never invent numbers                                                                                                                                                                                                                                                     |
| `Sparkline`                                                                       | `values: number[]`, `label` (text alternative, required), `color` 1–4, `area`                                                                                                                                                                  | Inline SVG, `aria-hidden` + sr-only label                                                                                                                                                                                                                                                                                 |
| `ProgressRing`                                                                    | `value` 0–100, `label` (required), `size` `sm`·`md`·`lg`, `color` 1–4, `showValue`                                                                                                                                                             | `role="progressbar"` with `aria-valuetext`                                                                                                                                                                                                                                                                                |
| `Input`, `Textarea`, `TextField`, `TextAreaField`, `Field`, `controlClasses`      | unchanged                                                                                                                                                                                                                                      | Filled look (soft grey, 3:1 boundary)                                                                                                                                                                                                                                                                                     |
| `SearchInput`                                                                     | `label` (required), `labelVisible`, `icon`, `wrapperClassName`, input props                                                                                                                                                                    | `type="search"`; put it in `<form role="search">` with a "Filter" button                                                                                                                                                                                                                                                  |
| `Select`, `SelectField`                                                           | unchanged                                                                                                                                                                                                                                      | Native `<select>` with CSS chevron                                                                                                                                                                                                                                                                                        |
| `Toggle` (client)                                                                 | `label`, `description`, `checked`/`defaultChecked`, `onCheckedChange`, `disabled`, `name` (hidden input "on"/"off"), `labelFirst`                                                                                                              | `<button role="switch" aria-checked>`; green when on. For settings that apply at once                                                                                                                                                                                                                                     |
| `SegmentedControl` (client)                                                       | `legend` (required), `legendVisible`, `options` `{value,label,disabled?}[]`, `value`/`defaultValue`, `onValueChange`, `name`, `size` `sm`·`md`, `block`                                                                                        | Fieldset of native radios: arrows, form value, announcements for free                                                                                                                                                                                                                                                     |
| `Tabs` (client)                                                                   | `label`, `items`, `defaultTabId`, `variant` `segmented` (default) · `underline`                                                                                                                                                                | WAI-ARIA tabs; panels stay in the DOM                                                                                                                                                                                                                                                                                     |
| `TabNav`                                                                          | `label`, `items`, `activeId`, `variant`                                                                                                                                                                                                        | Tabs that are links (`aria-current`)                                                                                                                                                                                                                                                                                      |
| `Table`, `THead`, `TBody`, `Tr`, `Th`, `Td`                                       | `Table` `density` `comfortable` (≈56px rows) · `compact`; `stickyFirstColumn` (below md); `Th`/`Td` `numeric`                                                                                                                                  | Light header, dividers, hover. `numeric` (counts, amounts): right-aligned, tabular figures, no wrap; set it on the header and the cells                                                                                                                                                                                  |
| `TableScroll`                                                                     | `label` (required: names the region), `framed` (card frame)                                                                                                                                                                                    | Wrap every hand-built `Table` in it: the table scrolls sideways inside a focusable region, never the page                                                                                                                                                                                                                 |
| `DataTable`                                                                       | unchanged + `density`, `stickyFirstColumn`; column `numeric`                                                                                                                                                                                  | Loading, error, empty states; renders `TableScroll framed`                                                                                                                                                                                                                                                                |
| `EmptyState`                                                                      | `title`, `body`, `action`, `icon`                                                                                                                                                                                                              | Say what to do next                                                                                                                                                                                                                                                                                                       |
| `LoadingState`, `Skeleton`                                                        | `label`, `rows`, `variant` `rows`·`cards`                                                                                                                                                                                                      | `role="status"`; shimmer off under reduced motion                                                                                                                                                                                                                                                                         |
| `Alert`                                                                           | unchanged (`tone`, `title`, `live`)                                                                                                                                                                                                            |                                                                                                                                                                                                                                                                                                                           |
| `Dialog` (client), `ActionDialog` (client)                                        | unchanged; `ActionDialog` `consequence`                                                                                                                                                                                                        | Native `<dialog>`, restyled. `consequence`: one plain sentence of what confirming changes ("Nothing on the student's record changes."), shown just above the buttons and read as the confirm button's description; use it on consequential actions |
| `Avatar`                                                                          | `name`, `size` `sm`·`md`·`lg`, `decorative`                                                                                                                                                                                                    | Initials only, never photos                                                                                                                                                                                                                                                                                               |
| `AvatarStack`                                                                     | `names`, `label` (required), `max`, `size`                                                                                                                                                                                                     | One image named by `label`, "+N"                                                                                                                                                                                                                                                                                          |
| `Timeline`                                                                        | `items` `{id, title, time?, body?, chips?, status? done·current·pending, statusLabel?}[]`, `label`                                                                                                                                             | Ordered list, mono times                                                                                                                                                                                                                                                                                                  |
| `AiPanel`                                                                         | `greeting` (its heading), `eyebrow`, `description`, `suggestions` `{id,label,href?                                                                                                                                                             | onSelect?}[]`, `suggestionsLabel`, children (question form)                                                                                                                                                                                                                                                               | For the Ask screens. Answers go in normal cards **with source chips** (invariant 8) |
| `QuoteBlock`                                                                      | `label`, children                                                                                                                                                                                                                              | Pale green quote                                                                                                                                                                                                                                                                                                          |
| `SidebarNav` (client)                                                             | `label`, `items` **or** `sections` (`NavSection[]`: `{id, label, items}`; items `{href, label, icon?, exact?, nested?, activePattern?, sub?, subActivePattern?}`), `theme` `school`·`platform`, `id`                                           | One current page (`aria-current="page"` + accent bar); group headings name their lists; `collapsed:` compact styles. Used by the shell's `Sidebar`. `sub`: a client sub-list under an item (Ask: New chat, recents, All chats, Memory), hidden when compact; `subActivePattern`: paths whose current link is inside `sub` |
| `UsageMeter`, `LanguageSwitcher`, `Value`, `Label`, `SecretOnce`, `ApiErrorAlert` | unchanged                                                                                                                                                                                                                                      |                                                                                                                                                                                                                                                                                                                           |

Shells (`@/components/shell`): `SchoolShell` (`permissions`, `features`, `schoolName`,
`canSwitchSchool`, `account`, `topbarActions`, `languages`, `banner`) and `PlatformShell`
(`permissions`, `account`) render `AppShell` (client): skip link, **one `Sidebar`** (beside the
page from lg, in the menu drawer below lg), a top bar (bell, language; below lg also the "Menu"
button and wordmark) and `<main id="main">`. See §5.2. `MinimalShell` (`wide` for tables) and
`Wordmark` serve pages outside a school; `BrandMark` is the round "S" both use.

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
- Status: `Badge` for record states (active/suspended), `Pill` status tints for workflow states
  (in progress/review/done), `Pill positive/negative` for matches/mismatches.
- Numbers: counts and amounts in tables are `numeric` columns (right-aligned, tabular figures).
- Decisions: a dialog whose confirm button changes a record (or deliberately does not) says so
  in `consequence`, in plain words, just above the buttons.

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
- **Dialogs** are `w-[min(<n>rem,calc(100vw-2rem))]` and `m-auto`; padding tightens below sm
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
| Brand      | Round "S" (`BrandMark`) + "SchoolOS", a link home; the collapse button at the right                                                                  | Same, yellow mark, link to `/platform`      |
| Context    | "Current school" + the school's name (GET `/me/schools`); "Switch school" for people in several schools                                              | The yellow "Platform admin" badge           |
| Navigation | `<nav aria-label="Main">`, permission- and feature-filtered groups under small headings; each item is icon + label in a 40px row; scrolls on its own | `<nav aria-label="Platform">`, same pattern |
| Account    | `<section aria-label="Your account">`: initials, name (heard as "Signed in as …"), role (system roles by name), "Lock now"                           | Same with "Sign out"                        |

The **top bar** holds page-wide tools only: the notification bell and the language switch (and,
below lg, the "Menu" button and wordmark). Page titles and breadcrumbs stay in each page's
`PageHeader`. Nothing is shown twice: "Switch school", the signed-in name and "Lock now" moved
from the top bar into the sidebar; the idle-timeout warning is mounted once by the shell
(`AppShell` `session`), never inside the sidebar.

**States.** Item: muted text and icon; hover: filled row (`surface-muted`, platform
`platform-hover`) with full-contrast text; keyboard focus: the 3px focus ring (blue; yellow on
the platform chrome); current page: `aria-current="page"`, tinted row, bold text and a 3px
accent bar at the start edge (blue `primary`; platform yellow `platform-accent`), so the state
never relies on colour alone. Exactly one item is current (the most specific match;
`activePattern` for sub-pages).

**Breakpoints.**

- **lg (1024px) and up:** sticky, full-height sidebar (`h-viewport`), 272px wide (compact 72px);
  the menu scrolls inside it (`overflow-y: auto`, `overscroll-behavior: contain`) while the
  header and the account stay put. At 1366×768 the page keeps 1094px minus gutters.
- **Below lg:** the "Menu" button (44px) opens the same sidebar in the **modal `<dialog>`
  drawer** (`.drawer`, 20rem or the screen minus 48px): focus moves to its close button and
  cannot leave it (the page behind is inert and does not scroll), Escape, the close button, a
  tap on the dimmed page, following a link or widening the window to lg close it, and focus
  returns to the button. It renders its content only while open, so there is one "Main"
  landmark. It is never compact.

**Compact mode.** The collapse button ("Collapse menu" / "Expand menu") switches the wide
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

**Themes.** School: white sidebar with a hairline on the canvas, top bar without a card.
Platform: dark violet sidebar and top bar (`platform-chrome`: yellow focus ring), yellow mark,
badge and active bar. Pairs in §7.

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
Escape and return focus. Colours reuse §7 pairs (`ink-muted` on white 7.56:1, `violet-ink` on
`violet-soft` 7.96:1, `primary` on `primary-soft` 5.82:1, `on-action` on `action` 17.74:1).
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

**The switch.** `SOS_TELUGU_ENABLED` (default `false`), the same variable as the API's. The web app
reads it **only** in `src/i18n/languages.ts` (`teluguEnabled()`, `enabledLocales()`,
`uiLocale()`), at run time on the server: the proxy, layouts, pages, route handlers and the BFF.
It is never inlined into a browser bundle; one image serves both settings. Client components ask
`useTeluguEnabled()` / `useEnabledLocales()` from `src/i18n/LanguagesProvider.tsx`, which the
locale layout fills; without a provider only English is on. `app/client-boundary.test.ts` fails if
a client module imports the server reader or code reads the variable anywhere else.

**With the switch off:**

- **Routing.** `/te/...` answers 307 to the same `/en/...` page (query kept; temporary, because
  Telugu may return). Only English is negotiated: `Accept-Language: te` and a stored
  `NEXT_LOCALE=te` cookie are ignored, the `Link` alternates name English only, and the locale
  layout 404s a `te` param that slipped past the proxy. The BFF asks the API for English only.
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
te, `/te` links, `*_te` fields or a language switch; each feature's tests pin its forms and
bodies. e2e: the default project runs with the switch off (`e2e/english-only.spec.ts`: redirects,
a Telugu browser, the font 404, school and platform pages); `chromium-telugu` runs a second
server from the same build with the switch on and re-runs the tests tagged `@telugu`.

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

**Deliberately not animated** (the skills' frequency and keyboard gates): sidebar navigation,
active item and compact switch; tabs and segmented controls (arrow keys move the selection,
so a sliding indicator would animate keyboard input); `<details>` (often opened from the
keyboard; no Baseline height animation); table rows, sorting and keyboard movement in grids;
page and route loads; the Escape close of the notification panel. No toasts: results stay
inline next to the form as `Alert live` (Sonner was considered and rejected: it injects a
`<style>` element at run time, which the nonce CSP without `'unsafe-inline'` blocks, and
inline results suit office users better).

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

**Type.** PP Mori extralight (200) for the page `h1` at 40px and up only (thin strokes need a
large size; ink on the canvas stays 14:1), regular (400) for section `h2`, semibold for `h3`;
tight tracking on display sizes (`.mk-display`, `.mk-title`; normal spacing under `:lang(te)`).
Instrument Serif for the one accent phrase in the home headline and step numbers; JetBrains Mono
for eyebrows. Dark bands (`.mk-night`, #0b1220) use white (18.9:1) and `--mk-night-muted`
#c3cbd8 (10.9:1 on #111827) with a white focus ring.

**Calls to action (owner decisions).** "Talk to us" is a plain `mailto:` link, shown only when
`SOS_PUBLIC_CONTACT_EMAIL` is set; there is no form and no lead data. "Sign in" goes to
`/bff/auth/login`. Without an address "Sign in" becomes the primary button and the home hero
offers "See how it works". **"Ask on WhatsApp"** (`WhatsAppLink`) is a secondary button beside
"Talk to us" (after "Sign in" when there is no address), shown only when
`SOS_PUBLIC_WHATSAPP_NUMBER` is set: in the header bar from xl (1280px; below that in the
phone menu), the hero, both plan cards on /pricing and the closing bands. It is a plain link
to `https://wa.me/<digits>?text=<encoded>` with the fixed greeting "Hello, I'd like to see
SchoolOS for our school." (`marketing.cta.whatsappMessage`; never student or other personal
data), `rel="noopener noreferrer"`, same tab, and an accessible name that starts with the
visible label and adds "(opens WhatsApp)" (WCAG 2.5.3). Unset or invalid: nothing is shown, no
placeholder number. wa.me is the only address outside the site a public page may link to.

**Settings** (read on the server at request time by `features/marketing/settings.ts`, never in a
browser bundle; unset or invalid means that part is not shown, no placeholder anywhere):

| Variable                     | Default | Use                                                                                                                                           |
| ---------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `SOS_PUBLIC_CONTACT_EMAIL`   | unset   | "Talk to us" mailto in the header, hero, plans, closing band, footer and About. Must be a plain address (no `?`, `,` or spaces), else ignored |
| `SOS_PUBLIC_COMPANY_NAME`    | unset   | Footer "© {company}" line and the About contact block (≤ 200 characters)                                                                      |
| `SOS_PUBLIC_COMPANY_ADDRESS` | unset   | About contact block; lines split on `\|` or `\n`                                                                                              |
| `SOS_PUBLIC_WHATSAPP_NUMBER` | unset   | "Ask on WhatsApp" (wa.me link). An international number of 8-15 ASCII digits with an optional leading `+`, first digit 1-9 (e.g. `+91XXXXXXXXXX`); spaces, dashes or anything else mean unset |

**Dedicated hosts.** With `SOS_DEPLOYMENT_MODE=dedicated` (`platformEnabled()`) `/features`,
`/security`, `/pricing` and `/about` answer 404 and `/welcome` is a branded sign-in card only: a
dedicated host is one school's own address, not a sales site.

**Illustrations.** HTML, CSS and inline SVG only (`mockups.tsx`): crisp at any DPI, no image
requests, no style attributes. Each carries a "Sample data" tag, uses made-up values ("Sample
student A"), sits in a `<figure>` whose picture is `aria-hidden` and whose `figcaption` (sr-only)
describes it, and keeps AA contrast like real UI. Mockups and the home tile vignettes are
cropped on purpose (truncated lines, a fixed-height tile window): the responsive e2e skips
its clipped-text check inside `aria-hidden` content only; page overflow and screen edges are
still checked.

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

## 6. Do and don't

| Do                                                                                      | Don't                                                                                                         |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Use the near-black `primary` for the main action of a card and `secondary` for the rest | Put two `brand` (blue) buttons on one screen                                                                  |
| Use `KpiCard` only for numbers the API returns; show "—" (`value={null}`) when missing  | Invent or estimate statistics, even on the welcome page                                                       |
| Give every icon-only control a `label`; give charts a text alternative                  | Rely on colour alone (always text in pills, a number in rings)                                                |
| Use `SegmentedControl` for a small choice, `Tabs` for panels, `TabNav` for pages        | Build tab-like controls from `div`s                                                                           |
| Keep Telugu in the sans (automatic with `:lang(te)`)                                    | Apply `font-display`, `tracking-*` or `leading-none` to text that may be Telugu without the `:lang(te)` guard |
| Pick a variant/size/tone prop                                                           | Override colours or padding with `className` (§2.7)                                                           |
| Use Avatar initials                                                                     | Show photos of children or staff                                                                              |
| Keep print in mind: chrome gets `data-print="hide"`                                     | Hide information only in print                                                                                |

## 7. Contrast table (recomputed by `tokens.test.ts`)

| Foreground                    | Background                 | Ratio   | Needs | Used for                                                  |
| ----------------------------- | -------------------------- | ------- | ----- | --------------------------------------------------------- |
| `ink` #111827                 | `surface` #ffffff          | 17.74:1 | 4.5:1 | body text on cards                                        |
| `ink` #111827                 | `canvas-from` #dbe9fa      | 14.40:1 | 4.5:1 | text on the canvas                                        |
| `ink-muted` #4b5563           | `surface` #ffffff          | 7.56:1  | 4.5:1 | secondary text on cards                                   |
| `ink-muted` #4b5563           | `canvas-from` #dbe9fa      | 6.14:1  | 4.5:1 | secondary text on the canvas                              |
| `ink-muted` #4b5563           | `canvas-to` #dcf2f0        | 6.48:1  | 4.5:1 | secondary text on the canvas                              |
| `ink-muted` #4b5563           | `surface-muted` #f3f5f8    | 6.92:1  | 4.5:1 | table header, filled input                                |
| `ink-muted` #4b5563           | `surface-sunken` #eef1f5   | 6.67:1  | 4.5:1 | unselected segment                                        |
| `ink-subtle` #5b6576          | `surface` #ffffff          | 5.89:1  | 4.5:1 | comparison line on KPI cards                              |
| `ink-subtle` #5b6576          | `canvas-from` #dbe9fa      | 4.78:1  | 4.5:1 | notes on the canvas                                       |
| `ink-subtle` #5b6576          | `canvas-to` #dcf2f0        | 5.05:1  | 4.5:1 | notes on the canvas                                       |
| `ink-subtle` #5b6576          | `surface-muted` #f3f5f8    | 5.39:1  | 4.5:1 | placeholder in filled inputs                              |
| `ink` #111827                 | `surface-muted` #f3f5f8    | 16.24:1 | 4.5:1 | typed text in filled inputs                               |
| `primary` #1d4ed8             | `surface` #ffffff          | 6.70:1  | 4.5:1 | links on cards                                            |
| `primary` #1d4ed8             | `canvas-from` #dbe9fa      | 5.44:1  | 4.5:1 | links on the canvas                                       |
| `primary` #1d4ed8             | `primary-soft` #e8efff     | 5.82:1  | 4.5:1 | active nav item                                           |
| `primary` #1d4ed8             | `info-soft` #eff4ff        | 6.08:1  | 4.5:1 | date chip                                                 |
| `on-primary` #ffffff          | `primary` #1d4ed8          | 6.70:1  | 4.5:1 | legacy brand fill                                         |
| `white` #ffffff               | `brand` #2563eb            | 5.17:1  | 4.5:1 | brand button                                              |
| `white` #ffffff               | `brand-strong` #1e40af     | 8.72:1  | 4.5:1 | brand button hover                                        |
| `on-action` #ffffff           | `action` #111827           | 17.74:1 | 4.5:1 | primary (near-black) button, delta pill                   |
| `on-action` #ffffff           | `action-hover` #1f2937     | 14.68:1 | 4.5:1 | primary button hover                                      |
| `white` #ffffff               | `danger` #b42318           | 6.57:1  | 4.5:1 | danger button                                             |
| `danger` #b42318              | `danger-soft` #fef3f2      | 6.05:1  | 4.5:1 | danger alert, negative chip                               |
| `danger` #b42318              | `surface` #ffffff          | 6.57:1  | 4.5:1 | error text                                                |
| `warning-ink` #7a2e0e         | `warning-soft` #fffaeb     | 9.05:1  | 4.5:1 | warning alert                                             |
| `success-ink` #05603a         | `success-soft` #ecfdf3     | 7.26:1  | 4.5:1 | success alert                                             |
| `positive-ink` #166534        | `positive-soft` #f0fdf4    | 6.81:1  | 4.5:1 | positive chip                                             |
| `info-ink` #1e40af            | `info-soft` #eff4ff        | 7.91:1  | 4.5:1 | info alert                                                |
| `violet-ink` #5b21b6          | `violet-soft` #f3efff      | 7.96:1  | 4.5:1 | violet chip                                               |
| `teal-ink` #115e59            | `teal-soft` #e6f7f5        | 6.86:1  | 4.5:1 | teal chip                                                 |
| `info-ink` #1e40af            | `info-soft` #eff4ff        | 7.91:1  | 4.5:1 | In progress pill                                          |
| `violet-ink` #5b21b6          | `violet-soft` #f3efff      | 7.96:1  | 4.5:1 | Review pill                                               |
| `teal-ink` #115e59            | `teal-soft` #e6f7f5        | 6.86:1  | 4.5:1 | Done pill                                                 |
| `positive-ink` #166534        | `positive-soft` #f0fdf4    | 6.81:1  | 4.5:1 | Positive pill                                             |
| `danger` #b42318              | `danger-soft` #fef3f2      | 6.05:1  | 4.5:1 | Negative pill                                             |
| `primary` #1d4ed8             | `info-soft` #eff4ff        | 6.08:1  | 4.5:1 | Date pill                                                 |
| `ink-muted` #4b5563           | `surface-muted` #f3f5f8    | 6.92:1  | 4.5:1 | Tag pill                                                  |
| `ink-muted` #4b5563           | `surface` #ffffff          | 7.56:1  | 4.5:1 | Sample pill                                               |
| `ink` #111827                 | `canvas-neutral` #f4f6f9   | 16.39:1 | 4.5:1 | text on the neutral canvas                                |
| `ink-muted` #4b5563           | `canvas-neutral` #f4f6f9   | 6.98:1  | 4.5:1 | secondary text on the neutral canvas                      |
| `ink-subtle` #5b6576          | `canvas-neutral` #f4f6f9   | 5.44:1  | 4.5:1 | notes on the neutral canvas                               |
| `primary` #1d4ed8             | `canvas-neutral` #f4f6f9   | 6.19:1  | 4.5:1 | links and breadcrumbs on the neutral canvas               |
| `focus` #1d4ed8               | `canvas-neutral` #f4f6f9   | 6.19:1  | 3:1   | focus ring on the neutral canvas                          |
| `white` #ffffff               | `ai-from` #1e3a8a          | 10.36:1 | 4.5:1 | AI panel text (dark stop)                                 |
| `white` #ffffff               | `ai-to` #2563eb            | 5.17:1  | 4.5:1 | AI panel text (light stop)                                |
| `platform-ink` #ffffff        | `platform` #3b0764         | 15.00:1 | 4.5:1 | platform sidebar and top bar text                         |
| `platform-ink` #ffffff        | `platform-hover` #581c87   | 10.88:1 | 4.5:1 | platform sidebar hover and active row                     |
| `platform-muted` #e9d5ff      | `platform` #3b0764         | 11.02:1 | 4.5:1 | platform sidebar items, headings, role line               |
| `platform-accent` #fcd34d     | `platform` #3b0764         | 10.40:1 | 3:1   | platform focus ring and active marker                     |
| `platform-accent-ink` #3b0764 | `platform-accent` #fcd34d  | 10.40:1 | 4.5:1 | platform badge                                            |
| `platform` #3b0764            | `platform-soft` #f5f0ff    | 13.42:1 | 4.5:1 | active platform nav item                                  |
| `primary` #1d4ed8             | `primary-soft` #e8efff     | 5.82:1  | 3:1   | sidebar active bar on the active row                      |
| `ink-subtle` #5b6576          | `surface-muted` #f3f5f8    | 5.39:1  | 4.5:1 | "Current school" label                                    |
| `primary` #1d4ed8             | `surface-muted` #f3f5f8    | 6.14:1  | 4.5:1 | "Switch school" link                                      |
| `platform-accent` #fcd34d     | `platform-hover` #581c87   | 7.54:1  | 3:1   | platform sidebar active bar, focus ring on the active row |
| `on-action` #ffffff           | `action` #111827           | 17.74:1 | 4.5:1 | compact sidebar label (tooltip)                           |
| `border-control` #7b8494      | `surface` #ffffff          | 3.77:1  | 3:1   | input and switch boundary on cards                        |
| `border-control` #7b8494      | `surface-muted` #f3f5f8    | 3.45:1  | 3:1   | input boundary against its fill                           |
| `border-strong` #6b7280       | `surface` #ffffff          | 4.83:1  | 3:1   | legacy control boundary                                   |
| `focus` #1d4ed8               | `surface` #ffffff          | 6.70:1  | 3:1   | focus ring on cards                                       |
| `focus` #1d4ed8               | `canvas-from` #dbe9fa      | 5.44:1  | 3:1   | focus ring on the canvas                                  |
| `success` #15803d             | `surface` #ffffff          | 5.02:1  | 3:1   | switch on, timeline check                                 |
| `chart-1` #2563eb             | `surface` #ffffff          | 5.17:1  | 3:1   | chart series 1                                            |
| `chart-2` #d97706             | `surface` #ffffff          | 3.19:1  | 3:1   | chart series 2 (amber)                                    |
| `chart-3` #0d9488             | `surface` #ffffff          | 3.74:1  | 3:1   | chart series 3 (teal)                                     |
| `chart-4` #7c3aed             | `surface` #ffffff          | 5.70:1  | 3:1   | chart series 4 (violet)                                   |

Secondary buttons and round icon buttons use the decorative `border-soft`: a text label or the
icon itself identifies the control, so WCAG 1.4.11 does not require a 3:1 boundary there. Inputs
and the switch have no text inside when empty, so they use `border-control` (3:1).

## 8. Open points

- Motion (§5.5), for the product owner: the owner asked for a sliding tab/segmented indicator,
  an animated accordion and toasts; they were left out on purpose (keyboard-driven, no Baseline
  height animation, Sonner blocked by the CSP). The e2e axe checks run without waiting for
  animations; a fade is at most 200ms, but a slow CI machine could catch one mid-fade.
- Signed-in feature screens still use `font-medium` in places, which PP Mori renders as 400;
  primitives were moved to `font-semibold`, screens adopt it in their own changes.

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
- Calmer app (0.8), for the product owner: between 1024 and 1279px the header bar has no room
  for "Ask on WhatsApp" beside the page links, so there it is only in the hero, plan cards and
  closing bands (the phone menu covers smaller screens); the neutral canvas covers the screens
  named in §3.2 only (Ask, notices, attendance and the other school pages keep the gradient
  until someone adds them to `NEUTRAL_CANVAS_PATHS`); the consequence line is used on the
  change-request and finding decisions so far; other dialogs adopt it in their own changes.
  The pricing page's fee factors (campuses) and the implementation fee are the owner's
  packaging decisions; docs/16 does not model campuses or a one-time fee yet.
- `Sparkline` and `ProgressRing` cover small inline charts only; a chart library decision (if
  full charts are needed) needs an ADR (licence, bundle size, CSP).

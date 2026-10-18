# Patterns (use the primitives; docs/17 §4-5 has the APIs)

## Shell
- Sidebar 240px expanded / 56px collapsed (icons with tooltips), grouped with sentence-case headings; rows 32px (44px on coarse pointers). Phone: drawer.
- Top bar 48px: breadcrumb trail, "Search or jump to… Ctrl K" trigger (icon button with a label on phones), notifications, help, user menu.
- Page header is a plain row: title, one-line description, one primary action, the rest under a "More" menu. No header card.
- Phone bottom bars only for teachers and principals; hidden while the on-screen keyboard is open; SaveBars and bulk bars dock above them; safe-area insets respected.

## Command palette (Ctrl/Cmd+K)
- Groups: Go to (exactly the filtered sidebar items), Actions (each with its permission), Students (server search, debounced), Help.
- Combobox in a modal dialog (APG pattern): `aria-activedescendant`, arrows move, Enter opens, Escape steps out a level then closes. Opens 150 ms, closes 100 ms, scale 0.98 to 1; nothing under reduced motion. Never stores personal data in "recent".

## Tables (DataTableV2)
- Comfortable rows by default, compact via the density toggle; sticky header with scroll-padding so focused rows are never hidden under it.
- Toolbar: search, filter chips (removable, plus "Clear"), column chooser, density, export where a permission allows it.
- Selection covers the visible page only and clears on sort, page or filter change; a bulk bar appears only where a bulk action already exists.
- States: first use (invitation + action), no results (what was searched, how to widen it), no permission (who to ask), error (retry), offline, loading (skeleton rows after ~1 s).
- Numbers right-aligned in the figures font; never truncate money, dates, IDs or receipt numbers; middle-truncate long file names; wrap names.
- Phone: stacked cards with the same fields; a "Select" checkbox per card when selection exists. A row's main link may stretch over the row; other in-row controls need `relative z-[1]`.
- Server pagination with the page in the URL.

## Forms
- Labels above fields; mark optional fields, not required ones; hint text under the label; DD/MM/YYYY dates.
- Validate on blur (`:user-invalid` styling) and on submit; on submit show the ErrorSummary at the top (links to each field) plus the inline error; the summary takes focus.
- Explicit save for official records and settings (SaveBar with unsaved state and Discard); autosave only for drafts.
- Multi-step flows use Steps (with a failed state that says "Stopped here").
- Idempotency-Key on every create that could be retried; If-Match on updates; step-up and maker-checker dialogs keep their consequence lines.

## Feedback and loading
- Under ~1 s: show nothing. 1-10 s: skeleton (known layout) or inline spinner on the control. Over 10 s: progress with a count or estimate.
- Toasts are a CSS-only `role="status"` region for confirmations ("Receipt 0412 saved"); errors stay on the form next to the field.
- Optimistic UI only for low-stakes toggles (mark notification read), never for records, receipts or approvals.
- Error copy: what happened, then what to do. No error codes alone, no stack traces, never reveal another school's existence.

## Dashboards ("Today")
- A status strip plus at most four KPI cards, each linking to the screen that explains it; real numbers only; neutral canvas.
- "Waiting for you" list with one action per item. Hero numbers in Instrument Serif only at page level (HeroFigure).

## Fee counter (keyboard flow)
Student combobox → dues → payment mode (segmented, keys C/Q/B/U) → Enter saves once (idempotent) → Print gets focus. en-IN grouping, figures font, shortcut hints always visible, ErrorSummary on failure, never lose the typed amount.

## Teacher flows on a phone
- Attendance: "Mark everyone present", tap the absentees (44px targets), running present/absent count, sticky SaveBar "Save attendance".
- Marks: grid with arrow/Enter navigation on desktop, one student per screen on phones, same save call.

---
name: schoolos-design
description: SchoolOS design and UI-review playbook for the web app (school console for every role incl. teachers, operator console, parent and verification pages, marketing site). Use before building or changing any screen, component, illustration or marketing page in apps/web, and when reviewing UI work: it gives the order of work, the per-role needs, the patterns (shell, tables, forms, feedback, dashboards), motion/mobile/accessibility rules, platform constraints (Baseline, CSP, self-hosted fonts, print), the illustration spec and the review loop (worst-case data, slop audit, browser acceptance). Combines ideas from Emil Kowalski, Meng To, MC Dean (Owl-Listener) and garden-skills, rewritten for SchoolOS; see ATTRIBUTION.md.
---

# SchoolOS design

## Precedence

1. `CLAUDE.md` (invariants, frontend rules, never-do list)
2. `docs/17-ui-design-system.md` (tokens, components, page recipe, contrast table; it holds the exact values)
3. This skill (how to work, what to check)
4. Other skills in `.claude/skills/` (emil-design-eng, animate, animation-performance, break-ui, audit-ai-design-slop, apple-design, ...)

If two disagree, the higher one wins. Never copy a value from this skill into code; read it from `globals.css` / docs/17.

## The look in one line

"Quiet ledger": a calm work tool for busy office staff. Warm neutral canvas, white hairline cards, one ink-blue accent, fixed status colours, shadows only on overlays, PP Mori for text, SOS Figures (tabular digits and ₹) for every number, Instrument Serif only for hero numbers and marketing headings. Light only. Comfortable density by default, compact toggle. Marketing, parent and verification pages use "Register paper" (paper canvas, ledger green, serif headings, ruled hairlines). The operator console has dark neutral chrome with an amber marker.

## Workflow (every UI change)

1. **Read** docs/17 §5 page recipe and `references/roles.md` for who uses the screen and on what device.
2. **Classify** the change:
   - *Extension*: new screen or block in the existing language. Build only from existing primitives.
   - *Preserve*: behaviour or content change; the look must not move.
   - *Overhaul*: a deliberate redesign. Write down the protected contracts first: API calls, query keys, BFF routes, permission checks, idempotency keys, If-Match, maker-checker and step-up flows, URLs, test ids. None of them change.
3. **Build** with `@/components/ui`, `@/components/console`, `@/components/shell` primitives (`references/patterns.md`). New primitive only when two screens need it; put it in components with tests and document it in docs/17.
4. **Copy**: plain English, sentence case, verbs on buttons, errors that say how to fix the problem, consequence line on anything irreversible, en strings in `messages/en.json` with `te` kept in step (parity gate).
5. **Check motion, mobile and accessibility** with `references/motion-mobile-a11y.md` and the platform rules in `references/platform.md`.
6. **Break it** with the worst-case data in `references/review.md` (Indian names, long school names, ₹ crores, 0 and 1, missing fields), using the `break-ui` skill's method.
7. **Audit for slop** with the `audit-ai-design-slop` skill's removal test; default to subtraction.
8. **Accept in a browser** at the viewports in `references/review.md`, keyboard only, with reduced motion, at 200% zoom.
9. **Score** the screen against the rubric in `references/review.md`; ship at 8/10 or better, otherwise fix the lowest dimension first.

## Principles

- **Task first.** Each screen answers "what does this person need to do next?". One primary button per view; everything else is secondary, ghost or under "More".
- **Weakest container that works.** Plain row before card, card before nested card. A page header is a row, not a card.
- **Colour means something.** Accent for the primary action, selection and links; status colours only for status, always with text or an icon; never colour alone.
- **Numbers are first-class.** Figures font, en-IN grouping (₹1,23,45,678), right-aligned in tables, never truncated, dates as DD/MM/YYYY or "18 Oct 2026".
- **Official records are explicit.** Save with a button and a SaveBar; drafts may autosave. Identity fields go through change requests; the UI never "fixes" a record by itself.
- **Honest.** No invented numbers, logos, testimonials or activity anywhere, including marketing. Demo data is labelled sample data.
- **Patient with the network.** Keep input on failure, show "Not saved yet" and a retry, no spinners before ~1 s, skeletons only where layout is known.

## Files

- `references/roles.md`: per-role needs, homes, navigation, shared-PC and network rules
- `references/patterns.md`: shell, palette, tables, forms, feedback and loading, dashboards, fee counter, teacher flows
- `references/motion-mobile-a11y.md`: motion budget, phones and touch, WCAG 2.2 AA checks
- `references/platform.md`: Baseline, CSP, fonts, print, dependencies, Telugu switch
- `references/illustration.md`: line-interior and teal-spot spec, SVG contract, representation rules
- `references/marketing.md`: Register paper, proof, pricing, homepage scroll
- `references/review.md`: worst-case catalogue (India), slop audit, browser acceptance, scoring rubric, QA checklist
- `ATTRIBUTION.md`: sources and licences

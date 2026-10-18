# Motion, mobile and accessibility

## Motion (docs/17 §5.5 is the source; the animate and animation-performance skills explain why)
- Motion explains a change of state or place; if it doesn't, remove it. Nothing moves on density change, sort, paging, tab switch or route change.
- Durations: menus 120 ms, palette 150 ms in / 100 ms out, bulk bar and SaveBar 150 ms with an 8px rise. Ease-out for entering, faster ease-in for leaving. No bounce or overshoot on work screens.
- Animate `transform` and `opacity` only. Never `transition: all`. No animated shadows (use opacity on a shadowed layer). `will-change` only after seeing a problem.
- Everything respects `prefers-reduced-motion: reduce` (no movement; instant state change). Lenis smooth scroll is homepage-only, off under reduced motion and on coarse pointers, and never hijacks keyboard or anchor scrolling.
- Test on a mid-range Android phone or a throttled profile, not a fast laptop.

## Phones and touch (docs/17 §5.7)
- Viewports to check: 360×640, 375×812 / 390×844, 768×1024, plus 1366×768 unchanged. 320 CSS px reflow at 200% zoom: no horizontal page scroll except deliberate table scrollers.
- Inputs 16px on touch (no iOS zoom), correct `inputmode`, `autocomplete` and `enterkeyhint`; never lock zoom.
- Primary touch actions 44px; every target at least 24px (WCAG 2.5.8).
- Sticky bars respect `env(safe-area-inset-*)` and sit above the bottom bar; bottom bar hides while the keyboard is open (visualViewport).
- Menus and popovers stay inside the viewport; dialogs become full-width sheets when they would overflow; the palette stays at the top so the keyboard can't cover it.
- Parent and verification pages: server-rendered, no client JS needed, fast on 2G/3G, readable at 360px.

## Accessibility (WCAG 2.2 AA)
- Contrast from the docs/17 §7 table (4.5:1 text, 3:1 large text and control boundaries); `tokens.test.ts` recomputes it; never add a colour pair without a row there.
- Keyboard: every action reachable, visible `:focus-visible` ring, logical order, no traps; Escape closes overlays and returns focus to the trigger.
- Focus not obscured (2.4.11): `scroll-padding` for the sticky header and docked bars.
- Character key shortcuts (2.1.4): single-key shortcuts only inside a focused widget (the fee counter's C/Q/B/U), never page-wide.
- Status never by colour alone: text or icon too. `forced-colors: active` keeps borders and focus visible; decorative art hides.
- Landmarks, one h1 per page, labelled regions, `aria-current="page"` in nav, `aria-current="step"` in Steps, live region for toasts.
- Screens usable at 1366×768 and keyboard-only (CLAUDE.md definition of done).

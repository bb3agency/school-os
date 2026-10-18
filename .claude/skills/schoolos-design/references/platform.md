# Platform constraints

## Browsers
Baseline Widely Available only unless docs/17 records a fallback (CLAUDE.md §10). Targets: current Chrome/Edge on Windows 10+ office PCs and Android Chrome; slow connections.
- OK: oklch(), color-mix(), :has(), :user-invalid, container queries, native <dialog>, <details>, subgrid, text-wrap: balance (progressive), Intl.NumberFormat("en-IN"), Intl.Segmenter.
- Not yet (or only with a fallback and a note in docs/17): light-dark() (not needed, light only), the Popover API, View Transitions, anchor positioning, scroll-driven animations.
- Check current guidance (the modern-web-guidance skill) before using a pattern from memory.

## Content Security Policy
Production sends `style-src 'self'` (no 'unsafe-inline'), see `apps/web/src/lib/security-headers.ts`.
- No `style=` attributes rendered on the server and no injected `<style>` tags. Use classes and CSS variables set through CSSOM (`element.style.setProperty`) on the client when a value is truly dynamic.
- Third-party UI libraries must not inject styles; headless libraries (Radix, React Aria, Base UI) need a CSP spike and an ADR before use. No shadcn wholesale.
- SVG: no `<style>`, `class`, `<script>`, `<foreignObject>`, external `href` or `url()`; presentation attributes only. Serve illustrations as `<img>` from 'self'.

## Fonts
Self-hosted only (no Google Fonts or other CDNs): PP Mori (UI), SOS Figures (Inter digits, ₹, separators via unicode-range), Instrument Serif (hero numbers, marketing, public pages; latin subset), JetBrains Mono (IDs only), Noto Sans Telugu bundled but not loaded while `SOS_TELUGU_ENABLED` is off.

## Print
A4 print CSS is first-class: registers, receipts, certificates, slips. Hide chrome with `data-print="hide"`; landscape where docs/17 says; line-interior art prints, teal-spot art hides; no clipped Telugu glyphs when Telugu is on.

## Dependencies
No new dependency without checking licence (no AGPL in core), maintenance and known CVEs, pinned to an exact version at least two weeks old; record the check in docs/17 or the commit message. Prefer the existing inline SVG icon set over adding an icon library.

## Data and privacy in the UI
- No real student data in fixtures, screenshots or demos (invariant 11); use the synthetic seed and `example.com` / `.test` domains.
- Never show more than one Aadhaar group (last 4 only); never put personal data in URLs (search text stays in memory, SEC-008), analytics or logs.

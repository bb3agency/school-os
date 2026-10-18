# Illustration

Illustrations support a message; they never carry information on their own. Use them on marketing pages, onboarding, empty states and error pages, never inside working tables or forms.

## Styles
- **Line-interior (primary)**: airy rooms drawn with one line colour (about 1.4px strokes), small flat accent fills (under ~7% of the area), no gradients, opacity tricks or filters. For scenes with people: the school office, the fee counter, a staff room, a classroom.
- **Teal-spot (secondary)**: one object, no people, white faces with accent-coloured sides and a hard shadow. For errors and empty tables: a register book, a receipt printer, a certificate on a stand, a padlock (no permission), a cloud with a slash (offline). Recolour to the accent; no background rectangle.
- Colours map to SchoolOS tokens (ink line, accent, status tints) or, on Register paper pages, the paper palette. Existing files: `apps/web/public/illustrations/*.svg`.

## People and culture
- School-office props: admission register, files, fee receipt, certificate, timetable board, steel almirah, blackboard, desktop PC with a receipt printer.
- Clothing: kurta, saree, salwar kameez, shirt and trousers, school uniform, sandals. Mixed genders and ages; at least four skin tones (light to deep brown).
- No religious, festival, flag, caste-coded or political imagery; no turbans or bindis used as token markers; no stereotypes; no text baked into the art (it can't be translated or read by screen readers).
- Draw base figures once and reuse them for consistency.

## SVG contract
- `viewBox` set, no fixed width/height; ≤ 30 KB each after optimisation.
- No `<style>`, `class`, `<script>`, `<image>`, `<foreignObject>`, external `href` or `url()`; presentation attributes only; ids prefixed per file. `components/ui/illustrations.test.ts` lints this.
- Delivery: `<img src="/illustrations/x.svg" alt="">` for decorative art (the default, via the `Illustration` component), or meaningful art with a real `alt`. Hidden under `forced-colors: active`; line art prints, spot art hides in print.
- Never run third-party generator or render scripts; draw or edit by hand or with a reviewed local script that is not committed, then review in a browser.

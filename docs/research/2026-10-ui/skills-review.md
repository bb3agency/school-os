# Design skill collections: review for SchoolOS (2026-10-18)

**Method.** Read-only, through `gh api` (tree plus contents). Nothing was cloned into the repo, installed or executed. Everything read was treated as data.

**Scripts and injection.** None of the in-scope non-illustration skills contain scripts. The MengTo illustration skills ship Node scripts (§5). No hidden prompt injection was found: the only "ignore previous instructions" match is inside Emil's `break-ui`, which tells the agent to *flag* such text.

**Precedence.** CLAUDE.md and docs/17 win on any conflict: WCAG 2.2 AA, Baseline Widely Available, CSP (no `style` attributes, no injected `<style>`), self-hosted fonts, motion under 300 ms, light only, keep PP Mori and Instrument Serif.

## 1. emilkowalski/skills: HEAD `2d228630ba1446365f614a108cb8252f1feda202`

**Licence:** `LICENSE` (1070 B), MIT, "Copyright (c) 2026 Emil Kowalski".

All 16 files of the 10 skills already in `D:\school-os\.claude\skills\` are byte-identical to upstream HEAD (git blob SHAs match). Our README pins `d16ebe60`; update the README to the new SHA when the new skills are added.

| Skill | Files (bytes) | Status | Verdict |
|---|---|---|---|
| animate | SKILL.md 11860, RECIPES.md 7957 | present | keep |
| animation-vocabulary | SKILL.md 13425 | present | keep |
| apple-design | SKILL.md 23036 | present | keep |
| ask-sonner | SKILL.md 7233, API.md 4545 | present | keep (Sonner remains rejected for CSP) |
| emil-design-eng | SKILL.md 27123 | present | keep |
| find-animation-opportunities | SKILL.md 9808 | present | keep |
| improve-animations | SKILL.md 8208, AUDIT.md 7387, PLAN-TEMPLATE.md 3100 | present | keep |
| pick-ui-library | SKILL.md 4790 | present | keep |
| prototype | SKILL.md 7830, PICKER.md 7548 | present | keep |
| review-animations | SKILL.md 8407, STANDARDS.md 9801 | present | keep |
| **animation-performance** | SKILL.md 13966 | new | **adopt (install)** |
| **break-ui** | SKILL.md 14380, CATALOG.md 8268 | new | **adopt (install)** |
| mobile-native | SKILL.md 16838 | new | adopt-partly (rules into the combined skill) |
| animate-expo | SKILL.md 17891, RECIPES.md 16808 | new | skip (React Native) |
| write-swift | SKILL.md 42445 | new | skip (Swift) |

Also in the repo: `performance-cheatsheet.md` 932 B (a subset of animation-performance) and README.md 4590 B. The README's `npx skills@latest add …` line must not be run; copy the files instead.

- **animation-performance.**
  - Animate only `transform` and `opacity`; never `transition: all`.
  - Frame budget 16.7 ms (60 Hz) and 8 ms (120 Hz); profile on a mid-range Android phone.
  - Property swaps: width/height → `scale`, top/left → `translate`, display → opacity, animated box-shadow → `drop-shadow`.
  - Blur at most 20px (2–5px for crossfades).
  - Motion during navigation or fetching must be CSS or WAAPI; no per-frame setState. Writing `ref.style.transform` through the CSSOM is allowed under our CSP.
  - Avoid animating inherited CSS variables. Use `will-change` only after you have seen the problem.
  - Conflicts: none.
- **break-ui.**
  - Feed plausible worst-case data through the same data boundary, behind a dev-only `?data=worst` toggle (Worst / Empty / One / 1,000).
  - Check at the real container width, 320px and 200% zoom.
  - Fixes: `min-width:0`, `flex-shrink:0` on avatars and badges, `overflow-wrap:anywhere` on IDs.
  - Never truncate amounts, dates or anything users compare; middle-truncate file names.
  - Use `Intl.PluralRules`, `NumberFormat`, `DateTimeFormat` and `Segmenter` for initials.
  - Keep the fixture as a regression test.
  - Fits invariant 11. Add an Indian catalog: initial-style names ("K. V. S. R. Prasad"), single names, long village addresses, en-IN lakh grouping (12,34,567.00), a missing guardian.
- **mobile-native (adopt partly).**
  - Hover styles only under `(hover:hover) and (pointer:fine)`.
  - `:active` press feedback at scale 0.97, 100–160 ms.
  - 16px inputs on coarse pointers; `inputmode` and `enterkeyhint`; never disable zoom.
  - `dvh` with a fallback, safe-area insets, `touch-action: manipulation`, `overscroll-behavior: contain` on sheets.
  - `theme-color` through the Next `viewport` export.
  - Skip three of its rules:
    - `user-select:none` on links (clerks copy text);
    - `overscroll-behavior:none` on `html` (kills pull-to-refresh);
    - relying on `interactive-widget` (Chromium only).

## 2. ConardLi/garden-skills `skills/web-design-engineer`: HEAD `7a6d5a72e4f44bb4cc0c23c01b3d97205c723b68`

**Licence:** `LICENSE` (1056 B), MIT, "Copyright (c) 2026" with **no holder named**.

**Files (bytes):**
- `SKILL.md` 35406, `README.md` 28270, `README.zh-CN.md` 27553, `manifest.json` 609, `agents/openai.yaml` 288 (`allow_implicit_invocation: true`)
- `references/`: advanced-patterns.md 14722, block-library.md 1767, browser-acceptance.md 4643, critique-guide.md 10446, design-calibration.md 6139, design-directions.md 14105, failure-patterns.md 7119, redesign-protocol.md 4181
- `references/style-recipes/INDEX.md` 12177, plus 25 recipes of 2230–3238 B each

**Red flags:**
- **Provenance.** The README says the skill distills "Claude Design's system prompt", and the repo ships `dist/prompts/claude-design-system-prompt.md` (73,264 B). Derived content of uncertain status, plus no named copyright holder.
- **Network and CDN.**
  - `yt-dlp` and `ffmpeg` as an asset-sourcing step.
  - An unpkg Popmotion script.
  - An unpinned jsdelivr Chart.js (SKILL.md:429, advanced-patterns.md:419).
  - A Google Fonts `<link>` (advanced-patterns.md:497).
- Suggests `gpt-image-2`.

**Conflicts:**
- "Avoid Inter / system-ui / Instrument Serif"
- "aim to stun"
- h1 at 4–6× body size
- 40% or more whitespace
- `backdrop-filter`
- add creative tweaks by default

**Worth taking (as ideas, rewritten in our own words):**
- **Calibration presets.** "Public-sector service" 3/2/6/3/9; "Data dashboard" 4/3/8/3/7. Accessibility overrides aesthetic scores.
- **Redesign protocol.**
  - Classify every change as Extension, Preserve or Overhaul.
  - Protected contracts (routes, form field names and order, legal copy, test selectors, accessibility wins) never change silently.
  - Order of work: accessibility, hierarchy, spacing, tokens, states, motion.
- **Failure patterns (dashboards).**
  - No marketing styling on operational UI.
  - No happy-path-only components.
  - No decorative data viz.
  - Avoid "cardification".
  - Radius by role.
- **Browser acceptance.**
  - Never accept a change from code inspection alone.
  - Viewports 390×844, 768×1024, 1280×720 and 1440×900, plus **1366×768**.
  - Check console errors, horizontal overflow, focus order and reduced motion.

**Verdict:** adopt partly. **Do not install or vendor.**

## 3. MengTo/Skills (non-illustration): HEAD `83a47fee32f0b6349bff1fede99257a5ef03dc93`

**Licence:** `LICENSE` (1064 B), MIT, "Copyright (c) 2026 Meng To".

**Every `demo/` folder is a red flag; ignore them all.** They embed base64 pages in iframes, fetch repo-root runtime bundles (GSAP, three, iconify), and their `source.json` lists `fonts.googleapis.com`, `cdn.tailwindcss.com` and `images.unsplash.com`.

| Skill | Files (bytes), excluding demo | Verdict | Rules worth adopting |
|---|---|---|---|
| ui/no-ai-design-slop | SKILL.md 7007, ARTICLE.md 17823, REFERENCES.md 825, agents/openai.yaml 255 | adopt-partly (rules into the combined skill) | Removal test (name the element, its job, delete it mentally); proximity before containers; no invented metrics, testimonials or logos; watch for the "card apocalypse" and "motion theater" clusters |
| ui/audit-ai-design-slop | SKILL.md 6210, REFERENCES.md 825, agents/openai.yaml 240 | **adopt (install)** | Review only; P0–P3 severity; "Quality defect" vs "Slop pattern"; evidence required; 5–8 findings grouped by root cause; ends with the single biggest thing to remove |
| ui/design-first-ui-prompting | SKILL.md 3090, ARTICLE.md 3183, REFERENCES.md 141 | skip | Pushes Google Fonts, Iconify, Midjourney |
| workflow/workflow-score-to-target | SKILL.md 5197, templates/critic-prompt.md 1000, templates/scorecard.md 1149 | adopt-partly | Anchored 1–10 scale (8 = polished); score the baseline from renders; blind judge, take the lower score; never round up; keep the scorecard in `qa/<area>/` |
| web-design/operational-enterprise-ai | SKILL.md 4641, REFERENCES.md 154, agents/openai.yaml 267 | adopt-partly (marketing) | Tell each capability as permission → action → approval → output → audit → exception → rollback (mirrors maker-checker); no unsupported compliance badges. Conflict: 500–760 ms entrances |
| web-design/product-proof-saas | SKILL.md 4357, REFERENCES.md 150, agents/openai.yaml 249 | adopt-partly (marketing) | Product UI as the proof, labelled "Sample workflow"; text transcript for replays; usage units in pricing. Conflict: 420–700 ms state changes |
| web-design/number-details | SKILL.md 793 | skip | Decorative 01/02/03 numbering |
| web-design/beautiful-shadows | SKILL.md 2540 | adopt-partly | Layered neutral shadows (sm: 1px ring at 0.08 plus 2–3px soft; lg: stacked layers at up to 0.12) ported into `@theme` tokens; no coloured tints; never lg on dense lists |
| web-design/tailwindcss | SKILL.md 2816, REFERENCES.md 72 | skip | v3-era; `focus:` instead of `focus-visible:` |
| web-design/orange-clean-paper-saas | SKILL.md 4038 | skip | Gradient borders, ambient motion |
| web-design/pricing-page | SKILL.md 5858, REFERENCES.md 278 | adopt-partly | Consistent CTA verbs; grouped comparison; 3–6 limits per plan; stacked on mobile; plain FAQ. Ignore prices and toggles (our pricing page is an explainer without prices) |
| web-design/landing-page | SKILL.md 5275, REFERENCES.md 288 | adopt-partly | One CTA above the fold; specificity; proof next to each claim; no fake logos |

## 4. Owl-Listener/designer-skills: HEAD `9a6930cf84a822eb458624bd11c61aac5bbdf224`

**Licence:** `LICENSE` (1064 B), MIT, "Copyright (c) 2026 MC Dean".

Each skill is a single short SKILL.md with no scripts and no links. Repo-level CI scripts (`scripts/*.py`, `build-gemini.sh`) are outside the skills and should not be copied. The `.gemini/` GEMINI.md files are duplicates. The guidance is mostly generic, so harvest the rules rather than installing the files.

### ui-design

All files are `ui-design/skills/<name>/SKILL.md`.

| Skill | Bytes | Verdict | Rules |
|---|---|---|---|
| aesthetic-usability | 2729 | adopt-partly | |
| color-system | 1682 | adopt | Contrast 4.5:1 text, 3:1 large text and UI; never colour alone; semantic bg/fg/border/icon set |
| dark-mode-design | 1718 | skip | Light only |
| data-visualization | 2055 | adopt | Simplest chart that answers the question; bar axes start at 0; label directly; table alternative; never red-green only |
| illustration-style | 1874 | adopt-partly | Outline for our illustration spec (§5) |
| law-of-closure | 4638 | adopt-partly | Keep explicit boundaries in dense tables |
| law-of-common-region | 2987 | adopt | Weakest container that works; at most 2 levels of nesting |
| law-of-continuity | 4395 | adopt-partly | |
| law-of-figure-ground | 4978 | adopt-partly | Scrim behind modals; surface stack as tokens |
| law-of-proximity | 2673 | adopt | Label closer to its input than to the next field; destructive action set apart |
| law-of-similarity | 4667 | adopt | Unintended visual differences are bugs; status coded twice (shape plus colour) |
| layout-grid | 1616 | adopt-partly | Add the 1366 breakpoint check |
| platform-conventions | 6631 | skip | Native apps |
| readable-measure | 2246 | adopt | Prose at most 65ch |
| responsive-design | 1732 | adopt-partly | |
| spacing-system | 1416 | adopt | 2/4/8/16/24/32/48/64 scale |
| typography-scale | 1542 | adopt-partly | Its 16px minimum body size conflicts with dense tables; docs/17 wins |
| visual-hierarchy | 1876 | adopt | One primary action; size steps of at least 1.5× |
| von-restorff-effect | 2391 | adopt | Only one element deviates; the difference must survive grayscale |

### design-systems

| Skill | Bytes | Verdict | Rules |
|---|---|---|---|
| design-token | 1554 | adopt | Tiers global → alias → component |
| component-spec | 1344 | adopt | Spec template with every state |
| accessibility-audit | 1389 | adopt-partly | |
| theming-system | 1419 | adopt-partly | Density modes |
| motion-system | 4515 | adopt-partly | 4–6 duration tokens; stagger 30–50 ms, at most 500 ms in total; reduced motion handled at the root. Reject its spring overshoot and its 400/600 ms tokens |
| pattern-library | 1329 | adopt-partly | |

### interaction-design

| Skill | Bytes | Verdict | Rules |
|---|---|---|---|
| form-design | 3992 | adopt | Single column; mark optional fields; radios for up to 5 options, select for 6 or more; validate on blur; focusable error summary; review step for high-stakes forms |
| loading-states | 1918 | adopt | Under 100 ms nothing; up to 1 s subtle; 1–10 s explicit; over 10 s progress with cancel |
| error-handling-ux | 1950 | adopt | What happened, why, what to do; keep the user's input; never "Something went wrong" |
| feedback-patterns | 2118 | adopt-partly | Feedback inline first; errors persist; undo instead of "Are you sure?" |
| navigation-patterns | 4165 | adopt | Breadcrumbs; active state not shown by colour alone |
| search-ux | 4341 | adopt | Suggestions after 2–3 characters, 5–8 shown; navigational queries return the destination; filter chips with "Clear all"; zero results echo the query |
| onboarding-design | 4482 | adopt-partly | Empty state = what goes here plus a first action; sample data labelled and clearable |
| interfaces-that-feel | 3945 | adopt-partly | Copy voice per state. Reject its 400–600 ms transitions |

### visual-critique and design-ops

| Skill | Bytes | Verdict | Rules |
|---|---|---|---|
| visual-critique/critique-affordance | 3243 | adopt | |
| visual-critique/critique-color | 3178 | adopt | Measured contrast ratios; check forced-colors |
| visual-critique/critique-typography | 3335 | adopt-partly | |
| visual-critique/critique-composition | 3107 | adopt-partly | |
| visual-critique/critique-information-density | 3412 | adopt | |
| visual-critique/critique-visual-hierarchy | 2453 | adopt | |
| visual-critique/critique-brand-consistency | 2914 | adopt-partly | |
| design-ops/design-qa-checklist | 2135 | adopt | |

## 5. Illustration: MengTo `agent-skills/illustration/*` (14 styles) and Owl `illustration-style`

**Shared by every MengTo style:**
- `README.md` 5239 B.
- `references/craft.md` 10528 B (identical in all 14), the SVG file contract:
  - `viewBox="0 0 480 360"`;
  - no `<style>`, `class`, `<script>`, `<image>`, `<foreignObject>`, external `href` or `url()`;
  - presentation attributes only;
  - per-card id prefixes.

**CSP check on all 42 example SVGs:** 0 `style="` attributes, 0 `<style>` elements, 0 `class` attributes. Every example carries `role="img" aria-label="…, X style"`; only 1 has a `<title>`.

**Red flags:**
- Every style ships executable scripts:
  - `scripts/lint.mjs` (3079 B);
  - `scripts/render.mjs` (5071 B), which runs `execSync('npm root -g')` and launches headless Chromium;
  - a per-style generator kit that writes files;
  - the README tells you to `npm i -g playwright-core`.
- **Do not run any of them** without an owner decision. Port lint.mjs's rules into a vitest instead.
- Demos are 50–956 KB HTML.
- The kits interpolate `aria-label` without escaping.
- No network calls; no agent-directed instructions.
- Cultural scan: clean.

| Style | SKILL.md / style.json / kit (B) | Example SVGs | Character | Verdict |
|---|---|---|---|---|
| flat | 19516 / 2531 / flat-kit.mjs 26541 | 11–15 KB | Slim figures, no outlines, lavender/coral confetti; **one skin tone only ("never a second skin tone")** | skip (not representative) |
| flat-with-black | 20940 / 4444 / parts.mjs 22718 | 15–22 KB | Flat colours with large black masses; cream skin only | skip |
| outlined-cartoon | 18424 / 4427 / outline-kit.mjs 24350 | 80–95 KB | Big heads, huge eyes | skip (childish) |
| bold-pop | 20422 / 3208 / pop.mjs 26142 | 48–68 KB | Chunky, saturated, 29 patterns | skip (loud) |
| **line-interior** | 22278 / 3107 / interior.mjs 34985 | 19–29 KB | Airy rooms; one 1.4px line colour; accents under 7%; no gradients, opacity or filters; 4 skin tones | **primary** |
| framed-panel | 22253 / 9394 / panel-kit.mjs 26709 | 22–23 KB | Pastel framed panel | skip (busy) |
| grainy-gouache | 20962 / 14335 / gouache.mjs 18750 | 26–55 KB | 10 filters, `feTurbulence`, blend modes | skip (costly, bad in print) |
| halftone-line | 18925 / 1831 / halftone.mjs 23235 | 100–337 KB | Monochrome halftone, 2,498 elements | skip (heavy) |
| ink-sketch | 18455 / 4252 / ink-kit.mjs 38009 | 229–331 KB | Googly-eyed critters | skip |
| isometric-mono | 19954 / 2512 / iso.mjs 43995 (+ ramp-check.mjs 3844, dev-stack.gen.mjs 3773) | 142–216 KB | Isometric developer infrastructure | skip (heavy, off-topic) |
| one-line | 20132 / 2028 / oneline.mjs 19643 | 22–38 KB | A single continuous line, riso patches | optional for marketing |
| **teal-spot** | 18298 / 2582 / teal3d.mjs 25680 (+ starter.mjs 5068) | 50–200 KB (SVGO precision 1 cuts this a lot) | One object, no people, white with teal sides and a hard shadow; no gradients, opacity or text | **secondary** |
| two-colour-brush | 18372 / 1863 / brush.mjs 24737 | 287–369 KB | Heavy marker | skip |
| vintage-emblem | 15880 / 2228 / emblem-kit.mjs 15216 + wordmark.mjs 19450 | 13–76 KB | Lettering baked into the art | skip (accessibility and localisation) |

**Owl `ui-design/skills/illustration-style/SKILL.md` (1874 B).** A checklist: style axes, palette as a subset of the product palette, diverse characters, the types (spot, hero, empty, onboarding, error), and "never convey information only through an illustration". Adopt partly, as the outline of our illustration spec.

**Recommendation.**
- **line-interior** for onboarding, marketing and empty states with people.
  - Remap the indigo line `#3a2d66` and the accents to SchoolOS tokens.
  - Replace the developer props with school-office props: registers, files, a fee receipt, a certificate, a timetable board, a steel almirah, a blackboard.
  - Add Indian clothing parts: kurta, saree or salwar, school uniform, sandals.
  - Keep its 4 skin tones (`#f3cdb0`, `#e2a57c`, `#b47852`, `#7a4b33`), mixed genders and ages.
  - Avoid stereotypes: no turbans or bindis as token markers, and no religious, festival, flag or caste-coded imagery.
- **teal-spot** for errors and empty tables. It has no people, so there is no representation risk. Candidate subjects: a register book, a receipt printer, a certificate on a stand, a lock (permission error), a cloud with a slash (offline). Recolour it to the accent and drop its `#ebebeb` background rect.
- **Implementation:**
  - Serve as `<img src="/illustrations/x.svg" alt="">` from `'self'`: CSP-safe, cacheable, no id collisions, light on JS. Inline only when `currentColor` is needed.
  - Decorative art gets `aria-hidden="true" focusable="false"`. Meaningful art gets `role="img"` plus a `<title>`, without the "X style" suffix.
  - Forced colors: `stroke="currentColor"` on line-interior, or hide the art under `forced-colors: active`.
  - Print: line-interior prints cleanly; hide teal-spot art in print.
  - Figure drawing is the hard part. Budget a review loop, or commission base figures once and reuse them.

## 6. Proposed single combined skill: `.claude/skills/schoolos-design/`

**Licence and attribution.** Write it in our own words. Ship `ATTRIBUTION.md` listing each MIT source (repo, HEAD SHA, copyright line, the skills drawn from) with the MIT permission notice included once per source:
- Emil Kowalski 2026
- Meng To 2026
- MC Dean 2026
- garden-skills: ideas only, no text copied; credit as inspiration because no holder is named

Separately, install verbatim under `.claude/skills/`: Emil `animation-performance` (13,966 B) and `break-ui` (SKILL.md 14,380 + CATALOG.md 8,268 B), and MengTo `audit-ai-design-slop` (SKILL.md 6,210 + REFERENCES.md 825 B; omit `agents/openai.yaml`). Update `.claude/skills/README.md` and the licence files.

**Outline:**

```
schoolos-design/
  SKILL.md            when to use; precedence (CLAUDE.md > docs/17 > this skill); the workflow below
  ATTRIBUTION.md
  references/
    principles.md     calm work tool; removal test; colour = meaning; one primary action; weakest container
    tokens.md         pointer to globals.css/docs/17; OKLCH; contrast pairs; spacing 4-pt scale; radius by role; shadows only on overlays
    roles.md          per-role needs and home/nav (report §2); low digital literacy; patchy internet; shared PCs (no PII in storage)
    shell-nav.md      sidebar, top bar, page header row, command palette (APG combobox), mobile bottom bars, breadcrumbs
    tables.md         DataTable v2: density, sticky header, columns, selection/bulk (Cloudscape rules), chips, states, paging, numbers (.num, en-IN, never truncate amounts)
    forms.md          labels, optional marking, blur + :user-invalid, error summary, explicit save vs draft autosave, steppers, fee-counter flow
    feedback-loading.md  NN/g + Owl thresholds; skeleton rules; inline results; error copy formula; optimistic UI only for low-stakes
    motion.md         docs/17 §5.5 + animation-performance rules + palette/bulk-bar additions; what never moves
    mobile.md         mobile-native rules (minus the three skipped), 44px coarse, sheets, stacked tables
    a11y.md           WCAG 2.2 AA: 2.5.8, 2.4.11 + scroll-padding, 2.1.4, focus-visible, forced-colors, colour never alone
    platform-constraints.md  Baseline list (allowed / not yet), CSP rules for libraries and SVG, self-hosted fonts, print
    illustration.md   line-interior + teal-spot spec, SVG contract (from craft.md, rewritten), cultural guidance, a11y, print
    marketing.md      Direction B; honest proof; operational-enterprise storytelling; pricing explainer rules; no slop template
    review.md         Extension/Preserve/Overhaul; protected contracts; break-ui worst-case pass (Indian catalog); audit-ai-design-slop P0–P3; score-to-target with blind judge; browser acceptance at 1366×768, 390×844, 768×1024, 1440×900, 320px, 200% zoom; design-qa checklist
```

**Workflow in `SKILL.md`:**
1. Read docs/17 and the role table.
2. Classify the change (Extension, Preserve or Overhaul).
3. Build with primitives only.
4. Run the break-ui worst-case pass.
5. Run the slop audit.
6. Do browser acceptance at the listed viewports, keyboard-only and with reduced motion.
7. Score against the rubric with a blind judge, aiming for 8/10 or better.

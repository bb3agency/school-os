# Project skills

Start with **`schoolos-design/`**: the SchoolOS design and UI-review playbook (order of work, roles,
patterns, motion/mobile/accessibility, platform constraints, illustration, marketing, review loop).
It is written for SchoolOS in our own words; its sources are listed in `schoolos-design/ATTRIBUTION.md`.

Third-party skills installed verbatim (text only; no scripts, demos or assets):

- Design-engineering skills by Emil Kowalski, from https://github.com/emilkowalski/skills (MIT, see
  LICENSE-emilkowalski-skills). animate, animation-vocabulary, apple-design, ask-sonner,
  emil-design-eng, find-animation-opportunities, improve-animations, pick-ui-library, prototype and
  review-animations were copied at commit d16ebe60d09a5ba2afcb7054ede9d0a10c9f6128 (byte-identical at
  2d228630); animation-performance and break-ui were added at commit
  2d228630ba1446365f614a108cb8252f1feda202 (2026-10-18). Only the web-relevant skills are included
  (the Expo, native mobile and Swift ones are left out).
- audit-ai-design-slop by Meng To, from https://github.com/MengTo/Skills at commit
  83a47fee32f0b6349bff1fede99257a5ef03dc93 (MIT, see LICENSE-mengto-skills). Its `agents/openai.yaml`
  is omitted. Its full-site step refers to `../no-ai-design-slop/ARTICLE.md`, which is not installed:
  use `schoolos-design/references/review.md` and `marketing.md` instead.

All of them are guidance for AI coding sessions. CLAUDE.md and docs/17-ui-design-system.md win on any
conflict: WCAG 2.2 AA, Baseline Widely Available features, prefers-reduced-motion, CSP (no inline
styles), self-hosted fonts, and no new dependency without the licence/maintenance/CVE check.
Library suggestions in pick-ui-library are options to evaluate, not approvals. Never run a skill's
install command (`npx skills add`) or any third-party script.

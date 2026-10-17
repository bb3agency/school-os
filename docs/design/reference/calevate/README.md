# Calevate UI components (reference copy)

Copied on 2026-10-18 at the owner's request from the owner's other project, `D:\Agency\calevate`
(Calevate web app), so the SchoolOS redesign can reuse its console components where they fit.

| Here | Source in Calevate |
|---|---|
| `components/ui.tsx` | `apps/web/src/components/ui.tsx` |
| `components/console/*` | `apps/web/src/components/console/*` |
| `components/interior/*` | `apps/web/src/components/interior/*` (adapted from interior.dev, MIT, see `components/interior/LICENSE`; keep that notice with any copy) |
| `lib/{focusTrap,useUnsavedGuard,lookup}.ts` | `apps/web/src/lib/*` (helpers the components import) |
| `docs/UI-COMPONENTS.md` | `docs/design/UI-COMPONENTS.md` (Calevate's ADOPT/ADAPT/SKIP review of interior.dev and Pixel Perfect) |

**This folder is reference material, not part of the build.** It lives outside `apps/web`, so it is
not compiled, linted or shipped. To use a component, port it into `apps/web/src/components/`:
replace Calevate imports (`@/components/ui`, `@/lib/*`, Calevate tokens and copy) with SchoolOS
equivalents, follow `docs/17-ui-design-system.md` and the `schoolos-design` skill, keep TypeScript
strict (no `any`), keep `motion` usage behind `useReducedMotion`, add `en` (and `te`) strings, and
add tests. Pixel Perfect code is not reproduced here and must not be copied (no licence).

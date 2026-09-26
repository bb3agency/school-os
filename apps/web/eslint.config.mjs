import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import { defineConfig, globalIgnores } from "eslint/config";

// jsx-a11y "recommended" rules, enabled by name (the plugin is registered by
// eslint-config-next; importing it directly clashes with its eslint<=9 peer range).
const a11yRules = Object.fromEntries(
  [
    "alt-text",
    "anchor-has-content",
    "anchor-is-valid",
    "aria-activedescendant-has-tabindex",
    "aria-props",
    "aria-proptypes",
    "aria-role",
    "aria-unsupported-elements",
    "autocomplete-valid",
    "click-events-have-key-events",
    "heading-has-content",
    "html-has-lang",
    "iframe-has-title",
    "img-redundant-alt",
    "interactive-supports-focus",
    "label-has-associated-control",
    "media-has-caption",
    "mouse-events-have-key-events",
    "no-access-key",
    "no-autofocus",
    "no-distracting-elements",
    "no-interactive-element-to-noninteractive-role",
    "no-noninteractive-element-interactions",
    "no-noninteractive-element-to-interactive-role",
    "no-noninteractive-tabindex",
    "no-redundant-roles",
    "no-static-element-interactions",
    "role-has-required-aria-props",
    "role-supports-aria-props",
    "scope",
    "tabindex-no-positive",
  ].map((rule) => [`jsx-a11y/${rule}`, "error"]),
);

const browserStorageMessage =
  "Do not store data in browser storage: tokens and personal data stay server-side (docs/07 §5.2).";

export default defineConfig([
  ...nextVitals,
  ...nextTypescript,
  globalIgnores([
    ".next/**",
    "out/**",
    "coverage/**",
    "playwright-report/**",
    "test-results/**",
    "next-env.d.ts",
  ]),
  {
    // eslint-plugin-react's "detect" calls context.getFilename(), removed in ESLint 10.
    settings: { react: { version: "19.3" } },
    rules: {
      ...a11yRules,
      // Scrollable table regions and tab panels must be focusable (WCAG 2.1.1).
      "jsx-a11y/no-noninteractive-tabindex": [
        "error",
        { tags: [], roles: ["tabpanel", "region"], allowExpressionValues: true },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "react/no-danger": "error",
      "react/forbid-dom-props": ["error", { forbid: ["style"] }],
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: browserStorageMessage },
        { name: "sessionStorage", message: browserStorageMessage },
      ],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "localStorage", message: browserStorageMessage },
        { object: "window", property: "sessionStorage", message: browserStorageMessage },
      ],
      "no-restricted-imports": [
        "error",
        {
          paths: [
            { name: "next/font/google", message: "Fonts are self-hosted via @fontsource." },
            { name: "next/link", message: "Use Link from @/i18n/navigation (locale-aware)." },
          ],
        },
      ],
    },
  },
]);

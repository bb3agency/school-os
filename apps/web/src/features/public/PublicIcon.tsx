/**
 * Decorative icons for the parent and verification pages (docs/17 §5.8). Inline SVG with
 * presentation attributes only (no `style`, CSP-safe), `currentColor` strokes so forced-colours
 * mode keeps them visible, and always `aria-hidden`: the words beside them carry the meaning.
 */
const PATHS = {
  valid: "M5 12.5l4.5 4.5L19 7.5",
  duplicate:
    "M8 8V5.5A1.5 1.5 0 0 1 9.5 4h9A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H16M5.5 8h9A1.5 1.5 0 0 1 16 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 18.5v-9A1.5 1.5 0 0 1 5.5 8z",
  cancelled: "M6.5 6.5l11 11M17.5 6.5l-11 11",
  alert:
    "M12 8v5M12 16.5v.01M10.3 4.2L2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z",
  clock: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
  unlinked:
    "M9.5 14.5l5-5M10.6 6.4l.9-.9a4 4 0 0 1 5.7 5.7l-.9.9M13.4 17.6l-.9.9a4 4 0 0 1-5.7-5.7l.9-.9M4 4l16 16",
} as const;

export type PublicIconName = keyof typeof PATHS;

export function PublicIcon({ name }: { name: PublicIconName }) {
  return (
    <svg
      className="public-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

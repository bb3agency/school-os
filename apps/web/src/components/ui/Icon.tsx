import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Stroke icons (24×24, 2px, round caps), inline SVG so they need no image requests and
 * no inline styles (CSP-safe). Always decorative (`aria-hidden`): the control around the
 * icon carries the accessible name.
 */
const paths = {
  home: <path d="M3 10.5 12 3l9 7.5M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5" />,
  users: (
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm13 10v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
  ),
  folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />,
  file: (
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Zm0 0v5h5M9 13h6M9 17h4" />
  ),
  upload: <path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />,
  camera: (
    <path d="M4 8a2 2 0 0 1 2-2h1.5l1.5-2h6l1.5 2H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Zm8 8a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" />
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  checkCircle: <path d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM8 12.5l2.7 2.7L16 9.8" />,
  shieldCheck: (
    <path d="M12 3 4.5 6v5.5c0 4.4 3.1 8.3 7.5 9.5 4.4-1.2 7.5-5.1 7.5-9.5V6ZM9 12l2 2 4-4" />
  ),
  sparkles: (
    <path d="M12 3v3m0 12v3M3 12h3m12 0h3M6.3 6.3l2 2m7.4 7.4 2 2m0-11.4-2 2m-7.4 7.4-2 2M12 9l1 2 2 1-2 1-1 2-1-2-2-1 2-1Z" />
  ),
  message: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-5A8 8 0 1 1 21 12Z" />,
  settings: (
    <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.5 7.5 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.5 7.5 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.5 7.5 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7.5 7.5 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z" />
  ),
  layers: <path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5" />,
  creditCard: (
    <path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Zm0 3h18M7 15h4" />
  ),
  lifeBuoy: (
    <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-6.4 2.4 3.6-3.6m5.6-5.6 3.6-3.6m0 12.8-3.6-3.6M8.8 8.8 5.2 5.2" />
  ),
  clipboard: (
    <path d="M9 4h6v3H9ZM9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 13l2 2 4-4" />
  ),
  key: <path d="M15 9a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm-3 3v9m0-4h3m-3 2h2" />,
  flag: <path d="M5 21V4m0 0h11l-2 4 2 4H5" />,
  activity: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  megaphone: <path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1Zm13-3a5 5 0 0 1 0 8" />,
  server: (
    <path d="M4 5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v5H4Zm0 5h16v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Zm3-3h.01M7 13h.01M8 20h8" />
  ),
  receipt: <path d="M6 3h12v18l-3-2-3 2-3-2-3 2Zm3 5h6m-6 4h6m-6 4h3" />,
  chart: <path d="M4 20V4m0 16h16M8 16v-4m4 4V8m4 8v-6" />,
  building: (
    <path d="M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16m0-10h4a1 1 0 0 1 1 1v9M3 21h18M8 8h3m-3 4h3m-3 4h3" />
  ),
  search: <path d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm9 2-4-4" />,
  filter: <path d="M4 5h16l-6 7.5V19l-4 2v-8.5Z" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  bell: <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronRight: <path d="m9 6 6 6-6 6" />,
  chevronLeft: <path d="m15 6-6 6 6 6" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  arrowUp: <path d="M12 19V5m0 0-6 6m6-6 6 6" />,
  arrowDown: <path d="M12 5v14m0 0 6-6m-6 6-6-6" />,
  arrowRight: <path d="M5 12h14m0 0-6-6m6 6-6 6" />,
  send: <path d="M21 3 10 14M21 3l-7 18-4-7-7-4Z" />,
  calendar: (
    <path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Zm0 3h16M8 3v4m8-4v4" />
  ),
  clock: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4l3 2" />,
  eye: (
    <path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7Zm9.5 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
  ),
  info: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13h.01M11 12h1v5h1" />,
  alert: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5m0 3h.01" />,
  inbox: <path d="M3 13h5l1.5 2h5l1.5-2h5M5 5h14l2 8v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5Z" />,
  logOut: <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4m-4 4h11" />,
  lock: <path d="M6 11h12v9a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1Zm2 0V8a4 4 0 1 1 8 0v3" />,
  globe: (
    <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm-9-9h18M12 3c2.5 2.5 3.5 5.5 3.5 9s-1 6.5-3.5 9c-2.5-2.5-3.5-5.5-3.5-9s1-6.5 3.5-9Z" />
  ),
  swap: <path d="M7 4 3 8l4 4M3 8h14m0 12 4-4-4-4m4 4H7" />,
  // Sidebar collapse / expand (a panel with its left column and an arrow).
  panelLeftClose: (
    <path d="M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm4 0v18m7-12-3 3 3 3" />
  ),
  panelLeftOpen: (
    <path d="M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm4 0v18m5-12 3 3-3 3" />
  ),
  cornerDownRight: <path d="M5 4v7a4 4 0 0 0 4 4h10m-4-4 4 4-4 4" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
  // Ask chat (docs/17 §5.3).
  stop: <path d="M7 7h10v10H7Z" />,
  copy: (
    <path d="M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1Zm-4 6H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
  ),
  refresh: <path d="M20 11a8 8 0 0 0-14.8-4M4 4v4h4m-4 5a8 8 0 0 0 14.8 4M20 20v-4h-4" />,
  pencil: <path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Zm10-13 3 3" />,
  thumbsUp: (
    <path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Zm0 0 4-8a2 2 0 0 1 2 2v4h5.5a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.3 20H7" />
  ),
  thumbsDown: (
    <path d="M17 13V4h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1Zm0 0-4 8a2 2 0 0 1-2-2v-4H5.5a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 6.7 4H17" />
  ),
  pin: <path d="M9 4h6m-5 0v5l-3 4h10l-3-4V4m-2 9v7" />,
  trash: <path d="M4 7h16M10 11v6m4-6v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />,
  memory: (
    <path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V6a2 2 0 0 0-3-2Zm6 0a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1" />
  ),
  newChat: (
    <path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6m-1.5-8.5a2.1 2.1 0 0 1 3 3L13 15l-4 1 1-4Z" />
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof paths;

/** Every icon name, for the dev reference page. */
export const ICON_NAMES = Object.keys(paths) as IconName[];

/** A caller's size class replaces the default (cn joins classes, it does not merge them). */
const SIZED = /(^|\s)(size|h|w)-/;

/** Decorative icon, 20px unless `className` sets a size (`size-4`, `h-3 w-3`). */
export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn(SIZED.test(className ?? "") ? undefined : "size-5", "shrink-0", className)}
    >
      {paths[name]}
    </svg>
  );
}

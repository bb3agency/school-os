import type { SidebarTheme } from "@/components/ui/SidebarNav";

/**
 * The canvas behind the console's cards (docs/17 §3.2).
 *
 * - `gradient`: the soft sky-blue to aqua body gradient. Kept for the two dashboards (school
 *   home, platform home) and the public marketing pages.
 * - `neutral`: a near-neutral flat canvas (`--color-canvas-neutral`) for dense record
 *   screens, so the only colour on the page is the colour that means something (a status
 *   pill, an error).
 *
 * The rule lives here, once, instead of in each screen. A page that needs the other canvas
 * passes `canvas` to the shell.
 */
export type ShellCanvas = "gradient" | "neutral";

/** Locale-free path prefixes of the dense record screens (each includes its sub-pages). */
export const NEUTRAL_CANVAS_PATHS = {
  school: [
    "/students",
    "/findings",
    "/change-requests",
    "/imports",
    "/register-photos",
    "/exports",
    "/documents",
    "/audit",
    "/settings",
  ],
} as const satisfies Record<"school", readonly string[]>;

function under(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** Which canvas a console page gets, from its theme and its locale-free path. */
export function canvasFor(theme: SidebarTheme, pathname: string): ShellCanvas {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  if (theme === "platform") {
    // Every platform page but the dashboard is a table of records.
    return path === "/platform" || path === "" || path === "/" ? "gradient" : "neutral";
  }
  return NEUTRAL_CANVAS_PATHS.school.some((prefix) => under(path, prefix))
    ? "neutral"
    : "gradient";
}

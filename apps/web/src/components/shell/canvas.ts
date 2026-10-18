import type { SidebarTheme } from "@/components/ui/SidebarNav";

/**
 * The canvas behind the console (docs/17 §3.2). Since v1.0 ("Quiet ledger", owner decision
 * 2026-10-18) there is ONE flat warm canvas everywhere, dashboards included; the gradient
 * is gone. The type and the `canvas` prop stay so older callers keep compiling; "gradient"
 * now paints the same flat canvas.
 */
export type ShellCanvas = "gradient" | "neutral";

/** Kept for older imports: every console page is on the flat canvas now. */
export const NEUTRAL_CANVAS_PATHS = {
  school: ["/"],
} as const satisfies Record<"school", readonly string[]>;

/** Which canvas a console page gets: always the flat one. */
export function canvasFor(...where: [theme: SidebarTheme, pathname: string]): ShellCanvas {
  void where;
  return "neutral";
}

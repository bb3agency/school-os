import type { SidebarTheme } from "@/components/ui/SidebarNav";
import { cn } from "@/lib/cn";

/**
 * Sidebar chrome per theme (docs/17 §5.2, contrast pairs in §7). A plain module (no
 * "use client"), so Server Components such as the dev UI reference may read it too.
 */
export const sidebarThemes: Record<
  SidebarTheme,
  { surface: string; brand: string; control: string; divider: string }
> = {
  school: {
    surface: "bg-canvas text-ink",
    brand: "text-ink hover:bg-surface-sunken",
    control: "text-ink-muted hover:bg-surface-sunken hover:text-ink",
    divider: "border-border",
  },
  platform: {
    // .platform-chrome switches the focus ring to amber on the dark neutral chrome.
    // A 2px amber marker along the top edge continues the top bar's (AppShell).
    surface: "platform-chrome border-t-2 border-t-platform-accent bg-platform text-platform-ink",
    brand: "text-platform-ink hover:bg-platform-hover",
    control: "text-platform-muted hover:bg-platform-hover hover:text-platform-ink",
    divider: "border-platform-hover",
  },
};

/** Square 36px (or 44px) icon button for the sidebar header (collapse toggle, drawer close). */
export function sidebarControlClasses(theme: SidebarTheme, size: "md" | "lg" = "md"): string {
  return cn(
    "pressable inline-flex shrink-0 items-center justify-center rounded-md",
    size === "lg" ? "size-11" : "size-9 pointer-coarse:size-11",
    sidebarThemes[theme].control,
  );
}

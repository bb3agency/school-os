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
    surface: "bg-surface text-ink",
    brand: "text-ink hover:bg-surface-muted",
    control: "text-ink-muted hover:bg-surface-muted hover:text-ink",
    divider: "border-border",
  },
  platform: {
    // .platform-chrome switches the focus ring to yellow on the dark violet.
    surface: "platform-chrome bg-platform text-platform-ink",
    brand: "text-platform-ink hover:bg-platform-hover",
    control: "text-platform-muted hover:bg-platform-hover hover:text-platform-ink",
    divider: "border-platform-hover",
  },
};

/** Square 40px (or 44px) icon button for the sidebar header (collapse toggle, drawer close). */
export function sidebarControlClasses(theme: SidebarTheme, size: "md" | "lg" = "md"): string {
  return cn(
    "inline-flex shrink-0 items-center justify-center rounded-md transition-colors",
    size === "lg" ? "size-11" : "size-10",
    sidebarThemes[theme].control,
  );
}

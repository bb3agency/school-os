/* Shared by Tabs (client) and TabNav (server): plain module, no "use client". */

export type TabsVariant = "segmented" | "underline";

/** Class names for the tab list and one tab, shared with `TabNav`. */
export function tabStyles(variant: TabsVariant) {
  return variant === "segmented"
    ? {
        list: "inline-flex max-w-full flex-wrap gap-1 rounded-lg bg-surface-sunken p-1",
        tab: "inline-flex min-h-9 items-center rounded-md border px-4 text-sm font-semibold transition-colors pointer-coarse:min-h-11",
        active: "border-border bg-surface text-ink shadow-raised",
        inactive: "border-transparent text-ink-muted hover:text-ink",
      }
    : {
        list: "flex flex-wrap gap-1 border-b border-border",
        tab: "-mb-px inline-flex items-center border-b-2 px-4 py-2 text-sm font-semibold pointer-coarse:min-h-11",
        active: "border-primary text-primary",
        inactive: "border-transparent text-ink-muted hover:text-ink",
      };
}

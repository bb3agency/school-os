"use client";

import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

export interface NavItem {
  href: string;
  label: string;
  /** Match only this exact path (for section roots such as "/" or "/platform"). */
  exact?: boolean;
  /** A sub-entry of the item above it (shown indented). */
  nested?: boolean;
  /**
   * Also the current page for paths matching this regular expression (source text, so the
   * item stays serialisable from server components), e.g. a year's promotion screen.
   */
  activePattern?: string;
}

export type SidebarTheme = "school" | "platform";

/** How well `item` matches `pathname`: -1 not at all; longer prefixes and patterns win. */
function matchScore(pathname: string, item: NavItem): number {
  if (item.activePattern && new RegExp(item.activePattern).test(pathname)) {
    return Number.MAX_SAFE_INTEGER;
  }
  if (item.exact) return pathname === item.href ? item.href.length : -1;
  return pathname === item.href || pathname.startsWith(`${item.href}/`) ? item.href.length : -1;
}

/**
 * The one item that is the current page: the most specific match, so a sub-entry such as
 * "Promotions" under "School structure" is marked alone, never together with its parent.
 */
export function activeHref(pathname: string, items: readonly NavItem[]): string | null {
  let best: NavItem | null = null;
  let bestScore = -1;
  for (const item of items) {
    const score = matchScore(pathname, item);
    if (score > bestScore) {
      best = item;
      bestScore = score;
    }
  }
  return best?.href ?? null;
}

const themes: Record<SidebarTheme, { link: string; active: string }> = {
  school: {
    link: "text-ink hover:bg-surface-muted",
    active: "bg-primary-soft text-primary font-semibold",
  },
  platform: {
    link: "text-platform-ink hover:bg-platform-hover",
    active: "bg-platform-hover text-platform-ink font-semibold underline underline-offset-4",
  },
};

/** Primary navigation. The current page is marked with aria-current="page". */
export function SidebarNav({
  label,
  items,
  theme = "school",
}: {
  label: string;
  items: readonly NavItem[];
  theme?: SidebarTheme;
}) {
  const pathname = usePathname() ?? "";
  const styles = themes[theme];
  const current = activeHref(pathname, items);
  return (
    <nav aria-label={label} data-print="hide">
      <ul className="space-y-1">
        {items.map((item) => {
          const active = item.href === current;
          return (
            <li key={item.href} className={item.nested ? "ms-4" : undefined}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "block rounded-md px-3 py-2 text-sm",
                  active ? styles.active : styles.link,
                )}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

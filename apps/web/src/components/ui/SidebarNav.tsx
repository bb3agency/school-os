"use client";

import { useId } from "react";
import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

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
  /** Optional leading icon (name from `Icon`, serialisable). */
  icon?: IconName;
}

/** A titled group of items in the secondary list panel ("RECORDS", "CHECKS"). */
export interface NavSection {
  id: string;
  label: string;
  /** Icon for the section's button in the rail. */
  icon: IconName;
  items: readonly NavItem[];
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

/** The id of the section holding the current page, or null. */
export function activeSectionId(pathname: string, sections: readonly NavSection[]): string | null {
  const current = activeHref(
    pathname,
    sections.flatMap((section) => section.items),
  );
  if (current === null) return null;
  return (
    sections.find((section) => section.items.some((item) => item.href === current))?.id ?? null
  );
}

const themes: Record<SidebarTheme, { link: string; active: string; heading: string }> = {
  school: {
    link: "text-ink-muted hover:bg-surface-muted hover:text-ink",
    active: "bg-primary-soft font-medium text-primary",
    heading: "text-ink-muted",
  },
  platform: {
    link: "text-ink-muted hover:bg-platform-soft hover:text-platform",
    active: "bg-platform-soft font-medium text-platform",
    heading: "text-ink-muted",
  },
};

function ItemList({
  items,
  current,
  theme,
  labelledBy,
}: {
  items: readonly NavItem[];
  current: string | null;
  theme: SidebarTheme;
  labelledBy?: string;
}) {
  const styles = themes[theme];
  return (
    <ul className="space-y-0.5" aria-labelledby={labelledBy}>
      {items.map((item) => {
        const active = item.href === current;
        return (
          <li key={item.href} className={item.nested ? "ms-4" : undefined}>
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex min-h-9 items-center gap-2.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                active ? styles.active : styles.link,
              )}
            >
              {item.icon ? <Icon name={item.icon} className="size-4.5" /> : null}
              <span className="min-w-0">{item.label}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Primary navigation. The current page is marked with aria-current="page".
 * Pass `items` for a flat list, or `sections` for grouped lists with small grey headings
 * (each group is a list named by its heading). Exactly one item is current either way.
 */
export function SidebarNav({
  label,
  items = [],
  sections,
  theme = "school",
  className,
}: {
  label: string;
  items?: readonly NavItem[];
  sections?: readonly NavSection[];
  theme?: SidebarTheme;
  className?: string;
}) {
  const pathname = usePathname() ?? "";
  const baseId = useId();
  const all = sections ? sections.flatMap((section) => section.items) : items;
  const current = activeHref(pathname, all);
  return (
    <nav aria-label={label} data-print="hide" className={className}>
      {sections ? (
        <div className="space-y-5">
          {sections.map((section) => {
            const headingId = `${baseId}-${section.id}`;
            return (
              <div key={section.id}>
                <p id={headingId} className={cn("eyebrow mb-1.5 px-3", themes[theme].heading)}>
                  {section.label}
                </p>
                <ItemList
                  items={section.items}
                  current={current}
                  theme={theme}
                  labelledBy={headingId}
                />
              </div>
            );
          })}
        </div>
      ) : (
        <ItemList items={items} current={current} theme={theme} />
      )}
    </nav>
  );
}

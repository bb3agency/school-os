"use client";

import { useId, type ReactNode } from "react";
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
  /** Leading icon (name from `Icon`, serialisable); the compact sidebar shows only this. */
  icon?: IconName;
  /**
   * A nested sub-list under the item with its own links and actions (e.g. Ask the school's
   * "New chat" and recent chats). A client component element; hidden in the compact sidebar.
   */
  sub?: ReactNode;
  /**
   * Paths whose current page is marked inside `sub` (source text of a regular expression):
   * there the item itself is not the current page, so exactly one link is.
   */
  subActivePattern?: string;
}

/** A titled group of items in the sidebar ("RECORDS", "CHECKS"). */
export interface NavSection {
  id: string;
  label: string;
  items: readonly NavItem[];
}

export type SidebarTheme = "school" | "platform";

/** How well `item` matches `pathname`: -1 not at all; longer prefixes and patterns win. */
function matchScore(pathname: string, item: NavItem): number {
  if (item.subActivePattern && new RegExp(item.subActivePattern).test(pathname)) return -1;
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

/**
 * Item colours per theme (docs/17 §7): school on white, platform on the dark violet chrome.
 * The current page gets a tinted row, bolder text and a 3px accent bar at its start edge,
 * so it never depends on colour alone.
 */
const themes: Record<SidebarTheme, { link: string; active: string; bar: string; heading: string }> =
  {
    school: {
      link: "text-ink-muted hover:bg-surface-muted hover:text-ink",
      active: "bg-primary-soft font-semibold text-primary",
      bar: "bg-primary",
      heading: "text-ink-subtle",
    },
    platform: {
      link: "text-platform-muted hover:bg-platform-hover hover:text-platform-ink",
      active: "bg-platform-hover font-semibold text-platform-ink",
      bar: "bg-platform-accent",
      heading: "text-platform-muted",
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
        // Sub-entries without their own icon show a "corner" arrow, so the compact sidebar
        // still has something to point at.
        const icon: IconName | null = item.icon ?? (item.nested ? "cornerDownRight" : null);
        return (
          <li key={item.href} className={item.nested ? "ms-4 collapsed:ms-0" : undefined}>
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              data-tooltip={item.label}
              className={cn(
                // 40px rows (touch targets well over 24px, WCAG 2.5.8); labels wrap, never clip.
                "relative flex min-h-10 items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                "collapsed:justify-center collapsed:px-0",
                active ? styles.active : styles.link,
              )}
            >
              {active ? (
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute inset-y-2 start-0 w-[3px] rounded-full",
                    "collapsed:inset-y-2.5",
                    styles.bar,
                  )}
                />
              ) : null}
              {icon ? <Icon name={icon} className={item.nested ? "size-4" : "size-5"} /> : null}
              <span className="min-w-0 flex-1 break-words collapsed:sr-only">{item.label}</span>
            </Link>
            {item.sub ? <div className="collapsed:hidden">{item.sub}</div> : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The sidebar's navigation. The current page is marked with aria-current="page" and an
 * accent bar. Pass `items` for a flat list, or `sections` for grouped lists with small
 * headings (each group is a list named by its heading). Exactly one item is current either
 * way. In the compact sidebar (`collapsed:` variant) only the icons show; the labels stay
 * in the DOM as the links' accessible names, and headings become thin dividers.
 */
export function SidebarNav({
  label,
  items = [],
  sections,
  theme = "school",
  className,
  id,
}: {
  label: string;
  items?: readonly NavItem[];
  sections?: readonly NavSection[];
  theme?: SidebarTheme;
  className?: string;
  id?: string;
}) {
  const pathname = usePathname() ?? "";
  const baseId = useId();
  const all = sections ? sections.flatMap((section) => section.items) : items;
  const current = activeHref(pathname, all);
  return (
    <nav aria-label={label} id={id} data-print="hide" className={className}>
      {sections ? (
        <div className="space-y-5 collapsed:space-y-2">
          {sections.map((section, index) => {
            const headingId = `${baseId}-${section.id}`;
            return (
              <div
                key={section.id}
                className={cn(
                  index > 0 &&
                    "collapsed:border-t collapsed:pt-2 " +
                      (theme === "platform"
                        ? "collapsed:border-platform-hover"
                        : "collapsed:border-border"),
                )}
              >
                <p
                  id={headingId}
                  className={cn("eyebrow mb-1.5 px-3 collapsed:sr-only", themes[theme].heading)}
                >
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

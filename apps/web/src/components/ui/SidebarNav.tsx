"use client";

import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

export interface NavItem {
  href: string;
  label: string;
  /** Match only this exact path (for section roots such as "/" or "/platform"). */
  exact?: boolean;
}

export type SidebarTheme = "school" | "platform";

function isActive(pathname: string, item: NavItem): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
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
  return (
    <nav aria-label={label} data-print="hide">
      <ul className="space-y-1">
        {items.map((item) => {
          const active = isActive(pathname, item);
          return (
            <li key={item.href}>
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

import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

export interface TabNavItem {
  id: string;
  href: string;
  label: ReactNode;
}

/**
 * Tab-styled navigation where each tab is a URL (deep-linkable, works without JS).
 * Uses links + aria-current rather than ARIA tabs, because activating one loads a page.
 */
export function TabNav({
  label,
  items,
  activeId,
}: {
  label: string;
  items: readonly TabNavItem[];
  activeId: string;
}) {
  return (
    <nav aria-label={label} data-print="hide">
      <ul className="flex flex-wrap gap-1 border-b border-border">
        {items.map((item) => {
          const active = item.id === activeId;
          return (
            <li key={item.id}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px inline-block border-b-2 px-4 py-2 text-sm font-semibold",
                  active
                    ? "border-primary text-primary"
                    : "border-transparent text-ink-muted hover:text-ink",
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

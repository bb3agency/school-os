import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { tabStyles, type TabsVariant } from "./tab-styles";

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
  variant = "segmented",
}: {
  label: string;
  items: readonly TabNavItem[];
  activeId: string;
  /** `segmented` (default) or the older `underline` look. */
  variant?: TabsVariant;
}) {
  const styles = tabStyles(variant);
  return (
    <nav aria-label={label} data-print="hide">
      <ul className={styles.list}>
        {items.map((item) => {
          const active = item.id === activeId;
          return (
            <li key={item.id}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(styles.tab, active ? styles.active : styles.inactive)}
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

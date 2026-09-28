"use client";

import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { tabStyles, type TabsVariant } from "./tab-styles";

export type { TabsVariant } from "./tab-styles";

export interface TabItem {
  id: string;
  label: ReactNode;
  panel: ReactNode;
}

export interface TabsProps {
  /** Accessible name of the tab list. */
  label: string;
  items: readonly TabItem[];
  defaultTabId?: string;
  className?: string;
  /** `segmented` (default): light track with a white raised tab. `underline`: the older look. */
  variant?: TabsVariant;
}

/**
 * In-page tabs following the WAI-ARIA tabs pattern: arrow keys move between tabs,
 * Home/End jump, only the active tab is in the Tab order. Inactive panels stay in the
 * DOM (hidden) so form fields inside them keep their values and still submit.
 * For tabs that change the URL, use `TabNav` instead.
 */
export function Tabs({ label, items, defaultTabId, className, variant = "segmented" }: TabsProps) {
  const styles = tabStyles(variant);
  const baseId = useId();
  const [activeId, setActiveId] = useState(defaultTabId ?? items[0]?.id ?? "");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function focusTab(index: number) {
    const count = items.length;
    const next = ((index % count) + count) % count;
    const item = items[next];
    if (!item) return;
    setActiveId(item.id);
    tabRefs.current[next]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        focusTab(index + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        focusTab(index - 1);
        break;
      case "Home":
        event.preventDefault();
        focusTab(0);
        break;
      case "End":
        event.preventDefault();
        focusTab(items.length - 1);
        break;
      default:
        break;
    }
  }

  return (
    <div className={className}>
      <div role="tablist" aria-label={label} className={styles.list}>
        {items.map((item, index) => {
          const selected = item.id === activeId;
          return (
            <button
              key={item.id}
              ref={(element) => {
                tabRefs.current[index] = element;
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${item.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${item.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActiveId(item.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
              className={cn(styles.tab, selected ? styles.active : styles.inactive)}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {items.map((item) => (
        <div
          key={item.id}
          role="tabpanel"
          id={`${baseId}-panel-${item.id}`}
          aria-labelledby={`${baseId}-tab-${item.id}`}
          hidden={item.id !== activeId}
          tabIndex={0}
          className="pt-4"
        >
          {item.panel}
        </div>
      ))}
    </div>
  );
}

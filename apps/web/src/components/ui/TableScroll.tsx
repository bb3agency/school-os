"use client";

import { useTranslations } from "next-intl";
import { useMemo, useState, type ComponentProps } from "react";
import { cn } from "@/lib/cn";

const LANDMARK = "section, form, nav, aside, [role='region'], [role='search'], [role='group']";

const norm = (text: string | null | undefined) => (text ?? "").replace(/\s+/g, " ").trim();

/** The name an ancestor landmark takes from `aria-labelledby` or `aria-label`. */
function landmarkName(element: Element): string {
  const ids = element.getAttribute("aria-labelledby");
  if (ids) {
    return norm(
      ids
        .split(/\s+/)
        .map((id) => element.ownerDocument.getElementById(id)?.textContent ?? "")
        .join(" "),
    );
  }
  return norm(element.getAttribute("aria-label"));
}

/**
 * WA-03 (axe landmark-unique): a table's scroll region is a landmark named after its caption.
 * Inside a section (a `Card`, a page section) that already carries that name, the region gets
 * "{label} table, scrolls sideways" instead, so a landmark list never reads the same name
 * twice. The region keeps `tabIndex=0` (keyboard scrolling, WCAG 2.1.1). `element` comes from
 * a callback ref, so this is read once the node is mounted; the server render keeps the
 * caption name.
 */
export function useDistinctRegionName(element: HTMLElement | null, label: string): string | null {
  const t = useTranslations("ui.table");
  const duplicate = useMemo(() => {
    const own = norm(label);
    for (let node = element?.parentElement; node; node = node.parentElement) {
      if (node.matches(LANDMARK) && landmarkName(node) === own) return true;
    }
    return false;
  }, [element, label]);
  return duplicate ? t("scrollRegion", { label }) : null;
}

export interface TableScrollProps extends Omit<ComponentProps<"div">, "role" | "tabIndex" | "ref"> {
  /** Accessible name of the scroll region ("Students, table, scrolls sideways"). */
  label: string;
  /** Draw the white card frame (hairline border, radius, shadow) around the table. */
  framed?: boolean;
}

/**
 * Horizontal scroll box for a wide table: the table never widens the card or the page, and
 * keyboard users can focus the region and scroll it with the arrow keys (WCAG 2.1.1).
 * `position: relative` (`.table-scroll`) keeps sr-only text inside the box.
 */
export function TableScroll({ label, framed = false, className, ...props }: TableScrollProps) {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const distinct = useDistinctRegionName(node, label);
  return (
    <div
      ref={setNode}
      role="region"
      aria-label={distinct ?? label}
      tabIndex={0}
      className={cn(
        "table-scroll rounded-xl border border-border print:overflow-visible print:border-0",
        framed && "bg-surface shadow-card print:shadow-none",
        className,
      )}
      {...props}
    />
  );
}

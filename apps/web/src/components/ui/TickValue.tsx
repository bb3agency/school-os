"use client";

import { useState, type ReactNode } from "react";

/**
 * Wraps a displayed number (KpiCard, StatCard). When the number changes from one real value
 * to another (a refetch after an action), the new value rises 4px while fading in (200ms,
 * `.value-tick` in globals.css; still under reduced motion). The first value, and the
 * change from "—" to a number when data arrives, show at once: nothing animates on page load.
 */
export function TickValue({ value, children }: { value: string | null; children: ReactNode }) {
  const [seen, setSeen] = useState({ value, tick: 0 });
  if (seen.value !== value) {
    // Adjusting state while rendering (React's documented pattern for derived state).
    setSeen({
      value,
      tick: seen.value !== null && value !== null ? seen.tick + 1 : seen.tick,
    });
  }
  return (
    <span
      key={seen.tick}
      data-tick={seen.tick}
      className={seen.tick > 0 ? "value-tick inline-block" : undefined}
    >
      {children}
    </span>
  );
}

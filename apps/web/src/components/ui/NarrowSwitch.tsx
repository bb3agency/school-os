"use client";

import type { ReactNode } from "react";
import { NARROW_QUERY, useMediaQuery } from "@/lib/use-media-query";

/**
 * Shows `narrow` below 640px and `wide` from there (docs/17 §5.7: the stacked table rule).
 * Only one of them is ever in the DOM, so screen readers and `getByRole` see one list of rows.
 * The server and the hydrating render show `wide` (no hydration mismatch); a phone switches
 * in the first browser render, before the rows have usually loaded. Both are plain React
 * nodes, so a Server Component can render a `DataTable` too.
 */
export function NarrowSwitch({ wide, narrow }: { wide: ReactNode; narrow: ReactNode }) {
  const isNarrow = useMediaQuery(NARROW_QUERY);
  return <>{isNarrow ? narrow : wide}</>;
}

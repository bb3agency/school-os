"use client";

import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Skeleton → content (docs/17 §5.5). Render it around both states: while `loading`, the
 * children are the placeholder; once loaded, content that replaced a placeholder fades in
 * (200ms, `.content-in`; still under reduced motion). Content that was ready at first
 * render (cached data, server data) appears at once: nothing animates on page load.
 */
export function LoadFade({
  loading,
  children,
  className,
}: {
  loading: boolean;
  children: ReactNode;
  className?: string;
}) {
  const [waited, setWaited] = useState(loading);
  // Adjusting state while rendering (React's documented pattern for derived state).
  if (loading && !waited) setWaited(true);
  if (loading || !waited) return <>{children}</>;
  return <div className={cn("content-in min-w-0", className)}>{children}</div>;
}

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A key or shortcut as printed on the keyboard ("Ctrl", "K", "Esc"). */
export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "inline-flex min-w-5 items-center justify-center rounded-sm border border-border bg-surface px-1 font-sans text-xs leading-5 text-ink-muted",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

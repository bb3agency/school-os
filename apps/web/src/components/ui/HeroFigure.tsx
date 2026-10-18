import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

const SIZES = {
  // Phones get one step smaller, so a long figure (a receipt number, ₹12,34,567) stays on
  // screen at 360px (docs/17 §5.7).
  md: "text-3xl sm:text-4xl",
  lg: "text-4xl sm:text-5xl",
} as const;

/**
 * The one big figure of a page or card (docs/17 §3.1, §5): a muted label, then the value in
 * the figures font with tabular digits ("86%" ready for UDISE+, a saved receipt number, events
 * checked). Long values wrap anywhere instead of widening the page. `labelId` lets a wrapping
 * group take the label as its name.
 */
export function HeroFigure({
  label,
  value,
  labelId,
  size = "lg",
  children,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  labelId?: string;
  size?: keyof typeof SIZES;
  /** Under the figure: a bar, a hint, the rest of the record. */
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <p id={labelId} className="text-sm text-ink-muted">
        {label}
      </p>
      <p
        className={cn(
          "num mt-1 leading-none font-semibold break-anywhere text-ink tabular-nums",
          SIZES[size],
        )}
      >
        {value}
      </p>
      {children}
    </div>
  );
}

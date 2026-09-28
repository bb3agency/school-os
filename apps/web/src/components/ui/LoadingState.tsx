import { cn } from "@/lib/cn";

/**
 * Placeholder rows while data loads. Announced once via role="status". The shimmer is a
 * flat block when the user prefers reduced motion (globals.css `.skeleton`).
 */
export function LoadingState({
  label,
  rows = 3,
  variant = "rows",
  className,
}: {
  label: string;
  rows?: number;
  /** `rows`: list lines. `cards`: a grid of card-sized blocks (dashboards). */
  variant?: "rows" | "cards";
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        variant === "cards" ? "grid gap-4 sm:grid-cols-2 lg:grid-cols-3" : "space-y-2",
        className,
      )}
    >
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className={variant === "cards" ? "h-32 rounded-xl" : "h-6"} />
      ))}
    </div>
  );
}

/** One decorative placeholder block (size it with `className`). */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("skeleton rounded-sm motion-safe:animate-shimmer", className)}
    />
  );
}

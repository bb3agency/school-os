import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Empty states say what to do next (PRD §8). */
export function EmptyState({
  title,
  body,
  action,
  className,
}: {
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 rounded-md border border-dashed border-border px-6 py-10 text-center",
        className,
      )}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="size-8 text-ink-muted"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="M3 10h18" />
      </svg>
      <p className="font-semibold text-ink">{title}</p>
      {body ? <p className="max-w-prose text-sm text-ink-muted">{body}</p> : null}
      {action ? (
        <div className="mt-2" data-print="hide">
          {action}
        </div>
      ) : null}
    </div>
  );
}

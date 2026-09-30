import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

export type TimelineStatus = "done" | "current" | "pending";

export interface TimelineItem {
  id: string;
  title: ReactNode;
  /** Shown in mono ("10:42 IST", "18 Jun 2026"). Use a `<time>` element when you have one. */
  time?: ReactNode;
  body?: ReactNode;
  /** Small chips under the title (`<Pill>`s). */
  chips?: ReactNode;
  status?: TimelineStatus;
  /** Read before the title, e.g. "Done:" (the circle alone is not enough). */
  statusLabel?: string;
}

const markers: Record<TimelineStatus, string> = {
  done: "border-success bg-success text-white",
  current: "border-primary bg-surface text-primary",
  pending: "border-border-control bg-surface text-ink-muted",
};

/**
 * Vertical history ("Import started → Checked → Approved"): a line with check circles,
 * mono timestamps and optional chips. An ordered list, oldest first unless the caller
 * says otherwise in the heading.
 */
export function Timeline({
  items,
  label,
  className,
}: {
  items: readonly TimelineItem[];
  /** Accessible name of the list. */
  label: string;
  className?: string;
}) {
  return (
    <ol aria-label={label} className={cn("relative", className)}>
      {items.map((item, index) => {
        const status = item.status ?? "done";
        const last = index === items.length - 1;
        return (
          <li key={item.id} className="relative flex gap-4 pb-6 last:pb-0">
            {last ? null : (
              <span
                aria-hidden="true"
                className="absolute top-7 bottom-0 left-3.25 w-px bg-border-soft"
              />
            )}
            <span
              aria-hidden="true"
              className={cn(
                "relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border-2",
                markers[status],
              )}
            >
              {status === "done" ? (
                <Icon name="check" className="size-3.5" />
              ) : status === "current" ? (
                <span className="size-2 rounded-full bg-primary" />
              ) : null}
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <p className="font-semibold text-ink">
                  {item.statusLabel ? <span className="sr-only">{item.statusLabel} </span> : null}
                  {item.title}
                </p>
                {item.time ? (
                  <span className="font-mono text-xs text-ink-subtle">{item.time}</span>
                ) : null}
              </div>
              {item.body ? <div className="mt-1 text-sm text-ink-muted">{item.body}</div> : null}
              {item.chips ? <div className="mt-2 flex flex-wrap gap-1.5">{item.chips}</div> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

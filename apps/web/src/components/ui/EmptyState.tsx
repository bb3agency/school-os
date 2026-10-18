import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";
import { Illustration, type IllustrationName } from "./Illustration";

/**
 * Empty states say what to do next (PRD §8): a title, one line of help and one action.
 * `illustration` swaps the icon for a small decorative line drawing (docs/17 §5.6) on
 * first-use and session pages; keep the icon for no-results and in-table states, where a
 * picture would only push the rows down.
 */
export function EmptyState({
  title,
  body,
  action,
  icon = "inbox",
  illustration,
  className,
}: {
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  /** Decorative icon in the soft circle. */
  icon?: IconName;
  /** Decorative illustration shown instead of the icon (hidden in forced-colours mode). */
  illustration?: IllustrationName;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 rounded-xl border border-border bg-surface px-6 py-10 text-center",
        className,
      )}
    >
      {illustration ? (
        <Illustration name={illustration} className="mb-2 w-48 max-w-full sm:w-56" />
      ) : (
        <span className="mb-1 flex size-10 items-center justify-center rounded-lg bg-surface-muted text-ink-muted">
          <Icon name={icon} className="size-5" />
        </span>
      )}
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

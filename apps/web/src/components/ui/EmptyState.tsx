import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon, type IconName } from "./Icon";

/** Empty states say what to do next (PRD §8): a title, one line of help and one action. */
export function EmptyState({
  title,
  body,
  action,
  icon = "inbox",
  className,
}: {
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  /** Decorative icon in the soft circle. */
  icon?: IconName;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 rounded-xl border border-dashed border-border-soft bg-surface px-6 py-10 text-center",
        className,
      )}
    >
      <span className="mb-1 flex size-12 items-center justify-center rounded-full bg-primary-soft text-primary">
        <Icon name={icon} className="size-6" />
      </span>
      <p className="font-medium text-ink">{title}</p>
      {body ? <p className="max-w-prose text-sm text-ink-muted">{body}</p> : null}
      {action ? (
        <div className="mt-2" data-print="hide">
          {action}
        </div>
      ) : null}
    </div>
  );
}

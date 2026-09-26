import { useId, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface CardProps {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  /** Heading level for the title; pages use h1, so sections are h2 by default. */
  headingLevel?: 2 | 3;
}

export function Card({
  title,
  description,
  actions,
  children,
  className,
  headingLevel = 2,
}: CardProps) {
  const headingId = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={cn(
        "rounded-lg border border-border bg-surface p-5 print:border-0 print:p-0",
        className,
      )}
    >
      {title || actions ? (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            {title ? (
              <Heading id={headingId} className="text-lg font-semibold text-ink">
                {title}
              </Heading>
            ) : null}
            {description ? <p className="text-sm text-ink-muted">{description}</p> : null}
          </div>
          {actions ? (
            <div className="flex flex-wrap gap-2" data-print="hide">
              {actions}
            </div>
          ) : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

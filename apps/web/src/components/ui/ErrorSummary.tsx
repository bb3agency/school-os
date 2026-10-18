"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, type MouseEvent } from "react";
import { cn } from "@/lib/cn";

export interface SummaryError {
  /** The id of the field to fix (its `<input id>`). */
  fieldId: string;
  /** The same message as under the field: what is wrong and how to fix it. */
  message: string;
}

/**
 * GOV.UK-style error summary (docs/17 §4.4): shown at the top of a form after a submit that
 * failed validation, it takes focus (so screen readers read it and the page scrolls to it)
 * and lists every problem as a link that moves focus to that field. Each field still shows
 * its own message under it (`Field`). Render it only when there are errors, and re-render it
 * with a new `attempt` on every failed submit so focus moves again.
 */
export function ErrorSummary({
  errors,
  title,
  attempt = 0,
  className,
}: {
  errors: readonly SummaryError[];
  /** Defaults to "There is a problem". */
  title?: string;
  /** Increase on each failed submit so the summary takes focus again. */
  attempt?: number;
  className?: string;
}) {
  const t = useTranslations("ui.errorSummary");
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (errors.length > 0) ref.current?.focus();
  }, [attempt, errors.length]);

  if (errors.length === 0) return null;

  const jump = (event: MouseEvent<HTMLAnchorElement>, fieldId: string) => {
    const field = document.getElementById(fieldId);
    if (!field) return;
    event.preventDefault();
    // The label is above the field: bring it into view, then focus without a second jump.
    const label = "labels" in field ? (field as HTMLInputElement).labels?.[0] : null;
    (label ?? field).scrollIntoView?.({ block: "start" });
    field.focus({ preventScroll: true });
  };

  return (
    <div
      ref={ref}
      role="alert"
      tabIndex={-1}
      aria-labelledby={titleId}
      className={cn(
        "rounded-lg border-2 border-danger bg-surface p-4 focus:outline-3 focus:outline-offset-2 focus:outline-focus",
        className,
      )}
    >
      <h2 id={titleId} className="text-lg font-semibold text-ink">
        {title ?? t("title")}
      </h2>
      <ul className="mt-2 list-none space-y-1.5">
        {errors.map((error) => (
          <li key={error.fieldId}>
            <a
              href={`#${error.fieldId}`}
              onClick={(event) => jump(event, error.fieldId)}
              className="font-semibold text-danger underline underline-offset-4 hover:no-underline"
            >
              {error.message}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

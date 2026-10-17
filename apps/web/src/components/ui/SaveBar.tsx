import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

/**
 * Explicit save for official records, fees, settings and structure (docs/17 §4.4): a bar
 * stuck to the bottom of the form while it scrolls, saying whether there are changes not
 * saved yet, with the form's buttons (Save as the one primary, Discard secondary). Put it
 * as the last child of the `<form>` so Save stays a submit button. Never autosave official
 * records or maker-checker changes.
 */
export function SaveBar({
  dirty,
  children,
  savedLabel,
  className,
}: {
  /** There are changes not saved yet. */
  dirty: boolean;
  /** The form's buttons (Save, Discard). */
  children: ReactNode;
  /** Shown while nothing is unsaved, e.g. "Saved at 10:42"; nothing by default. */
  savedLabel?: ReactNode;
  className?: string;
}) {
  const t = useTranslations("ui.saveBar");
  return (
    <div
      data-print="hide"
      data-dirty={dirty ? "" : undefined}
      className={cn(
        "sticky bottom-0 z-10 -mx-4 mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border bg-surface px-4 py-3 sm:-mx-5 sm:px-5",
        "pb-[max(0.75rem,env(safe-area-inset-bottom))]",
        className,
      )}
    >
      <p role="status" className="flex min-w-0 items-center gap-2 text-sm">
        {dirty ? (
          <>
            <Icon name="alert" className="size-4 shrink-0 text-warning-ink" />
            <span className="font-semibold text-warning-ink">{t("unsaved")}</span>
          </>
        ) : savedLabel ? (
          <span className="text-ink-muted">{savedLabel}</span>
        ) : (
          <span className="text-ink-muted">{t("noChanges")}</span>
        )}
      </p>
      <div className="flex flex-wrap items-center gap-2 max-sm:w-full max-sm:[&>*]:flex-1">
        {children}
      </div>
    </div>
  );
}

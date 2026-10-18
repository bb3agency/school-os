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
 *
 * `inset` (default): the bar bleeds to the edges of the padded card it sits in. `page`: for a
 * form laid straight on the page (no card), a framed bar of its own with no negative margins.
 * On phones it stands on the bottom bar (and drops to the screen edge while the keyboard is
 * up), clear of the home indicator (`--dock-offset`, `--dock-safe`; docs/17 §5.7).
 */
export function SaveBar({
  dirty,
  children,
  savedLabel,
  placement = "inset",
  className,
}: {
  /** There are changes not saved yet. */
  dirty: boolean;
  /** The form's buttons (Save, Discard). */
  children: ReactNode;
  /** Shown while nothing is unsaved, e.g. "Saved at 10:42"; nothing by default. */
  savedLabel?: ReactNode;
  /** `inset`: inside a padded card (default). `page`: on the page, without a card. */
  placement?: "inset" | "page";
  className?: string;
}) {
  const t = useTranslations("ui.saveBar");
  return (
    <div
      data-print="hide"
      data-dirty={dirty ? "" : undefined}
      data-dock=""
      data-placement={placement}
      className={cn(
        "sticky bottom-[var(--dock-offset)] z-10 mt-6 flex flex-wrap items-center justify-between gap-3 bg-surface py-3",
        "pb-[max(0.75rem,var(--dock-safe))]",
        placement === "inset"
          ? "-mx-4 border-t border-border px-4 sm:-mx-5 sm:px-5"
          : "rounded-lg border border-border px-4 shadow-popover sm:px-5",
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

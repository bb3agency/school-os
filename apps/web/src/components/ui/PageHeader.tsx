import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { Eyebrow } from "./Eyebrow";
import { Icon } from "./Icon";

export interface Crumb {
  label: ReactNode;
  /** Omit for the current page (the last crumb). */
  href?: string;
}

/**
 * Trail of links to the pages above this one ("Home › Students › Sample student A").
 * The last crumb is the current page (`aria-current="page"`, not a link).
 */
export function Breadcrumb({ items, label }: { items: readonly Crumb[]; label?: string }) {
  const t = useTranslations("common");
  return (
    <nav aria-label={label ?? t("breadcrumb")} data-print="hide">
      <ol className="flex flex-wrap items-center gap-1 text-sm text-ink-muted">
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={index} className="inline-flex min-w-0 items-center gap-1 break-anywhere">
              {item.href && !last ? (
                <Link
                  href={item.href}
                  className="inline-flex min-h-6 items-center rounded-sm underline-offset-4 hover:text-ink hover:underline pointer-coarse:min-h-11"
                >
                  {item.label}
                </Link>
              ) : (
                <span aria-current={last ? "page" : undefined} className={last ? "text-ink" : ""}>
                  {item.label}
                </span>
              )}
              {last ? null : <Icon name="chevronRight" className="size-3.5 text-ink-muted" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * Top of every page: breadcrumb, eyebrow, the page's one `<h1>`, a description, a badge
 * and actions on the right. A white card by default (`plain` drops the card).
 */
export function PageHeader({
  title,
  description,
  actions,
  badge,
  breadcrumb,
  eyebrow,
  plain = false,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  badge?: ReactNode;
  breadcrumb?: readonly Crumb[];
  /** Mono uppercase label above the title. */
  eyebrow?: ReactNode;
  /** No card: for pages outside the shell (sign-in picker) or inside another card. */
  plain?: boolean;
  className?: string;
}) {
  return (
    <header
      className={cn(
        "mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-3 md:mb-6",
        !plain &&
          "rounded-xl border border-border bg-surface px-4 py-4 shadow-card sm:px-5 md:px-6 md:py-5 print:border-0 print:p-0 print:shadow-none",
        className,
      )}
    >
      {/* Title block takes the row; actions sit on its right when they fit, else below. */}
      <div className="min-w-0 max-w-3xl flex-[1_1_18rem] space-y-1.5">
        {breadcrumb && breadcrumb.length > 0 ? <Breadcrumb items={breadcrumb} /> : null}
        {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="min-w-0 text-xl font-semibold break-anywhere text-ink sm:text-2xl">
            {title}
          </h1>
          {badge}
        </div>
        {description ? <p className="text-ink-muted">{description}</p> : null}
      </div>
      {actions ? (
        <div className="flex max-w-full min-w-0 flex-wrap items-center gap-2" data-print="hide">
          {actions}
        </div>
      ) : null}
    </header>
  );
}

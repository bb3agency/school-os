import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { cn } from "@/lib/cn";
import { SkipLink } from "./SkipLink";

/** Round "S" mark + wordmark, shared by the pages outside the consoles. */
export function Wordmark({ className }: { className?: string }) {
  const t = useTranslations("common");
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span
        aria-hidden="true"
        className="flex size-9 items-center justify-center rounded-full bg-action font-display text-lg text-on-action"
      >
        S
      </span>
      <span className="text-lg font-semibold text-ink">{t("appName")}</span>
    </span>
  );
}

/**
 * Floating top bar + centred main on the gradient canvas, for pages outside a school
 * (school picker, "no access yet", signed-out, dev helpers). `wide` for tables.
 */
export function MinimalShell({
  children,
  headerActions,
  wide = false,
}: {
  children: ReactNode;
  headerActions?: ReactNode;
  wide?: boolean;
}) {
  const t = useTranslations("common");
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink label={t("skipToContent")} />
      <div className="px-3 pt-3 md:px-6 md:pt-5" data-print="hide">
        <header className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-surface px-4 py-2.5 shadow-card">
          <Wordmark />
          <div className="flex flex-wrap items-center justify-end gap-3">
            {headerActions}
            <LanguageSwitcher />
          </div>
        </header>
      </div>
      <main
        id="main"
        tabIndex={-1}
        className={cn(
          "mx-auto w-full flex-1 px-4 py-8 focus:outline-none md:px-6 md:py-12",
          wide ? "max-w-5xl" : "max-w-3xl",
        )}
      >
        {children}
      </main>
    </div>
  );
}

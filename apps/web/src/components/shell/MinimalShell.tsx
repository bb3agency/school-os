import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { cn } from "@/lib/cn";
import { BrandMark } from "./Brand";
import { SkipLink } from "./SkipLink";

/** Round "S" mark + wordmark (the same as the console sidebar's), for pages outside it. */
export function Wordmark({ className }: { className?: string }) {
  const t = useTranslations("common");
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <BrandMark />
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
    <div className="flex min-h-viewport flex-col">
      <SkipLink label={t("skipToContent")} />
      <div className="px-page pt-3 md:pt-5" data-print="hide">
        <header className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-border bg-surface px-3 py-2 shadow-card sm:px-4 sm:py-2.5">
          <Wordmark />
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 sm:gap-3">
            {headerActions}
            <LanguageSwitcher />
          </div>
        </header>
      </div>
      <main
        id="main"
        tabIndex={-1}
        className={cn(
          "px-page mx-auto w-full flex-1 py-6 focus:outline-none md:py-12",
          wide ? "max-w-5xl" : "max-w-3xl",
        )}
      >
        {children}
      </main>
    </div>
  );
}

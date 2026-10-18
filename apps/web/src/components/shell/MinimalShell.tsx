import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { cn } from "@/lib/cn";
import { Wordmark } from "./Brand";
import { SkipLink } from "./SkipLink";

/** The wordmark lives in Brand.tsx; re-exported for older imports. */
export { Wordmark };

/**
 * Pages outside a school (school picker, "no access yet", signed-out, step-up return, not
 * found, dev helpers): a plain 56px top bar on the canvas with a hairline under it (the same
 * language as the console's top bar, docs/17 §5.2; no floating card) and one centred column.
 * `wide` for tables.
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
      <header className="border-b border-border bg-canvas" data-print="hide">
        <div className="px-page mx-auto flex min-h-14 w-full max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2">
          <Wordmark />
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 sm:gap-3">
            {headerActions}
            <LanguageSwitcher />
          </div>
        </div>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className={cn(
          "px-page mx-auto w-full flex-1 py-8 focus:outline-none md:py-14",
          wide ? "max-w-5xl" : "max-w-3xl",
        )}
      >
        {children}
      </main>
    </div>
  );
}

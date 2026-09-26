import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";
import { SkipLink } from "./SkipLink";

/** Header + centred main for pages outside a school (picker, "no access yet"). */
export function MinimalShell({
  children,
  headerActions,
}: {
  children: ReactNode;
  headerActions?: ReactNode;
}) {
  const t = useTranslations("common");
  return (
    <div className="flex min-h-screen flex-col">
      <SkipLink label={t("skipToContent")} />
      <header
        className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3"
        data-print="hide"
      >
        <p className="text-lg font-bold text-primary">{t("appName")}</p>
        <div className="flex flex-wrap items-center justify-end gap-4">
          {headerActions}
          <LanguageSwitcher />
        </div>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-3xl flex-1 p-6 focus:outline-none"
      >
        {children}
      </main>
    </div>
  );
}

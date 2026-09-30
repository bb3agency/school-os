import "./marketing.css";
import { useTranslations } from "next-intl";
import { BrandMark } from "@/components/shell/Brand";
import { buttonClasses } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import { SIGN_IN_HREF } from "./links";

/**
 * /welcome on a dedicated host (SOS_DEPLOYMENT_MODE=dedicated): the host is one school's own
 * SchoolOS, so no marketing, only a calm branded sign-in (FR-IAM-001).
 */
export function DedicatedWelcome() {
  const t = useTranslations("marketing");
  const tc = useTranslations("common");
  return (
    <div className="mk mk-hero-bg relative flex min-h-viewport flex-col">
      <div aria-hidden="true" className="mk-grid pointer-events-none absolute inset-0" />
      <main
        id="main"
        tabIndex={-1}
        className="px-page relative flex flex-1 items-center justify-center py-16 focus:outline-none"
      >
        <div className="w-full max-w-md rounded-3xl border border-border bg-surface p-8 text-center shadow-popover md:p-10">
          <p className="inline-flex items-center gap-2.5">
            <BrandMark />
            <span className="text-lg font-semibold tracking-tight text-ink">{tc("appName")}</span>
          </p>
          <h1 className="mk-title mt-8 text-3xl font-normal text-ink">{t("dedicated.headline")}</h1>
          <p className="mt-3 text-ink-muted">{t("dedicated.body")}</p>
          <a
            href={SIGN_IN_HREF}
            className={cn(buttonClasses("primary", "lg"), "mk-press mt-8 w-full")}
          >
            {t("cta.signIn")}
            <Icon name="arrowRight" className="size-4.5" />
          </a>
          <p className="mt-6 text-sm text-ink-subtle">{t("dedicated.help")}</p>
        </div>
      </main>
    </div>
  );
}

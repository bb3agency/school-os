import "./marketing.css";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { BrandMark } from "@/components/shell/Brand";
import { SkipLink } from "@/components/shell/SkipLink";
import { Link } from "@/i18n/navigation";
import { MARKETING_PAGES, PAGE_HREF, SIGN_IN_HREF, mailtoHref, type MarketingPage } from "./links";
import type { MarketingSettings } from "./settings";
import { SiteHeader } from "./SiteHeader";
import { Container } from "./ui";

const FOOTER_LINK =
  "inline-flex min-h-11 items-center text-ink-muted underline-offset-4 hover:text-ink hover:underline sm:min-h-8";

/**
 * Shared layout of the public pages (docs/17 §5.5): skip link, sticky header, `<main id="main">`
 * and the footer with every page link. A Server Component; only the header is a client
 * component (scroll state and the phone menu).
 */
export function MarketingShell({
  current,
  settings,
  children,
}: {
  current: MarketingPage;
  settings: MarketingSettings;
  children: ReactNode;
}) {
  const t = useTranslations("marketing");
  const tc = useTranslations("common");
  return (
    <div className="mk flex min-h-viewport flex-col">
      <SkipLink label={tc("skipToContent")} />
      <SiteHeader current={current} contactEmail={settings.contactEmail} />
      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        {children}
      </main>

      <footer className="border-t border-border bg-surface">
        <Container className="grid gap-10 py-14 md:grid-cols-[1.4fr_1fr_1fr_1fr] md:py-16">
          <div className="max-w-sm">
            <p className="inline-flex items-center gap-2.5">
              <BrandMark />
              <span className="text-lg font-semibold tracking-tight text-ink">{tc("appName")}</span>
            </p>
            <p className="mt-4 text-ink-muted">{t("footer.tagline")}</p>
          </div>
          <nav aria-label={t("footer.product")} data-print="hide">
            <h2 className="eyebrow text-ink-subtle">{t("footer.product")}</h2>
            <ul className="mt-3 space-y-1">
              {MARKETING_PAGES.filter((page) => page !== "about").map((page) => (
                <li key={page}>
                  <Link href={PAGE_HREF[page]} className={FOOTER_LINK}>
                    {t(`nav.${page}`)}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <nav aria-label={t("footer.company")} data-print="hide">
            <h2 className="eyebrow text-ink-subtle">{t("footer.company")}</h2>
            <ul className="mt-3 space-y-1">
              <li>
                <Link href={PAGE_HREF.about} className={FOOTER_LINK}>
                  {t("nav.about")}
                </Link>
              </li>
              {settings.contactEmail ? (
                <li>
                  <a href={mailtoHref(settings.contactEmail)} className={FOOTER_LINK}>
                    {t("footer.contact")}
                  </a>
                </li>
              ) : null}
            </ul>
          </nav>
          <nav aria-label={t("footer.account")} data-print="hide">
            <h2 className="eyebrow text-ink-subtle">{t("footer.account")}</h2>
            <ul className="mt-3 space-y-1">
              <li>
                <a href={SIGN_IN_HREF} className={FOOTER_LINK}>
                  {t("cta.signIn")}
                </a>
              </li>
            </ul>
          </nav>
        </Container>
        <div className="border-t border-border">
          <Container className="flex flex-col gap-2 py-6 text-sm text-ink-subtle md:flex-row md:justify-between">
            <p>
              {settings.companyName ? (
                <>© {t("footer.legal", { company: settings.companyName })} </>
              ) : null}
              {t("footer.location")}
            </p>
            <p>{t("footer.sampleNote")}</p>
          </Container>
        </div>
      </footer>
    </div>
  );
}

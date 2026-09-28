import { useTranslations } from "next-intl";
import { SkipLink } from "@/components/shell/SkipLink";
import { buttonClasses } from "@/components/ui/Button";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";

/** In-page sections, in page order (ids of the <section> elements in WelcomeView). */
export const WELCOME_SECTIONS = ["features", "how", "security", "plans", "faq"] as const;

/** Staff sign-in: a plain link, the BFF route starts the OIDC redirect (as SignedOutView). */
export const SIGN_IN_HREF = "/bff/auth/login";

/**
 * Header of the public welcome page: a floating white bar on the canvas with the wordmark,
 * in-page navigation, language and "Sign in". Sticky from tablet width up; on phones it
 * scrolls away so it never covers half the screen.
 */
export function WelcomeHeader() {
  const t = useTranslations("welcome");
  const tc = useTranslations("common");
  return (
    <>
      <SkipLink label={tc("skipToContent")} />
      <header className="px-3 pt-3 md:sticky md:top-0 md:z-40 md:px-6 md:pt-4">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 rounded-xl border border-border bg-surface px-4 py-2.5 shadow-card print:border-0 print:shadow-none">
          <p>
            <a
              href="#top"
              className="inline-flex items-center gap-2.5 rounded-full text-lg font-semibold text-ink"
            >
              <span
                aria-hidden="true"
                className="flex size-9 items-center justify-center rounded-full bg-action font-display text-lg font-normal text-on-action"
              >
                S
              </span>
              {tc("appName")}
            </a>
          </p>
          <nav
            aria-label={t("nav.label")}
            className="order-last w-full md:order-none md:w-auto"
            data-print="hide"
          >
            <ul className="flex flex-wrap items-center gap-x-1 gap-y-1">
              {WELCOME_SECTIONS.map((section) => (
                <li key={section}>
                  <a
                    href={`#${section}`}
                    className="inline-flex min-h-9 items-center rounded-full px-3 text-sm font-medium text-ink-muted hover:bg-surface-muted hover:text-ink"
                  >
                    {t(`nav.${section}`)}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <div className="flex items-center gap-3" data-print="hide">
            <LanguageSwitcher />
            <a href={SIGN_IN_HREF} className={buttonClasses("primary")}>
              {t("signIn")}
            </a>
          </div>
        </div>
      </header>
    </>
  );
}

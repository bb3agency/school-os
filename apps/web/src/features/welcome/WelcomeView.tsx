import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import { HeroVisual } from "./HeroVisual";
import { SIGN_IN_HREF, WelcomeHeader } from "./WelcomeHeader";

const FEATURES = ["import", "checks", "changes", "certificates", "ask", "audit"] as const;
/** Not built yet (M3, docs/14): shown with a "Planned" badge, never as available. */
const PLANNED = new Set<(typeof FEATURES)[number]>(["certificates"]);
const STEPS = ["import", "check", "submit"] as const;
const SECURITY = ["india", "isolation", "aadhaar", "audit", "ai", "support"] as const;
const PLANS = ["shared", "dedicated"] as const;
const PLAN_POINTS = ["point1", "point2", "point3"] as const;
const FAQ = ["portal", "register", "tools", "ai", "training", "exit", "language"] as const;

/** Sticky header height (md and up) plus a little air, so anchored headings are not hidden. */
const SECTION = "scroll-mt-24 px-4 py-14 md:px-6 md:py-20 print:py-6";

function SectionHeading({ id, title, intro }: { id: string; title: string; intro?: string }) {
  return (
    <div className="mx-auto mb-10 max-w-2xl text-center print:mb-4 print:text-left">
      <h2 id={id} className="text-2xl font-bold text-ink md:text-3xl">
        {title}
      </h2>
      {intro ? <p className="mt-3 text-ink-muted">{intro}</p> : null}
    </div>
  );
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 20 20" className="size-5" fill="currentColor" aria-hidden="true">
      <path d="M10 1.5 3 4.25v5.1c0 4.1 2.95 7.9 7 9.15 4.05-1.25 7-5.05 7-9.15v-5.1L10 1.5Zm3.2 6.3-3.8 4.6a.8.8 0 0 1-1.2.05L6.3 10.6a.8.8 0 1 1 1.15-1.1l1.3 1.35 3.2-3.9a.8.8 0 0 1 1.25 1Z" />
    </svg>
  );
}

function Card({ children }: { children: ReactNode }) {
  return (
    <div className="h-full rounded-lg border border-border bg-surface p-6 print:break-inside-avoid print:border-black print:p-3">
      {children}
    </div>
  );
}

/**
 * Public product page (no session): what SchoolOS does, how, how it keeps data safe, the
 * two tiers and common questions. Honest content only: no prices, customer names, counts
 * or ratings, and every claim is documented in docs/01, docs/07 and docs/08.
 */
export function WelcomeView() {
  const t = useTranslations("welcome");
  const tc = useTranslations("common");

  return (
    <div id="top" className="flex min-h-screen flex-col">
      <WelcomeHeader />
      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        {/* Hero */}
        <section
          aria-labelledby="welcome-title"
          className="border-b border-border bg-linear-to-b from-primary-soft to-canvas px-4 py-10 md:px-6 md:py-12 print:bg-none"
        >
          <div className="mx-auto grid w-full max-w-6xl items-center gap-10 lg:grid-cols-[1.1fr_1fr]">
            <div>
              <p className="text-sm font-semibold text-primary">{t("hero.eyebrow")}</p>
              <h1
                id="welcome-title"
                className="mt-3 text-3xl leading-tight font-bold text-balance text-ink md:text-4xl xl:text-5xl"
              >
                {t("hero.headline")}
              </h1>
              <p className="mt-5 max-w-xl text-lg text-ink-muted">{t("hero.body")}</p>
              <div className="mt-8 flex flex-wrap gap-3" data-print="hide">
                <a href={SIGN_IN_HREF} className={buttonClasses("primary", "lg")}>
                  {t("hero.primary")}
                </a>
                <a href="#how" className={buttonClasses("secondary", "lg")}>
                  {t("hero.secondary")}
                </a>
              </div>
              <p className="mt-4 text-sm text-ink-muted">{t("hero.note")}</p>
            </div>
            <HeroVisual />
          </div>
        </section>

        {/* Features */}
        <section id="features" aria-labelledby="features-title" className={SECTION}>
          <SectionHeading
            id="features-title"
            title={t("features.title")}
            intro={t("features.intro")}
          />
          <ul className="mx-auto grid w-full max-w-6xl gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature) => (
              <li key={feature}>
                <Card>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-lg font-semibold text-ink">
                      {t(`features.${feature}.title`)}
                    </h3>
                    {PLANNED.has(feature) ? (
                      <Badge tone="info">{t("features.planned")}</Badge>
                    ) : null}
                  </div>
                  <p className="mt-2 text-ink-muted">{t(`features.${feature}.body`)}</p>
                </Card>
              </li>
            ))}
          </ul>
        </section>

        {/* How it works */}
        <section
          id="how"
          aria-labelledby="how-title"
          className={`${SECTION} border-y border-border bg-surface`}
        >
          <SectionHeading id="how-title" title={t("how.title")} intro={t("how.intro")} />
          <ol className="mx-auto grid w-full max-w-6xl gap-8 md:grid-cols-3">
            {STEPS.map((step, index) => (
              <li key={step} className="flex gap-4 print:break-inside-avoid">
                <span
                  aria-hidden="true"
                  className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-lg font-bold text-on-primary print:border print:border-black"
                >
                  {index + 1}
                </span>
                <div>
                  <p className="text-sm font-semibold text-primary">
                    {t("how.step", { number: index + 1 })}
                  </p>
                  <h3 className="text-lg font-semibold text-ink">{t(`how.${step}.title`)}</h3>
                  <p className="mt-2 text-ink-muted">{t(`how.${step}.body`)}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* Security and privacy */}
        <section id="security" aria-labelledby="security-title" className={SECTION}>
          <SectionHeading
            id="security-title"
            title={t("security.title")}
            intro={t("security.intro")}
          />
          <ul className="mx-auto grid w-full max-w-6xl gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {SECURITY.map((item) => (
              <li key={item} className="flex gap-3 print:break-inside-avoid">
                <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-success-soft text-success-ink">
                  <ShieldIcon />
                </span>
                <div>
                  <h3 className="font-semibold text-ink">{t(`security.${item}.title`)}</h3>
                  <p className="mt-1 text-ink-muted">{t(`security.${item}.body`)}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* Plans */}
        <section
          id="plans"
          aria-labelledby="plans-title"
          className={`${SECTION} border-y border-border bg-surface`}
        >
          <SectionHeading id="plans-title" title={t("plans.title")} intro={t("plans.intro")} />
          <ul className="mx-auto grid w-full max-w-4xl gap-5 md:grid-cols-2">
            {PLANS.map((plan) => (
              <li key={plan}>
                <div className="h-full rounded-lg border border-border-strong bg-canvas p-6 print:break-inside-avoid print:border-black">
                  <h3 className="text-xl font-bold text-ink">{t(`plans.${plan}.name`)}</h3>
                  <p className="mt-2 text-ink-muted">{t(`plans.${plan}.summary`)}</p>
                  <ul className="mt-4 space-y-2">
                    {PLAN_POINTS.map((point) => (
                      <li key={point} className="flex gap-2 text-ink">
                        <svg
                          viewBox="0 0 20 20"
                          className="mt-1 size-4 shrink-0 text-success-ink"
                          fill="currentColor"
                          aria-hidden="true"
                        >
                          <path d="M16.7 5.3a1 1 0 0 1 0 1.4l-8 8a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.4l3.3 3.29 7.3-7.3a1 1 0 0 1 1.4 0Z" />
                        </svg>
                        <span>{t(`plans.${plan}.${point}`)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* FAQ: native disclosures (keyboard and find-in-page work without JS). */}
        <section id="faq" aria-labelledby="faq-title" className={SECTION}>
          <SectionHeading id="faq-title" title={t("faq.title")} />
          <div className="mx-auto w-full max-w-3xl space-y-3">
            {FAQ.map((item) => (
              <details
                key={item}
                className="group rounded-lg border border-border bg-surface print:break-inside-avoid"
              >
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-lg px-5 py-4 font-semibold text-ink hover:bg-surface-muted [&::-webkit-details-marker]:hidden">
                  {t(`faq.${item}.q`)}
                  <svg
                    viewBox="0 0 20 20"
                    className="size-5 shrink-0 text-primary transition-transform group-open:rotate-180 print:hidden"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path d="M5.3 7.3a1 1 0 0 1 1.4 0L10 10.58l3.3-3.3a1 1 0 1 1 1.4 1.42l-4 4a1 1 0 0 1-1.4 0l-4-4a1 1 0 0 1 0-1.42Z" />
                  </svg>
                </summary>
                <p className="px-5 pb-5 text-ink-muted">{t(`faq.${item}.a`)}</p>
              </details>
            ))}
          </div>
        </section>

        {/* Final call to action */}
        <section
          aria-labelledby="cta-title"
          className="border-t border-border bg-primary-soft px-4 py-14 md:px-6"
          data-print="hide"
        >
          <div className="mx-auto max-w-2xl text-center">
            <h2 id="cta-title" className="text-2xl font-bold text-ink md:text-3xl">
              {t("cta.title")}
            </h2>
            <p className="mt-3 text-ink-muted">{t("cta.body")}</p>
            <a href={SIGN_IN_HREF} className={`${buttonClasses("primary", "lg")} mt-6`}>
              {t("cta.button")}
            </a>
          </div>
        </section>
      </main>

      <footer className="border-t border-border bg-surface px-4 py-8 md:px-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 text-sm text-ink-muted md:flex-row md:items-start md:justify-between">
          <div className="space-y-1">
            <p className="font-bold text-primary">{tc("appName")}</p>
            <p>{t("footer.tagline")}</p>
            <p>{t("footer.location")}</p>
            <p>{t("footer.sampleNote")}</p>
          </div>
          <ul className="flex flex-wrap gap-4" data-print="hide">
            <li>
              <a href={SIGN_IN_HREF} className="font-semibold text-primary underline">
                {t("footer.signIn")}
              </a>
            </li>
            <li>
              <a href="#top" className="font-semibold text-primary underline">
                {t("footer.top")}
              </a>
            </li>
          </ul>
        </div>
      </footer>
    </div>
  );
}

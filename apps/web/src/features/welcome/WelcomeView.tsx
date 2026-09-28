import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Badge, Pill } from "@/components/ui/Badge";
import { buttonClasses } from "@/components/ui/Button";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Icon, type IconName } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import { HeroVisual } from "./HeroVisual";
import { SIGN_IN_HREF, WelcomeHeader } from "./WelcomeHeader";

const FEATURES = ["import", "checks", "changes", "certificates", "ask", "audit"] as const;
/** Not built yet (M3, docs/14): shown with a "Planned" badge, never as available. */
const PLANNED = new Set<(typeof FEATURES)[number]>(["certificates"]);
const FEATURE_ICONS: Record<(typeof FEATURES)[number], IconName> = {
  import: "upload",
  checks: "shieldCheck",
  changes: "clipboard",
  certificates: "file",
  ask: "sparkles",
  audit: "activity",
};
const STEPS = ["import", "check", "submit"] as const;
const SECURITY = ["india", "isolation", "aadhaar", "audit", "ai", "support"] as const;
const SECURITY_ICONS: Record<(typeof SECURITY)[number], IconName> = {
  india: "globe",
  isolation: "lock",
  aadhaar: "shieldCheck",
  audit: "activity",
  ai: "sparkles",
  support: "key",
};
const PLANS = ["shared", "dedicated"] as const;
const PLAN_POINTS = ["point1", "point2", "point3"] as const;
const FAQ = ["portal", "register", "tools", "ai", "training", "exit", "language"] as const;

/** Sticky header height (md and up) plus a little air, so anchored headings are not hidden. */
const SECTION = "scroll-mt-28 px-4 py-14 md:px-6 md:py-20 print:py-6";

/** Card shape on the canvas; prints as a plain bordered box. */
const CARD_SHAPE =
  "h-full rounded-xl border p-6 shadow-card print:break-inside-avoid print:border-black print:bg-white print:p-3 print:shadow-none";
/** White card (`cn` does not merge classes, so colours are never overridden). */
const CARD = `${CARD_SHAPE} border-border bg-surface`;

function SectionHeading({
  id,
  eyebrow,
  title,
  intro,
}: {
  id: string;
  eyebrow: string;
  title: string;
  intro?: string;
}) {
  return (
    <div className="mx-auto mb-10 max-w-2xl text-center print:mb-4 print:text-left">
      {/* Same words as the section's link in the header: decorative repetition. */}
      <Eyebrow as="div" className="mb-3" tone="brand">
        <span aria-hidden="true">{eyebrow}</span>
      </Eyebrow>
      <h2 id={id} className="text-2xl font-semibold text-balance text-ink md:text-3xl">
        {title}
      </h2>
      {intro ? <p className="mt-3 text-ink-muted">{intro}</p> : null}
    </div>
  );
}

function IconCircle({ name, tone = "brand" }: { name: IconName; tone?: "brand" | "success" }) {
  return (
    <span
      className={cn(
        "flex size-11 shrink-0 items-center justify-center rounded-full",
        tone === "brand" ? "bg-primary-soft text-primary" : "bg-success-soft text-success-ink",
      )}
    >
      <Icon name={name} className="size-5" />
    </span>
  );
}

function Check({ className }: { className?: string }) {
  return <Icon name="checkCircle" className={cn("mt-0.5 size-5", className)} />;
}

function Container({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("mx-auto w-full max-w-6xl", className)}>{children}</div>;
}

/**
 * Public product page (no session): what SchoolOS does, how, how it keeps data safe, the
 * two tiers and common questions. Honest content only: no prices, customer names, counts
 * or ratings, and every claim is documented in docs/01, docs/07 and docs/08. The serif
 * display face is used for step numbers only, never for statistics.
 */
export function WelcomeView() {
  const t = useTranslations("welcome");
  const tc = useTranslations("common");

  return (
    <div id="top" className="flex min-h-screen flex-col">
      <WelcomeHeader />
      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        {/* Hero on the gradient canvas */}
        <section
          aria-labelledby="welcome-title"
          className="px-4 pt-8 pb-12 md:px-6 md:pt-10 md:pb-16"
        >
          <Container className="grid items-center gap-10 lg:grid-cols-[1.1fr_1fr]">
            <div>
              <Pill variant="date" size="md">
                <Icon name="sparkles" className="size-4" />
                {t("hero.eyebrow")}
              </Pill>
              <h1
                id="welcome-title"
                className="mt-4 text-3xl leading-tight font-semibold text-balance text-ink md:text-4xl xl:text-5xl"
              >
                {t("hero.headline")}
              </h1>
              <p className="mt-5 max-w-xl text-lg text-ink-muted">{t("hero.body")}</p>
              <div className="mt-8 flex flex-wrap gap-3" data-print="hide">
                <a href={SIGN_IN_HREF} className={buttonClasses("primary", "lg")}>
                  {t("hero.primary")}
                  <Icon name="arrowRight" className="size-4.5" />
                </a>
                <a href="#how" className={buttonClasses("secondary", "lg")}>
                  {t("hero.secondary")}
                </a>
              </div>
              <p className="mt-4 text-sm text-ink-muted">{t("hero.note")}</p>
            </div>
            <HeroVisual />
          </Container>
        </section>

        {/* Features */}
        <section id="features" aria-labelledby="features-title" className={SECTION}>
          <SectionHeading
            id="features-title"
            eyebrow={t("nav.features")}
            title={t("features.title")}
            intro={t("features.intro")}
          />
          <Container>
            <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((feature) => (
                <li key={feature}>
                  <div className={CARD}>
                    <div className="mb-4 flex items-start justify-between gap-3">
                      <IconCircle name={FEATURE_ICONS[feature]} />
                      {PLANNED.has(feature) ? (
                        <Badge tone="info">{t("features.planned")}</Badge>
                      ) : null}
                    </div>
                    <h3 className="text-lg font-medium text-ink">
                      {t(`features.${feature}.title`)}
                    </h3>
                    <p className="mt-2 text-ink-muted">{t(`features.${feature}.body`)}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Container>
        </section>

        {/* How it works */}
        <section id="how" aria-labelledby="how-title" className={SECTION}>
          <SectionHeading
            id="how-title"
            eyebrow={t("nav.how")}
            title={t("how.title")}
            intro={t("how.intro")}
          />
          <Container>
            <ol className="grid gap-5 md:grid-cols-3">
              {STEPS.map((step, index) => (
                <li key={step} className={CARD}>
                  <span
                    aria-hidden="true"
                    className="block font-display text-6xl leading-none text-primary"
                  >
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <Eyebrow className="mt-4">{t("how.step", { number: index + 1 })}</Eyebrow>
                  <h3 className="mt-1 text-lg font-medium text-ink">{t(`how.${step}.title`)}</h3>
                  <p className="mt-2 text-ink-muted">{t(`how.${step}.body`)}</p>
                </li>
              ))}
            </ol>
          </Container>
        </section>

        {/* Security and privacy */}
        <section id="security" aria-labelledby="security-title" className={SECTION}>
          <SectionHeading
            id="security-title"
            eyebrow={t("nav.security")}
            title={t("security.title")}
            intro={t("security.intro")}
          />
          <Container>
            <ul className="grid gap-x-8 gap-y-8 rounded-xl border border-border bg-surface p-6 shadow-card sm:grid-cols-2 md:p-8 lg:grid-cols-3 print:border-black print:shadow-none">
              {SECURITY.map((item) => (
                <li key={item} className="flex gap-4 print:break-inside-avoid">
                  <IconCircle name={SECURITY_ICONS[item]} tone="success" />
                  <div>
                    <h3 className="font-medium text-ink">{t(`security.${item}.title`)}</h3>
                    <p className="mt-1 text-ink-muted">{t(`security.${item}.body`)}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Container>
        </section>

        {/* Plans: the second one on the near-black card (white text 17.7:1). */}
        <section id="plans" aria-labelledby="plans-title" className={SECTION}>
          <SectionHeading
            id="plans-title"
            eyebrow={t("nav.plans")}
            title={t("plans.title")}
            intro={t("plans.intro")}
          />
          <Container className="max-w-4xl">
            <ul className="grid gap-5 md:grid-cols-2">
              {PLANS.map((plan) => {
                const dark = plan === "dedicated";
                return (
                  <li key={plan}>
                    <div
                      className={cn(
                        CARD_SHAPE,
                        dark
                          ? "border-action bg-action text-on-action"
                          : "border-border bg-surface",
                      )}
                    >
                      <h3
                        className={cn(
                          "text-xl font-semibold",
                          dark ? "text-on-action print:text-black" : "text-ink",
                        )}
                      >
                        {t(`plans.${plan}.name`)}
                      </h3>
                      <p
                        className={cn(
                          "mt-2",
                          dark ? "text-on-action print:text-black" : "text-ink-muted",
                        )}
                      >
                        {t(`plans.${plan}.summary`)}
                      </p>
                      <ul className="mt-5 space-y-2.5">
                        {PLAN_POINTS.map((point) => (
                          <li
                            key={point}
                            className={cn(
                              "flex gap-2",
                              dark ? "text-on-action print:text-black" : "text-ink",
                            )}
                          >
                            <Check className={dark ? "text-positive-border" : "text-success-ink"} />
                            <span>{t(`plans.${plan}.${point}`)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Container>
        </section>

        {/* FAQ: native disclosures (keyboard and find-in-page work without JS). */}
        <section id="faq" aria-labelledby="faq-title" className={SECTION}>
          <SectionHeading id="faq-title" eyebrow={t("nav.faq")} title={t("faq.title")} />
          <div className="mx-auto w-full max-w-3xl space-y-3">
            {FAQ.map((item) => (
              <details
                key={item}
                className="group rounded-xl border border-border bg-surface shadow-card print:break-inside-avoid print:shadow-none"
              >
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-xl px-5 py-4 font-medium text-ink hover:bg-surface-hover [&::-webkit-details-marker]:hidden">
                  {t(`faq.${item}.q`)}
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border-soft text-ink print:hidden">
                    <Icon
                      name="chevronDown"
                      className="size-4 transition-transform group-open:rotate-180"
                    />
                  </span>
                </summary>
                <p className="px-5 pb-5 text-ink-muted">{t(`faq.${item}.a`)}</p>
              </details>
            ))}
          </div>
        </section>

        {/* Final call to action: blue gradient card (white text ≥ 5.2:1). */}
        <section aria-labelledby="cta-title" className="px-4 pb-14 md:px-6" data-print="hide">
          <Container className="max-w-4xl">
            <div className="ai-gradient ai-chrome rounded-xl border border-transparent px-6 py-12 text-center text-white shadow-card">
              <h2 id="cta-title" className="text-2xl font-semibold md:text-3xl">
                {t("cta.title")}
              </h2>
              <p className="mx-auto mt-3 max-w-xl text-white">{t("cta.body")}</p>
              <a href={SIGN_IN_HREF} className={cn(buttonClasses("inverse", "lg"), "mt-6")}>
                {t("cta.button")}
              </a>
            </div>
          </Container>
        </section>
      </main>

      <footer className="px-3 pb-4 md:px-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 rounded-xl border border-border bg-surface px-5 py-6 text-sm text-ink-muted shadow-card md:flex-row md:items-start md:justify-between print:border-0 print:shadow-none">
          <div className="space-y-1">
            <p className="font-semibold text-ink">{tc("appName")}</p>
            <p>{t("footer.tagline")}</p>
            <p>{t("footer.location")}</p>
            <p>{t("footer.sampleNote")}</p>
          </div>
          <ul className="flex flex-wrap gap-4" data-print="hide">
            <li>
              <a href={SIGN_IN_HREF} className="font-medium text-primary underline">
                {t("footer.signIn")}
              </a>
            </li>
            <li>
              <a href="#top" className="font-medium text-primary underline">
                {t("footer.top")}
              </a>
            </li>
          </ul>
        </div>
      </footer>
    </div>
  );
}

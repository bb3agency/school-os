import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { FEATURE_SECTIONS, PLANNED_FEATURES, type FeatureSection } from "./links";
import { MarketingShell } from "./MarketingShell";
import {
  AskCard,
  AuditCard,
  CertificateCard,
  ChangeRequestCard,
  FindingCard,
  ImportCard,
  Mock,
} from "./mockups";
import { Reveal } from "./Reveal";
import type { MarketingSettings } from "./settings";
import { CheckList, Container, CtaGroup, PageHero, PlannedBadge } from "./ui";

const POINTS = ["one", "two", "three"] as const;

/** The illustration of each feature section (sample data, described for screen readers). */
function Illustration({ feature }: { feature: FeatureSection }): ReactNode {
  switch (feature) {
    case "records":
      return (
        <div className="grid gap-4">
          <ImportCard className="mk-float" />
          <FindingCard className="mk-float sm:ms-8" />
        </div>
      );
    case "checks":
      return <FindingCard flag className="mk-float" />;
    case "corrections":
      return <ChangeRequestCard className="mk-float" />;
    case "ask":
      return <AskCard notFound className="mk-float" />;
    case "audit":
      return <AuditCard className="mk-float" />;
    case "certificates":
      return <CertificateCard className="mk-float" />;
  }
}

/**
 * /features: one page with an anchored section per capability (docs/17 §5.6), each with its
 * illustrated mockup, alternating sides on wide screens. Certificates are marked Planned.
 */
export function FeaturesView({ settings }: { settings: MarketingSettings }) {
  const t = useTranslations("marketing");
  return (
    <MarketingShell current="features" settings={settings}>
      <PageHero
        id="features-title"
        eyebrow={t("features.eyebrow")}
        title={t("features.headline")}
        intro={t("features.intro")}
      >
        <nav aria-label={t("features.jumpLabel")} className="mt-10" data-print="hide">
          <ul className="flex flex-wrap gap-2">
            {FEATURE_SECTIONS.map((feature) => (
              <li key={feature}>
                <a
                  href={`#${feature}`}
                  className="mk-press inline-flex min-h-11 items-center gap-2 rounded-full border border-border bg-surface px-4 text-sm font-medium text-ink hover:border-border-soft hover:bg-surface-muted"
                >
                  {t(`features.${feature}.nav`)}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </PageHero>

      {FEATURE_SECTIONS.map((feature, index) => (
        <section
          key={feature}
          id={feature}
          aria-labelledby={`${feature}-title`}
          className={cn(
            "scroll-mt-20 py-16 md:py-24 print:py-6",
            index % 2 === 0 ? "border-t border-border bg-surface" : "border-t border-border",
          )}
        >
          <Container className="grid items-center gap-10 lg:grid-cols-2 lg:gap-20">
            <div className={cn(index % 2 === 1 && "lg:order-2")}>
              <div className="flex flex-wrap items-center gap-3">
                <p className="eyebrow text-primary">
                  {String(index + 1).padStart(2, "0")} · {t(`features.${feature}.nav`)}
                </p>
                {PLANNED_FEATURES.has(feature) ? <PlannedBadge label={t("planned")} /> : null}
              </div>
              <h2
                id={`${feature}-title`}
                className="mk-title mt-4 text-3xl font-normal text-ink md:text-4xl"
              >
                {t(`features.${feature}.title`)}
              </h2>
              <p className="mk-lede mt-5 text-lg text-ink-muted">{t(`features.${feature}.body`)}</p>
              <div className="mt-8">
                <CheckList
                  items={POINTS.map((point) => t(`features.${feature}.points.${point}`))}
                />
              </div>
            </div>
            <Reveal className={cn("mx-auto w-full max-w-md", index % 2 === 1 && "lg:order-1")}>
              <Mock caption={`${t("mock.label")}: ${t(`features.${feature}.title`)}`}>
                <div
                  className={cn(
                    "rounded-3xl p-5 sm:p-8",
                    index % 2 === 0 ? "bg-canvas" : "bg-surface",
                  )}
                >
                  <Illustration feature={feature} />
                </div>
              </Mock>
            </Reveal>
          </Container>
        </section>
      ))}

      <section
        aria-labelledby="features-cta"
        className="border-t border-border py-20 md:py-24"
        data-print="hide"
      >
        <Container className="flex flex-col items-start gap-6 md:flex-row md:items-center md:justify-between">
          <h2 id="features-cta" className="mk-title max-w-xl text-3xl font-normal text-ink">
            {t("home.closing.title")}
          </h2>
          <CtaGroup
            contactEmail={settings.contactEmail}
            talkLabel={t("cta.talk")}
            signInLabel={t("cta.signIn")}
          />
        </Container>
      </section>
    </MarketingShell>
  );
}

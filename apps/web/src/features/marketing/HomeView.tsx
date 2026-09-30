import { useTranslations } from "next-intl";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { FEATURE_SECTIONS, PAGE_HREF, PLANNED_FEATURES, type FeatureSection } from "./links";
import { MarketingShell } from "./MarketingShell";
import {
  AskCard,
  AuditCard,
  ChangeRequestCard,
  FindingCard,
  HeroComposition,
  ImportCard,
  CertificateCard,
} from "./mockups";
import { PlanCards } from "./PlanCards";
import { Reveal } from "./Reveal";
import type { MarketingSettings } from "./settings";
import { ArrowLink, Container, CtaGroup, PlannedBadge, Section, SectionIntro } from "./ui";

const SOURCES = ["register", "aadhaar", "udise", "board"] as const;
const SOURCE_ICONS: Record<(typeof SOURCES)[number], IconName> = {
  register: "folder",
  aadhaar: "shieldCheck",
  udise: "globe",
  board: "building",
};
const STEPS = ["import", "check", "submit"] as const;
const SECURITY_POINTS = ["india", "isolation", "aadhaar", "ai", "support", "audit"] as const;
const SECURITY_ICONS: Record<(typeof SECURITY_POINTS)[number], IconName> = {
  india: "globe",
  isolation: "layers",
  aadhaar: "shieldCheck",
  ai: "sparkles",
  support: "key",
  audit: "activity",
};
const FAQ = ["portal", "register", "tools", "training", "exit"] as const;

/** Small vignette per feature tile (a slice of the real mock, scaled to the tile). */
function Vignette({ feature }: { feature: FeatureSection }) {
  switch (feature) {
    case "records":
      return <ImportCard className="shadow-card" />;
    case "checks":
      return <FindingCard className="shadow-card" />;
    case "corrections":
      return <ChangeRequestCard className="shadow-card" />;
    case "ask":
      return <AskCard />;
    case "audit":
      return <AuditCard className="shadow-card" />;
    case "certificates":
      return <CertificateCard className="shadow-card" />;
  }
}

/**
 * Public home page (/welcome; FR-IAM-001 entry point): hero with the product illustration,
 * the four sources, how it works, feature highlights, a dark security band, the plans teaser,
 * questions and a closing call to action. Honest content only (docs/01, 07, 08, 14, 16): no
 * prices, logos, testimonials, ratings or counts; certificates are marked Planned.
 */
export function HomeView({ settings }: { settings: MarketingSettings }) {
  const t = useTranslations("marketing");
  const email = settings.contactEmail;

  return (
    <MarketingShell current="home" settings={settings}>
      {/* Hero */}
      <section
        aria-labelledby="home-title"
        className="mk-hero-bg relative -mt-16 overflow-hidden pt-16"
      >
        <div aria-hidden="true" className="mk-grid pointer-events-none absolute inset-0" />
        <Container className="relative grid items-center gap-12 pt-10 pb-16 md:pt-16 lg:grid-cols-[1fr_1.05fr] lg:gap-10 lg:pt-20 lg:pb-24">
          <div>
            <p className="eyebrow inline-flex items-center gap-2 rounded-full border border-border bg-surface/80 px-3 py-1.5 text-ink">
              <span aria-hidden="true" className="size-1.5 rounded-full bg-success" />
              {t("home.eyebrow")}
            </p>
            <h1
              id="home-title"
              className="mk-display mt-6 text-[2.5rem] font-semibold text-ink sm:text-5xl lg:text-[3.5rem] xl:text-[4rem]"
            >
              {t("home.headlineLead")}{" "}
              <span className="font-display font-normal tracking-normal text-primary">
                {t("home.headlineAccent")}
              </span>
            </h1>
            <p className="mk-lede mt-6 max-w-xl text-lg text-ink-muted md:text-xl">
              {t("home.body")}
            </p>
            <CtaGroup
              className="mt-9"
              contactEmail={email}
              talkLabel={t("cta.talk")}
              signInLabel={t("cta.signIn")}
              secondary={{ href: "#how", label: t("home.secondary") }}
            />
            <p className="mt-5 text-sm text-ink-muted">
              {email ? t("cta.existing") : t("home.note")}
            </p>
          </div>
          <HeroComposition />
        </Container>
      </section>

      {/* The four sources */}
      <section aria-labelledby="sources-title" className="border-y border-border bg-surface">
        <Container className="flex flex-col gap-5 py-8 md:flex-row md:items-center md:gap-10">
          <h2 id="sources-title" className="eyebrow shrink-0 text-ink-subtle">
            {t("home.sourcesLabel")}
          </h2>
          <ul className="grid flex-1 grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-4">
            {SOURCES.map((source) => (
              <li key={source} className="flex items-center gap-2.5 font-medium text-ink">
                <Icon name={SOURCE_ICONS[source]} className="size-5 text-ink-subtle" />
                {t(`home.sources.${source}`)}
              </li>
            ))}
          </ul>
        </Container>
      </section>

      {/* How it works */}
      <Section id="how" labelledBy="how-title">
        <Container>
          <SectionIntro
            id="how-title"
            eyebrow={t("home.how.eyebrow")}
            title={t("home.how.title")}
          />
          <Reveal as="ol" stagger className="grid gap-4 md:grid-cols-3 md:gap-5">
            {STEPS.map((step, index) => (
              <li
                key={step}
                className="relative rounded-2xl border border-border bg-surface p-6 shadow-card md:p-8"
              >
                <span
                  aria-hidden="true"
                  className="block font-display text-6xl leading-none text-primary"
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <p className="eyebrow mt-6 text-ink-subtle">
                  {t("home.how.step", { number: index + 1 })}
                </p>
                <h3 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                  {t(`home.how.${step}.title`)}
                </h3>
                <p className="mt-2 text-ink-muted">{t(`home.how.${step}.body`)}</p>
              </li>
            ))}
          </Reveal>
        </Container>
      </Section>

      {/* Feature highlights */}
      <Section id="highlights" labelledBy="highlights-title" tone="white">
        <Container>
          <SectionIntro
            id="highlights-title"
            eyebrow={t("home.highlights.eyebrow")}
            title={t("home.highlights.title")}
            intro={t("home.highlights.intro")}
          />
          <Reveal as="ul" stagger className="grid gap-4 md:grid-cols-2 md:gap-5 lg:grid-cols-3">
            {FEATURE_SECTIONS.map((feature) => (
              <li key={feature}>
                <Link
                  href={`${PAGE_HREF.features}#${feature}`}
                  className="mk-lift group flex h-full flex-col overflow-hidden rounded-2xl border border-border bg-canvas"
                >
                  <div className="relative h-56 overflow-hidden px-6 pt-6">
                    <div aria-hidden="true" className="origin-top scale-[0.92] select-none">
                      <Vignette feature={feature} />
                    </div>
                    <div
                      aria-hidden="true"
                      className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-canvas to-transparent"
                    />
                  </div>
                  <div className="flex flex-1 flex-col border-t border-border bg-surface p-6">
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="text-lg font-semibold tracking-tight text-ink">
                        {t(`home.highlights.${feature}.title`)}
                      </h3>
                      {PLANNED_FEATURES.has(feature) ? <PlannedBadge label={t("planned")} /> : null}
                    </div>
                    <p className="mt-2 flex-1 text-ink-muted">
                      {t(`home.highlights.${feature}.body`)}
                    </p>
                    <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-primary">
                      {t("cta.learnMore")}
                      <span className="sr-only">: {t(`home.highlights.${feature}.title`)}</span>
                      <Icon name="arrowRight" className="mk-arrow size-4" />
                    </span>
                  </div>
                </Link>
              </li>
            ))}
          </Reveal>
        </Container>
      </Section>

      {/* Security band (dark) */}
      <Section id="security" labelledBy="security-title" tone="night">
        <Container>
          <div className="grid gap-12 lg:grid-cols-[1fr_1.3fr] lg:gap-16">
            <div>
              <SectionIntro
                id="security-title"
                eyebrow={t("home.security.eyebrow")}
                title={t("home.security.title")}
                intro={t("home.security.intro")}
                night
              />
              <ArrowLink href={PAGE_HREF.security} night>
                {t("home.security.link")}
              </ArrowLink>
            </div>
            <Reveal as="ul" stagger className="grid gap-3 sm:grid-cols-2">
              {SECURITY_POINTS.map((point) => (
                <li key={point} className="mk-night-card flex gap-4 rounded-xl p-5">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-white/10 text-white">
                    <Icon name={SECURITY_ICONS[point]} className="size-5" />
                  </span>
                  <p className="self-center font-medium text-white">
                    {t(`home.security.${point}`)}
                  </p>
                </li>
              ))}
            </Reveal>
          </div>
        </Container>
      </Section>

      {/* Plans teaser */}
      <Section id="plans" labelledBy="plans-title">
        <Container>
          <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
            <SectionIntro
              id="plans-title"
              eyebrow={t("home.plans.eyebrow")}
              title={t("home.plans.title")}
              intro={t("home.plans.intro")}
            />
            <div className="mb-12 md:mb-16">
              <ArrowLink href={PAGE_HREF.pricing}>{t("home.plans.link")}</ArrowLink>
            </div>
          </div>
          <Reveal stagger className="grid gap-4 md:grid-cols-2 md:gap-5">
            <PlanCards headingLevel={3} compact />
          </Reveal>
        </Container>
      </Section>

      {/* FAQ */}
      <Section id="faq" labelledBy="faq-title" tone="white">
        <Container className="grid gap-10 lg:grid-cols-[1fr_1.6fr] lg:gap-16">
          <SectionIntro id="faq-title" title={t("home.faq.title")} />
          <Faq
            items={FAQ.map((item) => ({ q: t(`home.faq.${item}.q`), a: t(`home.faq.${item}.a`) }))}
          />
        </Container>
      </Section>

      {/* Closing call to action */}
      <section aria-labelledby="closing-title" className="py-20 md:py-28" data-print="hide">
        <Container>
          <div className="mk-night relative overflow-hidden rounded-3xl px-6 py-14 text-center md:px-12 md:py-20">
            <div
              aria-hidden="true"
              className="mk-grid pointer-events-none absolute inset-0 opacity-40"
            />
            <div className="relative">
              <h2
                id="closing-title"
                className="mk-title text-3xl font-semibold text-white md:text-5xl"
              >
                {t("home.closing.title")}
              </h2>
              <p className="mk-night-muted mx-auto mt-5 max-w-xl text-lg">
                {email ? t("home.closing.body") : t("home.closing.bodyNoContact")}
              </p>
              <CtaGroup
                className="mt-9 justify-center"
                contactEmail={email}
                talkLabel={t("cta.talk")}
                signInLabel={t("cta.signIn")}
                inverse
              />
            </div>
          </div>
        </Container>
      </section>
    </MarketingShell>
  );
}

/** Native disclosures: keyboard, find-in-page and no-JS all work; closed by default. */
export function Faq({ items }: { items: { q: string; a: string }[] }) {
  return (
    <div className="divide-y divide-border border-y border-border">
      {items.map((item) => (
        <details key={item.q} className="mk-faq group">
          <summary
            className={cn(
              "flex min-h-14 cursor-pointer list-none items-center justify-between gap-6 py-5 text-lg font-medium text-ink",
              "rounded-md hover:text-primary",
            )}
          >
            {item.q}
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border-soft text-ink print:hidden">
              <Icon name="chevronDown" className="mk-chevron size-4" />
            </span>
          </summary>
          <p className="max-w-2xl pb-6 text-ink-muted">{item.a}</p>
        </details>
      ))}
    </div>
  );
}

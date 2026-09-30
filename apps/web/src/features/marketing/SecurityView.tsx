import { useTranslations } from "next-intl";
import { Icon, type IconName } from "@/components/ui/Icon";
import { MarketingShell } from "./MarketingShell";
import { AccessRequestCard, AskCard, Mock, RegionDiagram } from "./mockups";
import { Reveal } from "./Reveal";
import type { MarketingSettings } from "./settings";
import { CheckList, Container, CtaGroup, PageHero, Section, SectionIntro } from "./ui";

const CONTROLS = ["isolation", "encryption", "aadhaar", "access", "audit", "logs"] as const;
const CONTROL_ICONS: Record<(typeof CONTROLS)[number], IconName> = {
  isolation: "layers",
  encryption: "lock",
  aadhaar: "shieldCheck",
  access: "users",
  audit: "activity",
  logs: "server",
};
const AI_POINTS = ["filter", "sources", "readOnly", "training"] as const;
const STEPS = ["request", "approve", "access", "record"] as const;
const PRIVACY = ["purpose", "export", "exit"] as const;

/**
 * /security: only claims documented in docs/07, docs/08 and docs/10 (data location, isolation,
 * encryption, no Aadhaar numbers, access control, the audit log, AI limits, support access
 * with the school's approval, DPDP roles, export and exit). No certifications are claimed.
 */
export function SecurityView({ settings }: { settings: MarketingSettings }) {
  const t = useTranslations("marketing");
  return (
    <MarketingShell current="security" settings={settings}>
      <PageHero
        id="security-page-title"
        eyebrow={t("security.eyebrow")}
        title={t("security.headline")}
        intro={t("security.intro")}
      />

      {/* Where the data lives */}
      <Section id="where" labelledBy="where-title" tone="white" className="md:py-24">
        <Container className="grid items-center gap-12 lg:grid-cols-2 lg:gap-20">
          <div>
            <h2 id="where-title" className="mk-title text-3xl font-semibold text-ink md:text-4xl">
              {t("security.where.title")}
            </h2>
            <p className="mk-lede mt-5 text-lg text-ink-muted">{t("security.where.body")}</p>
          </div>
          <Reveal>
            <RegionDiagram
              primary={t("security.where.primary")}
              primaryNote={t("security.where.primaryNote")}
              backup={t("security.where.backup")}
              backupNote={t("security.where.backupNote")}
            />
          </Reveal>
        </Container>
      </Section>

      {/* Controls */}
      <Section id="controls" labelledBy="controls-title">
        <Container>
          <SectionIntro id="controls-title" title={t("security.controls.title")} />
          <Reveal as="ul" stagger className="grid gap-4 md:grid-cols-2 md:gap-5 lg:grid-cols-3">
            {CONTROLS.map((control) => (
              <li
                key={control}
                className="rounded-2xl border border-border bg-surface p-6 shadow-card md:p-7"
              >
                <span className="flex size-11 items-center justify-center rounded-xl bg-success-soft text-success-ink">
                  <Icon name={CONTROL_ICONS[control]} className="size-5" />
                </span>
                <h3 className="mt-5 text-lg font-semibold tracking-tight text-ink">
                  {t(`security.controls.${control}.title`)}
                </h3>
                <p className="mt-2 text-ink-muted">{t(`security.controls.${control}.body`)}</p>
              </li>
            ))}
          </Reveal>
        </Container>
      </Section>

      {/* AI */}
      <Section id="ai" labelledBy="ai-title" tone="night">
        <Container className="grid items-center gap-12 lg:grid-cols-2 lg:gap-20">
          <div>
            <SectionIntro
              id="ai-title"
              title={t("security.ai.title")}
              intro={t("security.ai.body")}
              night
            />
            <CheckList night items={AI_POINTS.map((point) => t(`security.ai.${point}`))} />
          </div>
          <Reveal className="mx-auto w-full max-w-md">
            <Mock caption={`${t("mock.label")}: ${t("security.ai.title")}`}>
              <AskCard notFound />
            </Mock>
          </Reveal>
        </Container>
      </Section>

      {/* Support access */}
      <Section id="support" labelledBy="support-title">
        <Container>
          <SectionIntro
            id="support-title"
            title={t("security.support.title")}
            intro={t("security.support.body")}
          />
          <div className="grid items-start gap-10 lg:grid-cols-[1.5fr_1fr] lg:gap-16">
            <Reveal as="ol" stagger className="grid gap-4 sm:grid-cols-2">
              {STEPS.map((step, index) => (
                <li key={step} className="rounded-2xl border border-border bg-surface p-6">
                  <span
                    aria-hidden="true"
                    className="flex size-8 items-center justify-center rounded-full bg-action font-mono text-sm text-on-action"
                  >
                    {index + 1}
                  </span>
                  <h3 className="mt-4 font-semibold text-ink">
                    {t(`security.support.steps.${step}.title`)}
                  </h3>
                  <p className="mt-1.5 text-ink-muted">
                    {t(`security.support.steps.${step}.body`)}
                  </p>
                </li>
              ))}
            </Reveal>
            <div className="space-y-5">
              <Reveal>
                <Mock caption={`${t("mock.label")}: ${t("mock.access.title")}`}>
                  <AccessRequestCard className="mk-float" />
                </Mock>
              </Reveal>
              <p className="flex gap-3 rounded-xl border border-border bg-surface-muted p-4 text-sm text-ink-muted">
                <Icon name="info" className="mt-0.5 size-4 text-ink" />
                {t("security.support.emergency")}
              </p>
            </div>
          </div>
        </Container>
      </Section>

      {/* Privacy (DPDP) */}
      <Section id="privacy" labelledBy="privacy-title" tone="white">
        <Container className="grid gap-10 lg:grid-cols-2 lg:gap-20">
          <SectionIntro
            id="privacy-title"
            title={t("security.privacy.title")}
            intro={t("security.privacy.body")}
          />
          <div className="lg:pt-2">
            <CheckList items={PRIVACY.map((point) => t(`security.privacy.${point}`))} />
          </div>
        </Container>
      </Section>

      <section aria-labelledby="security-cta" className="py-20 md:py-24" data-print="hide">
        <Container className="flex flex-col items-start gap-6 md:flex-row md:items-center md:justify-between">
          <h2 id="security-cta" className="mk-title max-w-xl text-3xl font-semibold text-ink">
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

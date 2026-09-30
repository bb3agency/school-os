import { useTranslations } from "next-intl";
import type messages from "../../../messages/en.json";
import { Icon } from "@/components/ui/Icon";
import { Faq } from "./HomeView";
import { MarketingShell } from "./MarketingShell";
import { PlanCards } from "./PlanCards";
import { Reveal } from "./Reveal";
import type { MarketingSettings } from "./settings";
import { CheckList, Container, CtaGroup, PageHero, Section, SectionIntro } from "./ui";

type CompareKey = keyof (typeof messages)["marketing"]["pricing"]["compare"];
type Cell = "yes" | CompareKey;
const ROWS: { key: CompareKey; shared: Cell; dedicated: Cell }[] = [
  { key: "features", shared: "yes", dedicated: "yes" },
  { key: "security", shared: "yes", dedicated: "yes" },
  { key: "location", shared: "yes", dedicated: "yes" },
  { key: "server", shared: "serverShared", dedicated: "serverDedicated" },
  { key: "key", shared: "keyShared", dedicated: "keyDedicated" },
  { key: "domain", shared: "domainShared", dedicated: "domainDedicated" },
];
const HOW = ["subscription", "invoices", "noSurprises"] as const;
const FAQ = ["price", "features", "switch", "data"] as const;

/**
 * /pricing: Shared and Dedicated described in words, never a number (owner decision); the
 * price is agreed with each school, so "Talk to us" is the call to action.
 */
export function PricingView({ settings }: { settings: MarketingSettings }) {
  const t = useTranslations("marketing");
  const cell = (value: Cell) =>
    value === "yes" ? (
      <span className="inline-flex items-center gap-2 text-ink">
        <Icon name="checkCircle" className="size-5 text-success" />
        {t("pricing.compare.yes")}
      </span>
    ) : (
      <span className="text-ink">{t(`pricing.compare.${value}`)}</span>
    );

  return (
    <MarketingShell current="pricing" settings={settings}>
      <PageHero
        id="pricing-title"
        eyebrow={t("pricing.eyebrow")}
        title={t("pricing.headline")}
        intro={t("pricing.intro")}
      />

      <section aria-label={t("pricing.headline")} className="-mt-6 pb-20 md:-mt-10 md:pb-28">
        <Container>
          <Reveal stagger className="grid gap-4 md:grid-cols-2 md:gap-5">
            <PlanCards contactEmail={settings.contactEmail} />
          </Reveal>
        </Container>
      </section>

      <Section id="compare" labelledBy="compare-title" tone="white">
        <Container>
          <SectionIntro id="compare-title" title={t("pricing.compare.title")} />
          <div
            className="table-scroll rounded-2xl border border-border"
            tabIndex={0}
            role="region"
            aria-labelledby="compare-title"
          >
            <table className="w-full border-collapse text-sm sm:text-base text-start">
              <caption className="sr-only">{t("pricing.compare.caption")}</caption>
              <thead>
                <tr className="bg-surface-muted">
                  <th
                    scope="col"
                    className="w-1/3 px-3 py-4 sm:px-5 text-start text-sm font-semibold text-ink-muted"
                  >
                    {t("pricing.compare.feature")}
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-4 sm:px-5 text-start text-sm font-semibold text-ink"
                  >
                    {t("pricing.shared.name")}
                  </th>
                  <th
                    scope="col"
                    className="px-3 py-4 sm:px-5 text-start text-sm font-semibold text-ink"
                  >
                    {t("pricing.dedicated.name")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {ROWS.map((row) => (
                  <tr key={row.key}>
                    <th scope="row" className="px-3 py-4 sm:px-5 text-start font-medium text-ink">
                      {t(`pricing.compare.${row.key}`)}
                    </th>
                    <td className="px-3 py-4 sm:px-5">{cell(row.shared)}</td>
                    <td className="px-3 py-4 sm:px-5">{cell(row.dedicated)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Container>
      </Section>

      <Section id="how-pricing" labelledBy="how-pricing-title">
        <Container className="grid gap-10 lg:grid-cols-2 lg:gap-20">
          <SectionIntro id="how-pricing-title" title={t("pricing.how.title")} />
          <div className="space-y-8">
            <CheckList items={HOW.map((item) => t(`pricing.how.${item}`))} />
            <CtaGroup
              contactEmail={settings.contactEmail}
              talkLabel={t("cta.talk")}
              signInLabel={t("cta.signIn")}
            />
          </div>
        </Container>
      </Section>

      <Section id="pricing-faq" labelledBy="pricing-faq-title" tone="white">
        <Container className="grid gap-10 lg:grid-cols-[1fr_1.6fr] lg:gap-16">
          <SectionIntro id="pricing-faq-title" title={t("pricing.faq.title")} />
          <Faq
            items={FAQ.map((item) => ({
              q: t(`pricing.faq.${item}.q`),
              a: t(`pricing.faq.${item}.a`),
            }))}
          />
        </Container>
      </Section>
    </MarketingShell>
  );
}

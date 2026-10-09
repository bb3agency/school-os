import { useTranslations } from "next-intl";
import type messages from "../../../messages/en.json";
import { Icon, type IconName } from "@/components/ui/Icon";
import { Faq } from "./HomeView";
import { MarketingShell } from "./MarketingShell";
import { PlanCards } from "./PlanCards";
import { Reveal } from "./Reveal";
import type { MarketingSettings } from "./settings";
import { CheckList, Container, CtaGroup, PageHero, Section, SectionIntro } from "./ui";

type Pricing = (typeof messages)["marketing"]["pricing"];
type CompareKey = keyof Pricing["compare"];
type Cell = "yes" | CompareKey;
const ROWS: { key: CompareKey; shared: Cell; dedicated: Cell }[] = [
  { key: "features", shared: "yes", dedicated: "yes" },
  { key: "security", shared: "yes", dedicated: "yes" },
  { key: "location", shared: "yes", dedicated: "yes" },
  { key: "server", shared: "serverShared", dedicated: "serverDedicated" },
  { key: "key", shared: "keyShared", dedicated: "keyDedicated" },
  { key: "domain", shared: "domainShared", dedicated: "domainDedicated" },
];
const BASIS: {
  key: Exclude<keyof Pricing["basis"], "eyebrow" | "title" | "intro">;
  icon: IconName;
}[] = [
  { key: "size", icon: "users" },
  { key: "campuses", icon: "building" },
  { key: "tier", icon: "layers" },
  { key: "ai", icon: "sparkles" },
];
const EVERYONE = ["features", "security", "india", "managed", "export", "support"] as const;
const IMPLEMENTATION = [
  "audit",
  "import",
  "mapping",
  "templates",
  "training",
  "reconciliation",
  "domain",
] as const;
const BUNDLES = ["lite", "standard", "high", "extra"] as const;
const BILLING = ["cycle", "oneTime", "gst", "invoices"] as const;
const NEXT = ["reply", "demo", "quote", "start"] as const;
const FAQ = ["price", "features", "ai", "switch", "data"] as const;

/**
 * /pricing (docs/17 §5.6): a packaging explainer that never shows a number (owner decision).
 * It says what the fee is based on, what every school gets, the one-time implementation and
 * data verification fee, AI as monthly question bundles (in words, never "unlimited" or
 * tokens), billing and GST, and what happens after a school gets in touch. Claims follow
 * docs/16 (plans, billing, GST invoices), docs/07 and docs/08 (isolation, export).
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

      <section aria-label={t("pricing.plansLabel")} className="-mt-6 pb-20 md:-mt-10 md:pb-28">
        <Container>
          <Reveal stagger className="grid gap-4 md:grid-cols-2 md:gap-5">
            <PlanCards
              contactEmail={settings.contactEmail}
              whatsappNumber={settings.whatsappNumber}
            />
          </Reveal>
        </Container>
      </section>

      {/* What the fee is based on */}
      <Section id="fee-basis" labelledBy="basis-title" tone="white">
        <Container>
          <SectionIntro
            id="basis-title"
            eyebrow={t("pricing.basis.eyebrow")}
            title={t("pricing.basis.title")}
            intro={t("pricing.basis.intro")}
          />
          <Reveal as="ul" stagger className="grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-4">
            {BASIS.map(({ key, icon }) => (
              <li key={key} className="rounded-2xl border border-border bg-surface p-6">
                <span
                  aria-hidden="true"
                  className="flex size-10 items-center justify-center rounded-xl bg-primary-soft text-primary"
                >
                  <Icon name={icon} className="size-5" />
                </span>
                <h3 className="mt-4 font-semibold text-ink">{t(`pricing.basis.${key}.title`)}</h3>
                <p className="mt-1.5 text-ink-muted">{t(`pricing.basis.${key}.body`)}</p>
              </li>
            ))}
          </Reveal>
        </Container>
      </Section>

      {/* What every school gets */}
      <Section id="everyone" labelledBy="everyone-title">
        <Container className="grid gap-10 lg:grid-cols-2 lg:gap-20">
          <SectionIntro
            id="everyone-title"
            title={t("pricing.everyone.title")}
            intro={t("pricing.everyone.intro")}
          />
          <CheckList items={EVERYONE.map((item) => t(`pricing.everyone.items.${item}`))} />
        </Container>
      </Section>

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
                    <th scope="row" className="px-3 py-4 sm:px-5 text-start font-semibold text-ink">
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

      {/* One-time implementation and data verification */}
      <Section id="implementation" labelledBy="implementation-title">
        <Container className="grid items-start gap-10 lg:grid-cols-2 lg:gap-20">
          <SectionIntro
            id="implementation-title"
            eyebrow={t("pricing.implementation.eyebrow")}
            title={t("pricing.implementation.title")}
            intro={t("pricing.implementation.intro")}
          />
          <div className="rounded-2xl border border-border bg-surface p-6 shadow-card md:p-8">
            <p id="implementation-covers" className="eyebrow text-ink-subtle">
              {t("pricing.implementation.coversLabel")}
            </p>
            <ul aria-labelledby="implementation-covers" className="mt-4 space-y-3">
              {IMPLEMENTATION.map((item) => (
                <li key={item} className="flex gap-3">
                  <Icon name="checkCircle" className="mt-0.5 size-5 shrink-0 text-success" />
                  <span className="text-ink">{t(`pricing.implementation.items.${item}`)}</span>
                </li>
              ))}
            </ul>
          </div>
        </Container>
      </Section>

      {/* AI as monthly question bundles */}
      <Section id="ai-bundles" labelledBy="ai-title" tone="white">
        <Container>
          <SectionIntro
            id="ai-title"
            eyebrow={t("pricing.ai.eyebrow")}
            title={t("pricing.ai.title")}
            intro={t("pricing.ai.intro")}
          />
          <Reveal as="ul" stagger className="grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-4">
            {BUNDLES.map((bundle) => (
              <li
                key={bundle}
                className={
                  bundle === "extra"
                    ? "rounded-2xl border border-dashed border-border-soft bg-surface-muted p-6"
                    : "rounded-2xl border border-border bg-surface p-6"
                }
              >
                <h3 className="font-semibold text-ink">{t(`pricing.ai.${bundle}.name`)}</h3>
                <p className="mt-1.5 text-ink-muted">{t(`pricing.ai.${bundle}.body`)}</p>
              </li>
            ))}
          </Reveal>
          <p className="mt-6 flex gap-3 text-sm text-ink-muted">
            <Icon name="info" className="mt-0.5 size-4 shrink-0 text-ink" />
            {t("pricing.ai.note")}
          </p>
        </Container>
      </Section>

      {/* Billing and GST */}
      <Section id="billing" labelledBy="billing-title">
        <Container className="grid gap-10 lg:grid-cols-2 lg:gap-20">
          <SectionIntro id="billing-title" title={t("pricing.billing.title")} />
          <CheckList items={BILLING.map((item) => t(`pricing.billing.${item}`))} />
        </Container>
      </Section>

      {/* What happens after you contact us */}
      <Section id="next-steps" labelledBy="next-title" tone="white">
        <Container>
          <SectionIntro id="next-title" title={t("pricing.next.title")} />
          <Reveal as="ol" stagger className="grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-4">
            {NEXT.map((step, index) => (
              <li key={step} className="rounded-2xl border border-border bg-surface p-6">
                <span
                  aria-hidden="true"
                  className="flex size-8 items-center justify-center rounded-full bg-action font-mono text-sm text-on-action"
                >
                  {index + 1}
                </span>
                <h3 className="mt-4 font-semibold text-ink">
                  {t(`pricing.next.steps.${step}.title`)}
                </h3>
                <p className="mt-1.5 text-ink-muted">{t(`pricing.next.steps.${step}.body`)}</p>
              </li>
            ))}
          </Reveal>
          <CtaGroup
            className="mt-10"
            contactEmail={settings.contactEmail}
            whatsappNumber={settings.whatsappNumber}
            talkLabel={t("cta.talk")}
            signInLabel={t("cta.signIn")}
          />
        </Container>
      </Section>

      <Section id="pricing-faq" labelledBy="pricing-faq-title">
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

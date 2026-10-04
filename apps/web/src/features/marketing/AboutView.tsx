import { useTranslations } from "next-intl";
import { Icon, type IconName } from "@/components/ui/Icon";
import { mailtoHref } from "./links";
import { MarketingShell } from "./MarketingShell";
import { Reveal } from "./Reveal";
import type { MarketingSettings } from "./settings";
import { Container, PageHero, Section, SectionIntro } from "./ui";

const PRINCIPLES = ["register", "evidence", "grounded", "children"] as const;
const PRINCIPLE_ICONS: Record<(typeof PRINCIPLES)[number], IconName> = {
  register: "folder",
  evidence: "clipboard",
  grounded: "search",
  children: "shieldCheck",
};

/**
 * /about: what SchoolOS is and for whom, what it holds to, and the company's contact details
 * from the settings. Every contact line is shown only when its setting is present; with none
 * set the Contact section is left out and the page still reads well.
 */
export function AboutView({ settings }: { settings: MarketingSettings }) {
  const t = useTranslations("marketing");
  const { contactEmail, companyName, companyAddress } = settings;
  const hasContact = Boolean(contactEmail || companyName || companyAddress);

  return (
    <MarketingShell current="about" settings={settings}>
      <PageHero
        id="about-title"
        eyebrow={t("about.eyebrow")}
        title={t("about.headline")}
        intro={t("about.intro")}
      />

      <Section id="who" labelledBy="who-title" tone="white" className="md:py-24">
        <Container className="grid gap-10 lg:grid-cols-2 lg:gap-20">
          <h2 id="who-title" className="mk-title text-3xl font-normal text-ink md:text-4xl">
            {t("about.who.title")}
          </h2>
          <p className="mk-lede text-lg text-ink-muted md:text-xl">{t("about.who.body")}</p>
        </Container>
      </Section>

      <Section id="principles" labelledBy="principles-title">
        <Container>
          <SectionIntro id="principles-title" title={t("about.principles.title")} />
          <Reveal as="ul" stagger className="grid gap-4 md:grid-cols-2 md:gap-5">
            {PRINCIPLES.map((principle) => (
              <li
                key={principle}
                className="flex gap-5 rounded-2xl border border-border bg-surface p-6 shadow-card md:p-8"
              >
                <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
                  <Icon name={PRINCIPLE_ICONS[principle]} className="size-5" />
                </span>
                <div>
                  <h3 className="text-lg font-semibold tracking-tight text-ink">
                    {t(`about.principles.${principle}.title`)}
                  </h3>
                  <p className="mt-1.5 text-ink-muted">{t(`about.principles.${principle}.body`)}</p>
                </div>
              </li>
            ))}
          </Reveal>
        </Container>
      </Section>

      {hasContact ? (
        <Section id="contact" labelledBy="contact-title" tone="white">
          <Container className="grid gap-10 lg:grid-cols-2 lg:gap-20">
            <SectionIntro
              id="contact-title"
              title={t("about.contact.title")}
              intro={contactEmail ? t("about.contact.body") : undefined}
            />
            <dl className="grid gap-6 rounded-2xl border border-border bg-canvas p-6 md:p-8">
              {companyName ? (
                <div>
                  <dt className="eyebrow text-ink-subtle">{t("about.contact.company")}</dt>
                  <dd className="mt-1 text-lg font-semibold text-ink">{companyName}</dd>
                </div>
              ) : null}
              {companyAddress ? (
                <div>
                  <dt className="eyebrow text-ink-subtle">{t("about.contact.address")}</dt>
                  <dd className="mt-1 text-ink">
                    <address className="not-italic">
                      {companyAddress.map((line) => (
                        <span key={line} className="block">
                          {line}
                        </span>
                      ))}
                    </address>
                  </dd>
                </div>
              ) : null}
              {contactEmail ? (
                <div>
                  <dt className="eyebrow text-ink-subtle">{t("about.contact.email")}</dt>
                  <dd className="mt-1">
                    <a
                      href={mailtoHref(contactEmail)}
                      className="break-anywhere inline-flex min-h-11 items-center text-lg font-semibold text-primary underline underline-offset-4"
                    >
                      {contactEmail}
                    </a>
                  </dd>
                </div>
              ) : null}
            </dl>
          </Container>
        </Section>
      ) : null}
    </MarketingShell>
  );
}

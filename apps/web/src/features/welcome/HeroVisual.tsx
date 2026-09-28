import { useTranslations } from "next-intl";
import { Pill } from "@/components/ui/Badge";
import { Icon } from "@/components/ui/Icon";

/**
 * Made-up sample values for the illustration (not UI text, so not in the message files).
 * Obviously fake: "Sample student A/B" labels, no real names, no ID numbers.
 */
const SAMPLE_DATES = {
  register: "14-06-2014",
  aadhaar: "16-06-2014",
  udise: "14-06-2014",
} as const;

/**
 * Hero illustration, built from HTML/CSS and inline SVG only (no images, CSP-safe): a
 * mismatch finding card and an "Ask the school" answer card with source chips, both with
 * sample data, in the product's card language. Screen readers get the one-sentence
 * description instead of the mock's fragments. Every text pair still meets AA contrast.
 */
export function HeroVisual() {
  const t = useTranslations("welcome.visual");
  const rows = [
    { label: t("register"), value: SAMPLE_DATES.register, ok: true },
    { label: t("aadhaar"), value: SAMPLE_DATES.aadhaar, ok: false },
    { label: t("udise"), value: SAMPLE_DATES.udise, ok: true },
  ];
  return (
    <figure className="relative mx-auto w-full max-w-md lg:max-w-none">
      <figcaption className="sr-only">{t("description")}</figcaption>
      <div aria-hidden="true" className="space-y-4">
        <div className="rounded-xl border border-border bg-surface p-5 shadow-card">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <Pill variant="negative">
              <Icon name="alert" className="size-3.5" />
              {t("findingBadge")}
            </Pill>
            <Pill variant="sample">{t("sample")}</Pill>
          </div>
          <p className="font-medium text-ink">{t("findingTitle")}</p>
          <p className="text-sm text-ink-muted">{t("findingStudent")}</p>
          <dl className="mt-3 divide-y divide-border overflow-hidden rounded-lg border border-border text-sm">
            {rows.map((row) => (
              <div
                key={row.label}
                className={
                  row.ok
                    ? "flex items-center justify-between gap-3 bg-surface px-3 py-2"
                    : "flex items-center justify-between gap-3 bg-danger-soft px-3 py-2"
                }
              >
                <dt className="text-ink-muted">{row.label}</dt>
                <dd className="flex items-center gap-2 font-mono text-ink">
                  {row.value}
                  {row.ok ? (
                    <Pill variant="positive">
                      <Icon name="check" className="size-3" />
                      {t("matches")}
                    </Pill>
                  ) : (
                    <Pill variant="negative">
                      <Icon name="alert" className="size-3" />
                      {t("differs")}
                    </Pill>
                  )}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-ink-subtle">{t("findingRoute")}</p>
        </div>

        <div className="ai-gradient rounded-xl border border-transparent p-5 text-white shadow-card lg:ml-10">
          <p className="eyebrow flex items-center gap-1.5 text-white">
            <Icon name="sparkles" className="size-4" />
            {t("askLabel")}
          </p>
          <p className="mt-3 rounded-lg bg-surface px-3 py-2 text-sm text-ink">
            {t("askQuestion")}
          </p>
          <p className="mt-3 text-sm text-white">{t("askAnswer")}</p>
          <p className="eyebrow mt-3 text-white">{t("sources")}</p>
          <ul className="mt-1.5 flex flex-wrap gap-2">
            {[t("sourceRegister"), t("sourceRecord")].map((source, index) => (
              <li
                key={source}
                className="inline-flex items-center gap-1 rounded-full bg-surface px-2.5 py-0.5 text-xs font-medium text-ink"
              >
                <span className="font-mono text-primary">[{index + 1}]</span>
                <Icon name="file" className="size-3.5 text-ink-muted" />
                {source}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </figure>
  );
}

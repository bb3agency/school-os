import { useTranslations } from "next-intl";

/**
 * Made-up sample values for the illustration (not UI text, so not in the message files).
 * Obviously fake: "Sample student A/B" labels, no real names, no ID numbers.
 */
const SAMPLE_DATES = {
  register: "14-06-2014",
  aadhaar: "16-06-2014",
  udise: "14-06-2014",
} as const;

function WarningIcon() {
  return (
    <svg viewBox="0 0 20 20" className="size-4 shrink-0" fill="currentColor" aria-hidden="true">
      <path d="M10 2.5a1 1 0 0 1 .87.5l7 12.25A1 1 0 0 1 17 16.75H3a1 1 0 0 1-.87-1.5l7-12.25a1 1 0 0 1 .87-.5Zm0 4.25a.9.9 0 0 0-.9.9v3.7a.9.9 0 0 0 1.8 0v-3.7a.9.9 0 0 0-.9-.9Zm0 6.4a1.05 1.05 0 1 0 0 2.1 1.05 1.05 0 0 0 0-2.1Z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 20 20" className="size-4 shrink-0" fill="currentColor" aria-hidden="true">
      <path d="M16.7 5.3a1 1 0 0 1 0 1.4l-8 8a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.4l3.3 3.29 7.3-7.3a1 1 0 0 1 1.4 0Z" />
    </svg>
  );
}

function DocumentIcon() {
  return (
    <svg viewBox="0 0 20 20" className="size-3.5 shrink-0" fill="currentColor" aria-hidden="true">
      <path d="M5 2h6.6L16 6.4V17a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Zm6 1.5V7h3.5L11 3.5ZM6.5 10a.75.75 0 0 0 0 1.5h7a.75.75 0 0 0 0-1.5h-7Zm0 3a.75.75 0 0 0 0 1.5h5a.75.75 0 0 0 0-1.5h-5Z" />
    </svg>
  );
}

/**
 * Hero illustration, built from HTML/CSS and inline SVG only (no images, CSP-safe): a
 * mismatch finding and an "Ask the school" answer with source chips, both with sample data.
 * Screen readers get the one-sentence description instead of the mock's fragments.
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
        <div className="rounded-lg border border-border bg-surface p-4 shadow-md">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1 rounded-full border border-warning-border/40 bg-warning-soft px-2.5 py-0.5 text-xs font-semibold text-warning-ink">
              <WarningIcon />
              {t("findingBadge")}
            </span>
            <span className="rounded-full bg-surface-muted px-2 py-0.5 text-xs text-ink-muted">
              {t("sample")}
            </span>
          </div>
          <p className="font-semibold text-ink">{t("findingTitle")}</p>
          <p className="text-sm text-ink-muted">{t("findingStudent")}</p>
          <dl className="mt-3 divide-y divide-border rounded-md border border-border text-sm">
            {rows.map((row) => (
              <div
                key={row.label}
                className={
                  row.ok
                    ? "flex items-center justify-between gap-3 px-3 py-1.5"
                    : "flex items-center justify-between gap-3 bg-danger-soft px-3 py-1.5"
                }
              >
                <dt className="text-ink-muted">{row.label}</dt>
                <dd className="flex items-center gap-2 font-mono text-ink">
                  {row.value}
                  <span
                    className={
                      row.ok
                        ? "inline-flex items-center gap-1 font-sans text-xs font-semibold text-success-ink"
                        : "inline-flex items-center gap-1 font-sans text-xs font-semibold text-danger"
                    }
                  >
                    {row.ok ? <CheckIcon /> : <WarningIcon />}
                    {row.ok ? t("matches") : t("differs")}
                  </span>
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-ink-muted">{t("findingRoute")}</p>
        </div>

        <div className="rounded-lg border border-border bg-surface p-4 shadow-md lg:ml-10">
          <p className="text-xs font-semibold tracking-wide text-primary uppercase">
            {t("askLabel")}
          </p>
          <p className="mt-2 rounded-md bg-surface-muted px-3 py-2 text-sm text-ink">
            {t("askQuestion")}
          </p>
          <p className="mt-2 text-sm text-ink">{t("askAnswer")}</p>
          <p className="mt-3 text-xs font-semibold text-ink-muted">{t("sources")}</p>
          <ul className="mt-1 flex flex-wrap gap-2">
            {[t("sourceRegister"), t("sourceRecord")].map((source, index) => (
              <li
                key={source}
                className="inline-flex items-center gap-1 rounded-full border border-info-ink/30 bg-info-soft px-2.5 py-0.5 text-xs font-semibold text-info-ink"
              >
                <span className="font-mono">[{index + 1}]</span>
                <DocumentIcon />
                {source}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </figure>
  );
}

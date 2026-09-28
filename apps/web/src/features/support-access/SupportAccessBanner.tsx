import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";

/**
 * Shown on every school page while a SchoolOS operator uses an approved break-glass grant
 * (ADR-0023, 07 §6.4, SEC-021): read-only, every page recorded in the school's audit log.
 * Plain text (no interactive parts); the sign-out button sits in the header controls.
 */
export function SupportAccessBanner() {
  const t = useTranslations("auth.support");
  return (
    <section aria-label={t("bannerTitle")} className="mb-6" data-print="hide">
      <Alert tone="warning" title={t("bannerTitle")}>
        <p>{t("bannerBody")}</p>
      </Alert>
    </section>
  );
}

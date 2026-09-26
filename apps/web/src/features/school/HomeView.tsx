import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard } from "@/components/ui/StatCard";
import { formatCount } from "@/lib/format";
import type { Locale } from "@/i18n/routing";

export interface HomeSummary {
  checksToReview: number | null;
  changesWaiting: number | null;
  recentImports: number | null;
}

export function HomeView({ summary }: { summary: HomeSummary | null }) {
  const t = useTranslations();
  const locale = useLocale() as Locale;
  const count = (value: number | null | undefined) => formatCount(value, locale);
  return (
    <>
      <PageHeader title={t("school.home.title")} description={t("school.home.description")} />
      <dl className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label={t("school.home.checksToReview")}
          value={count(summary?.checksToReview)}
          unavailableLabel={t("common.notAvailable")}
          hint={summary ? undefined : t("school.home.cardHint")}
        />
        <StatCard
          label={t("school.home.changesWaiting")}
          value={count(summary?.changesWaiting)}
          unavailableLabel={t("common.notAvailable")}
          hint={summary ? undefined : t("school.home.cardHint")}
        />
        <StatCard
          label={t("school.home.recentImports")}
          value={count(summary?.recentImports)}
          unavailableLabel={t("common.notAvailable")}
          hint={summary ? undefined : t("school.home.cardHint")}
        />
      </dl>
      <Alert tone="info" title={t("school.home.gettingStartedTitle")} className="mt-6">
        {t("school.home.gettingStartedBody")}
      </Alert>
    </>
  );
}

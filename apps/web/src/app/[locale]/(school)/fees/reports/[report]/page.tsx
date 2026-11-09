import { notFound } from "next/navigation";
import { FeeReportsScreen } from "@/features/fees/PeriodReports";
import type { ReportTab } from "@/features/fees/period";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.periodReports.title"));

const TABS: readonly ReportTab[] = ["collections", "dcb", "registers", "ageing", "year-end"];

type Props = { params: Promise<{ locale: string; report: string }> };

/** FM-04 (FR-FEE-061..074): one fee report per tab, each its own URL. */
export default async function FeeReportPage({ params }: Props) {
  const { report } = await params;
  const tab = TABS.find((t) => t === report);
  if (tab === undefined) notFound();
  return <FeeReportsScreen tab={tab} />;
}

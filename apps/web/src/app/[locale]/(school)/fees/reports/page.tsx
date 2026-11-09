import { FeeReportsScreen } from "@/features/fees/PeriodReports";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.periodReports.title"));

/** FM-04 (FR-FEE-061): the fee reports open on collections by period. */
export default function FeeReportsPage() {
  return <FeeReportsScreen tab="collections" />;
}

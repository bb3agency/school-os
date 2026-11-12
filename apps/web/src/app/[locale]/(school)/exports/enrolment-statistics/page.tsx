import { EnrolmentStatsScreen } from "@/features/export-packs/PackScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("enrolmentStats.title"));

/** FO-06, FR-EXP-009..019: enrolment counts per class in the UDISE+ table shapes. */
export default function EnrolmentStatisticsPage() {
  return <EnrolmentStatsScreen />;
}

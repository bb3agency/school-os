import { FollowUpCountsScreen } from "@/features/insights/FollowUpCountsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("followUpCounts.title"));

/** FT-13 (FR-EW-026..030): school-wide early-warning follow-up counts for management. */
export default function FollowUpCountsPage() {
  return <FollowUpCountsScreen />;
}

import { DashboardView } from "@/features/platform/DashboardView";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.dashboard.title"));

/** KPIs come from GET /api/v1/platform/dashboard once the BFF is wired. */
export default function PlatformDashboardPage() {
  return <DashboardView kpis={null} />;
}

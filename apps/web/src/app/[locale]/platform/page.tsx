import { DashboardScreen } from "@/features/platform/screens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.dashboard.title"));

/** FR-PLT-001: KPIs from GET /api/v1/platform/dashboard through the BFF. */
export default function PlatformDashboardPage() {
  return <DashboardScreen />;
}

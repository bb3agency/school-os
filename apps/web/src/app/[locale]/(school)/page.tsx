import { HomeScreen } from "@/features/school/HomeView";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.home.title"));

/**
 * School home: KPI row and work waiting from existing endpoints (dq summary, pending change
 * requests, recent imports), each only for members who can open that screen.
 */
export default function SchoolHomePage() {
  return <HomeScreen />;
}

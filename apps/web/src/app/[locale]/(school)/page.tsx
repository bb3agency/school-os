import { HomeScreen } from "@/features/school/HomeView";
import { ClientMessages } from "@/i18n/ClientMessages";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.home.title"));

/**
 * School home: KPI row and work waiting from existing endpoints (dq summary, pending change
 * requests, recent imports), each only for members who can open that screen. WP-04 (PERF-014):
 * the home page is not in an area folder, so it picks its own message paths.
 */
export default function SchoolHomePage() {
  return (
    <ClientMessages scope="home">
      <HomeScreen />
    </ClientMessages>
  );
}

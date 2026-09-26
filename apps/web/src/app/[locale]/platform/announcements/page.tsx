import { AnnouncementsScreen } from "@/features/platform/AnnouncementsView";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.announcements.title"));

/** FR-PLT-026: GET/POST /api/v1/platform/announcements through the BFF. */
export default function PlatformAnnouncementsPage() {
  return <AnnouncementsScreen />;
}

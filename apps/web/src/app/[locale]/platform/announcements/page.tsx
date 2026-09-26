import { AnnouncementsView } from "@/features/platform/AnnouncementsView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.announcements.title"));

export default function PlatformAnnouncementsPage() {
  return <AnnouncementsView announcements={ready([])} />;
}

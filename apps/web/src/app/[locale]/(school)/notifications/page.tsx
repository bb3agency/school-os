import { NotificationsScreen } from "@/features/notifications/NotificationsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("notifications.title"));

/** FR-NOT-001: your notifications (own only; the API answers 404 for anyone else's). */
export default function NotificationsPage() {
  return <NotificationsScreen />;
}

import { CalendarScreen } from "@/features/portal-calendar/CalendarScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("portalCalendar.title"));

/** US-3101, US-3102 (ADR-0053): the school's portal and board windows for the year. */
export default function PortalCalendarPage() {
  return <CalendarScreen />;
}

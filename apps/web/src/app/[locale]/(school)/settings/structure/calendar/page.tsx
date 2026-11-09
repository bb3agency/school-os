import { SchoolCalendarScreen } from "@/features/school-calendar/SchoolCalendarScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("schoolCalendar.title"));

/** FT-06 (FR-CAL-001..FR-CAL-020): the school calendar, working days and holidays per year. */
export default function SchoolCalendarPage() {
  return <SchoolCalendarScreen />;
}

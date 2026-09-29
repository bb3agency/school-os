import { AttendanceScreen } from "@/features/insights/AttendanceScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("attendance.title"));

/** US-1701, US-1702: mark a section's day, the month register and sheet import. */
export default function AttendancePage() {
  return <AttendanceScreen />;
}

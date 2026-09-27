import { StudentsScreen } from "@/features/students/StudentList";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("students.list.title"));

/**
 * US-302 / FR-STU-010: GET /students through the BFF. The search is held in the page's memory,
 * never in the URL: names are personal data and must stay out of history and access logs.
 */
export default function StudentsPage() {
  return <StudentsScreen />;
}

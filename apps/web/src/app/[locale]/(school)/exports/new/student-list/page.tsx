import { NewStudentListScreen } from "@/features/exports/NewStudentListScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("exports.studentList.title"));

/** US-901, FR-EXP-003..004: new student list with chosen columns. */
export default function NewStudentListPage() {
  return <NewStudentListScreen />;
}

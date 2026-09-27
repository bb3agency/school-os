import { CreateStudentScreen } from "@/features/students/CreateStudent";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("students.create.title"));

/** US-301 / FR-STU-001..003: POST /students (student.create). */
export default function NewStudentPage() {
  return <CreateStudentScreen />;
}

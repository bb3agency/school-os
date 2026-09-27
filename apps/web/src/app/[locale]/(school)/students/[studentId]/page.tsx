import { notFound } from "next/navigation";
import { StudentDetailScreen } from "@/features/students/StudentDetail";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

// The page title never carries the student's name (browser history on shared office PCs).
export const generateMetadata = pageMetadata((t) => t("students.detail.loadingTitle"));

type Props = { params: Promise<{ locale: string; studentId: string }> };

/** US-301 / FR-STU-001..008: one student (404 outside the caller's scope or school). */
export default async function StudentPage({ params }: Props) {
  const { studentId } = await params;
  if (!UUID_PATTERN.test(studentId)) notFound();
  return <StudentDetailScreen studentId={studentId} />;
}

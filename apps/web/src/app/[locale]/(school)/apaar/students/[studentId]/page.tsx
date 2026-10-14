import { notFound } from "next/navigation";
import { StudentConsentScreen } from "@/features/apaar/StudentConsentScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

// The page title never carries the student's name (browser history on shared office PCs).
export const generateMetadata = pageMetadata((t) => t("apaar.student.title"));

type Props = { params: Promise<{ locale: string; studentId: string }> };

/** ADR-0039, US-1901/US-1902: one student's APAAR consent, its history and the form. */
export default async function StudentConsentPage({ params }: Props) {
  const { studentId } = await params;
  if (!UUID_PATTERN.test(studentId)) notFound();
  return <StudentConsentScreen studentId={studentId} />;
}

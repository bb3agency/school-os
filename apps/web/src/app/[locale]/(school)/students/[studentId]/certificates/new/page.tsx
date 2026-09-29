import { notFound } from "next/navigation";
import { parseIssueType, type SearchParams } from "@/features/certificates/filters";
import { IssueCertificateScreen } from "@/features/certificates/IssueCertificateScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

// The page title never carries the student's name (browser history on shared office PCs).
export const generateMetadata = pageMetadata((t) => t("certificates.issue.title"));

type Props = {
  params: Promise<{ locale: string; studentId: string }>;
  searchParams: Promise<SearchParams>;
};

/** US-1101, US-1102: issue a certificate from the checked record (optional ?type=). */
export default async function IssueCertificatePage({ params, searchParams }: Props) {
  const { studentId } = await params;
  if (!UUID_PATTERN.test(studentId)) notFound();
  return (
    <IssueCertificateScreen
      studentId={studentId}
      initialType={parseIssueType(await searchParams)}
    />
  );
}

import { notFound } from "next/navigation";
import { CertificateDetailScreen } from "@/features/certificates/CertificateDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

// The page title never carries the student's name (browser history on shared office PCs).
export const generateMetadata = pageMetadata((t) => t("certificates.nav"));

type Props = { params: Promise<{ locale: string; certificateId: string }> };

/** US-1101..US-1107: one certificate; approve/reject (never by the requester), print, PDF. */
export default async function CertificatePage({ params }: Props) {
  const { certificateId } = await params;
  if (!UUID_PATTERN.test(certificateId)) notFound();
  return <CertificateDetailScreen certificateId={certificateId} />;
}

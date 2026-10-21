import { notFound } from "next/navigation";
import { RequestDetailScreen } from "@/features/privacy/RequestDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

// The page title never carries the person's name (browser history on shared office PCs).
export const generateMetadata = pageMetadata((t) => t("privacy.detail.title"));

type Props = { params: Promise<{ locale: string; requestId: string }> };

/** US-2603..US-2607, FR-DPDP-008..016: one privacy request and its decision. */
export default async function PrivacyRequestPage({ params }: Props) {
  const { requestId } = await params;
  if (!UUID_PATTERN.test(requestId)) notFound();
  return <RequestDetailScreen requestId={requestId} />;
}

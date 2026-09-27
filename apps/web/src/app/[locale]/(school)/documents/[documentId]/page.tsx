import { notFound } from "next/navigation";
import { DocumentDetailScreen } from "@/features/documents/DocumentDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("documents.detail.title"));

type Props = { params: Promise<{ locale: string; documentId: string }> };

/** US-701 AC3..AC4, FR-DOC-002..008: one document, its versions and who can see it. */
export default async function DocumentPage({ params }: Props) {
  const { documentId } = await params;
  if (!UUID_PATTERN.test(documentId)) notFound();
  return <DocumentDetailScreen documentId={documentId} />;
}

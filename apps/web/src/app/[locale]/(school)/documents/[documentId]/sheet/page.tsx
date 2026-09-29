import { notFound } from "next/navigation";
import { DocumentSheetScreen } from "@/features/documents/DocumentSheet";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("sheets.document.title"));

type Props = { params: Promise<{ locale: string; documentId: string }> };

/** US-701 AC5, FR-DOC-009..011: an XLSX/CSV document as a table (view, correct, download). */
export default async function DocumentSheetPage({ params }: Props) {
  const { documentId } = await params;
  if (!UUID_PATTERN.test(documentId)) notFound();
  return <DocumentSheetScreen documentId={documentId} />;
}

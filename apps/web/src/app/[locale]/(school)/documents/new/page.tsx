import { NewDocumentScreen } from "@/features/documents/NewDocumentScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("documents.new.title"));

/** US-701 AC1..AC2, FR-DOC-001..005: upload a document through the presigned flow. */
export default function NewDocumentPage() {
  return <NewDocumentScreen />;
}

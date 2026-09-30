import { AskScreen } from "@/features/ask/AskScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.title"));

/** US-801..803, FR-KB-001..012: ask the school's records and documents, with sources. */
export default function AskPage() {
  return <AskScreen />;
}

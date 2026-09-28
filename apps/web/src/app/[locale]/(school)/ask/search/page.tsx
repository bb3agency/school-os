import { SearchScreen } from "@/features/ask/SearchScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.search.title"));

/** FR-KB-001, FR-KB-002: search-only passages from documents you can read (text in the body). */
export default function AskSearchPage() {
  return <SearchScreen />;
}

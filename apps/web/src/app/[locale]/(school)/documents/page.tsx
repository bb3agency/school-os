import { DocumentsScreen } from "@/features/documents/DocumentsScreen";
import {
  filtersKey,
  parseDeletedNotice,
  parseDocumentListFilters,
  type SearchParams,
} from "@/features/documents/filters";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("documents.title"));

type Props = { searchParams: Promise<SearchParams> };

/** US-701, FR-DOC-005..008: documents you may see, filtered by codes in the URL. */
export default async function DocumentsPage({ searchParams }: Props) {
  const params = await searchParams;
  const filters = parseDocumentListFilters(params);
  return (
    <DocumentsScreen
      key={filtersKey(filters)}
      filters={filters}
      deleted={parseDeletedNotice(params)}
    />
  );
}

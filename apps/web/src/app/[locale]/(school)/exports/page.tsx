import { ExportsScreen } from "@/features/exports/ExportsScreen";
import { parseExportListFilters, type SearchParams } from "@/features/exports/filters";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("exports.title"));

type Props = { searchParams: Promise<SearchParams> };

/** US-501 AC4, US-901, FR-EXP-001..004 (ADR-0021): your exports, or the school's (`?view=all`). */
export default async function ExportsPage({ searchParams }: Props) {
  const filters = parseExportListFilters(await searchParams);
  return <ExportsScreen key={filters.view} filters={filters} />;
}

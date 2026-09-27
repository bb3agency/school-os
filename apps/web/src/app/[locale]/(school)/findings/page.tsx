import { FindingsScreen } from "@/features/findings/FindingsScreen";
import { parseFindingFilters, type SearchParams } from "@/features/findings/filters";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("findings.title"));

type Props = { searchParams: Promise<SearchParams> };

/** US-501, US-502, FR-DQ-006: findings with filters from the URL (IDs and codes only). */
export default async function FindingsPage({ searchParams }: Props) {
  return <FindingsScreen filters={parseFindingFilters(await searchParams)} />;
}

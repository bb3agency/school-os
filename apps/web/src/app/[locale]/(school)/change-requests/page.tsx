import { ChangeRequestsScreen } from "@/features/change-requests/ChangeRequestsScreen";
import { parseChangeRequestFilters, type SearchParams } from "@/features/change-requests/filters";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("changeRequests.title"));

type Props = { searchParams: Promise<SearchParams> };

/** US-601, FR-CR-001..004: the maker-checker queue (filters in the URL: status, student ID). */
export default async function ChangeRequestsPage({ searchParams }: Props) {
  return <ChangeRequestsScreen filters={parseChangeRequestFilters(await searchParams)} />;
}

import { NewChangeRequestScreen } from "@/features/change-requests/NewChangeRequestScreen";
import { parseNewRequestParams, type SearchParams } from "@/features/change-requests/filters";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("changeRequests.new.title"));

type Props = { searchParams: Promise<SearchParams> };

/** US-601 AC1, FR-CR-001: new correction request (optional student, field and finding IDs). */
export default async function NewChangeRequestPage({ searchParams }: Props) {
  return <NewChangeRequestScreen params={parseNewRequestParams(await searchParams)} />;
}

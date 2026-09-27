import { parseNewPrecheckParams, type SearchParams } from "@/features/exports/filters";
import { NewPrecheckScreen } from "@/features/exports/NewPrecheckScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("exports.precheck.title"));

type Props = { searchParams: Promise<SearchParams> };

/** US-501 AC4, FR-EXP-001..004: new board or portal pre-check (optional `?profile=` key). */
export default async function NewPrecheckPage({ searchParams }: Props) {
  return <NewPrecheckScreen params={parseNewPrecheckParams(await searchParams)} />;
}

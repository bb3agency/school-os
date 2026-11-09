import { BriefScreen } from "@/features/brief/BriefScreen";
import { weekParam } from "@/features/brief/data";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("brief.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/** FA-05 (FR-BRF-001..015): the weekly management brief (owner and principal). */
export default async function BriefPage({ searchParams }: Props) {
  const week = (await searchParams).week;
  return <BriefScreen initialWeek={weekParam(typeof week === "string" ? week : null)} />;
}

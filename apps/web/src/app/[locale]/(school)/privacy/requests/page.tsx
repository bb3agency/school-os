import { RequestsScreen } from "@/features/privacy/RequestsScreen";
import { parseTab } from "@/features/privacy/types";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("privacy.requests.title"));

type Props = { searchParams: Promise<{ tab?: string | string[] }> };

/** US-2601, FR-DPDP-005..007: requests and complaints (only the tab is in the URL). */
export default async function PrivacyRequestsPage({ searchParams }: Props) {
  const { tab } = await searchParams;
  return <RequestsScreen tab={parseTab(tab)} />;
}

import { TcRequestsScreen } from "@/features/certificates/tc-requests/TcRequestsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("certificates.tcRequests.title"));

/** FO-09 (FR-CERT-044): the TC request log. */
export default function TcRequestsPage() {
  return <TcRequestsScreen />;
}

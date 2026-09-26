import { AuditView } from "@/features/school/AuditView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.audit.title"));

/** FR-AUD-005: GET /audit/events once the BFF is wired. */
export default function SchoolAuditPage() {
  return <AuditView events={ready([])} />;
}

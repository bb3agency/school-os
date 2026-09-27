import { AuditVerifyScreen } from "@/features/school/AuditVerifyScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.audit.integrity.title"));

/** US-1001 AC2, FR-AUD-003, FR-AUD-005: check that the school's audit chain is unbroken. */
export default function SchoolAuditVerifyPage() {
  return <AuditVerifyScreen />;
}

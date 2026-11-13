import { PoliciesScreen } from "@/features/compliance/PoliciesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("compliancePolicies.title"));

/** FC-10: the school's policy library with review dates and staff acknowledgement. */
export default function CompliancePoliciesPage() {
  return <PoliciesScreen />;
}

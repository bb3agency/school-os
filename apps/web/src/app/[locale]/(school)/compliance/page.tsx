import { ComplianceScreen } from "@/features/compliance/ComplianceScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("compliance.title"));

/** FC-01: the school's compliance certificate register (owner, principal, office, auditor). */
export default function CompliancePage() {
  return <ComplianceScreen />;
}

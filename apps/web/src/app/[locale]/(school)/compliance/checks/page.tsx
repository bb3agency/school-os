import { ChecksScreen } from "@/features/compliance/ChecksScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("complianceChecks.title"));

/** FC-09: safety drills and periodic checks (owner, principal, office, auditor). */
export default function ComplianceChecksPage() {
  return <ChecksScreen />;
}

import { ReadinessScreen } from "@/features/privacy/ReadinessScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("privacy.readiness.title"));

/** FC-03, FR-DPDP-053..060: DPDP readiness checklist and notice given at admission. */
export default function PrivacyReadinessPage() {
  return <ReadinessScreen />;
}

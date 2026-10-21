import { NewRequestScreen } from "@/features/privacy/NewRequestScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("privacy.new.title"));

/** US-2602, FR-DPDP-005: log a privacy request or complaint. */
export default function NewPrivacyRequestPage() {
  return <NewRequestScreen />;
}

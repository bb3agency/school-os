import { PrivacySettingsScreen } from "@/features/privacy/PrivacySettingsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("privacy.settings.title"));

/** US-2609, FR-DPDP-004/006/017: privacy contact, request target and the record classes. */
export default function PrivacySettingsPage() {
  return <PrivacySettingsScreen />;
}

import { NoticesScreen } from "@/features/privacy/NoticesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("privacy.notices.title"));

/** US-2608, FR-DPDP-001..003: privacy notices, their PDFs and when the school issued them. */
export default function PrivacyNoticesPage() {
  return <NoticesScreen />;
}

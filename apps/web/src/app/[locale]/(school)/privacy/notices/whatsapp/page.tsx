import { WhatsAppSendScreen } from "@/features/privacy/WhatsAppNoticeScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("privacy.whatsapp.title"));

/** FP-13, FR-DPDP-061..064: send the parents' privacy notice on WhatsApp. */
export default function PrivacyNoticeWhatsAppPage() {
  return <WhatsAppSendScreen />;
}

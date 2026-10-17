import { WhatsAppSettingsScreen } from "@/features/messaging/WhatsAppSettingsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("messaging.settingsTitle"));

/**
 * US-2201, US-2209, FR-MSG-002..004, FR-MSG-021 (ADR-0043): connect the school's own WhatsApp
 * number, its status, quality and templates, Resume after a pause, switches and times. The only
 * page that may load Meta's SDK (CSP exception scoped to this route, docs/10 §11.2).
 */
export default function WhatsAppSettingsPage() {
  return <WhatsAppSettingsScreen />;
}

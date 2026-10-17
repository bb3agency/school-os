import { JoinSlipsScreen } from "@/features/messaging/MessagesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("messaging.slips.title"));

/** US-2202 AC2, FR-MSG-009: printable WhatsApp join slips for a section. */
export default function JoinSlipsPage() {
  return <JoinSlipsScreen />;
}

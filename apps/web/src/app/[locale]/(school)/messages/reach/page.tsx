import { ReachScreen } from "@/features/messaging/ReachScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("messaging.reach.title"));

/** FP-01 (FR-MSG-039..043): WhatsApp opt-in coverage per section and the families to follow up. */
export default function ReachPage() {
  return <ReachScreen />;
}

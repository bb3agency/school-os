import { ClosureScreen } from "@/features/messaging/ClosureScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("messaging.closures.title"));

/** FP-02 (FR-MSG-047..054): the urgent closure message (principal, step-up) and recent ones. */
export default function ClosuresPage() {
  return <ClosureScreen />;
}

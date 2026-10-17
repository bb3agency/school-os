import { SetupScreen } from "@/features/fees/SetupScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.setup.title"));

/** US-2101, FR-FEE-002..005: heads, plans, applying a plan. */
export default function FeeSetupPage() {
  return <SetupScreen />;
}

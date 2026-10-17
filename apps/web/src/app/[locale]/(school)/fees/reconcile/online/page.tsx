import { OnlineReconciliationScreen } from "@/features/payments/OnlineReconciliationScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("payments.recon.title"));

/** US-2111, FR-PAY-009, FR-PAY-010: online payment exceptions for a person to explain. */
export default function OnlineReconciliationPage() {
  return <OnlineReconciliationScreen />;
}

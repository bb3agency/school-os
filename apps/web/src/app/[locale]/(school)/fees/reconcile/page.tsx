import { ReconcileScreen } from "@/features/fees/ReconcileScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.reconcile.title"));

/** US-2109 AC2-AC3, FR-FEE-016/017: bank statements, UPI confirmations and exceptions. */
export default function FeeReconcilePage() {
  return <ReconcileScreen />;
}

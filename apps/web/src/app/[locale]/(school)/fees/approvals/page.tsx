import { ApprovalsScreen } from "@/features/fees/ApprovalsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.approvals.title"));

/** FR-FEE-007/008/012/013: requests waiting for a second person (D17). */
export default function FeeApprovalsPage() {
  return <ApprovalsScreen />;
}

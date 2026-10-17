import { ReceiptsScreen } from "@/features/fees/ReceiptsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.receipts.title"));

/** FR-FEE-010/011: receipts, newest first. */
export default function FeeReceiptsPage() {
  return <ReceiptsScreen />;
}

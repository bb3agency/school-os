import { ChequesScreen } from "@/features/fees/ChequesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.cheques.title"));

/** FM-08 (FR-FEE-131..141): cheque status, the deposit list and returned cheques. */
export default function FeeChequesPage() {
  return <ChequesScreen />;
}

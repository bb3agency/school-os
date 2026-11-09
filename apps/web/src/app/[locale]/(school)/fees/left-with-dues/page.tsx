import { LeftWithDuesScreen } from "@/features/fees/LeftWithDues";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.leftWithDues.title"));

/** FM-01 (FR-FEE-038, FR-FEE-040): students who left with fees still open. */
export default function FeeLeftWithDuesPage() {
  return <LeftWithDuesScreen />;
}

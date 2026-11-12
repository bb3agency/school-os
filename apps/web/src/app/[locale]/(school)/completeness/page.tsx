import { CompletenessScreen } from "@/features/completeness/CompletenessScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("completeness.title"));

/** FT-14 (FR-ATT-024, FR-MRK-024): attendance and marks entry completeness per section. */
export default function CompletenessPage() {
  return <CompletenessScreen />;
}

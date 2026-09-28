import { PromotionsIndexScreen } from "@/features/promotions/PromotionsIndexScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("academicStructure.promotions.titlePlain"));

/**
 * FR-TEN-011, US-202 AC2: the "Promotions" menu entry: the academic years, each linking to its
 * year-end promotion (holders of tenant.structure.manage).
 */
export default function PromotionsIndexPage() {
  return <PromotionsIndexScreen />;
}

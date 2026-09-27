import { notFound } from "next/navigation";
import { PromotionsScreen } from "@/features/promotions/PromotionsScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("academicStructure.promotions.titlePlain"));

type Props = { params: Promise<{ locale: string; yearId: string }> };

/**
 * FR-TEN-011, US-202 AC2: year-end promotion of one academic year (preview, promote, undo
 * within 24 hours) for holders of tenant.structure.manage. The URL holds only the year's ID.
 */
export default async function PromotionsPage({ params }: Props) {
  const { yearId } = await params;
  if (!UUID_PATTERN.test(yearId)) notFound();
  return <PromotionsScreen yearId={yearId} />;
}

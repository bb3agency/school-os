import { notFound } from "next/navigation";
import { ExportDetailScreen } from "@/features/exports/ExportDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("exports.detail.title"));

type Props = { params: Promise<{ locale: string; exportId: string }> };

/** FR-EXP-003..004, SEC-005 (ADR-0021): one export's status, details and downloads. */
export default async function ExportPage({ params }: Props) {
  const { exportId } = await params;
  if (!UUID_PATTERN.test(exportId)) notFound();
  return <ExportDetailScreen exportId={exportId} />;
}

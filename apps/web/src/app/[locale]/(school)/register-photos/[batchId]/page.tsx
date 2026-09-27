import { notFound } from "next/navigation";
import { BatchScreen } from "@/features/extraction/BatchScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("extraction.batch.loadingTitle"));

type Props = { params: Promise<{ locale: string; batchId: string }> };

/** US-402 AC4: one batch with progress per page and its verification queue. */
export default async function RegisterPhotoBatchPage({ params }: Props) {
  const { batchId } = await params;
  if (!UUID_PATTERN.test(batchId)) notFound();
  return <BatchScreen batchId={batchId} />;
}

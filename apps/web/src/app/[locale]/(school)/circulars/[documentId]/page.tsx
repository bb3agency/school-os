import { notFound } from "next/navigation";
import { CircularDetailScreen } from "@/features/circulars/CircularDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("circulars.detailTitle"));

type Props = { params: Promise<{ locale: string; documentId: string }> };

/** US-1601, US-1602: one circular's AI reading, suggested deadlines and review. */
export default async function CircularPage({ params }: Props) {
  const { documentId } = await params;
  if (!UUID_PATTERN.test(documentId)) notFound();
  return <CircularDetailScreen documentId={documentId} />;
}

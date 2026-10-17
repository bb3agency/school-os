import { notFound } from "next/navigation";
import { ReceiptDetailScreen } from "@/features/fees/ReceiptDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

// The page title never carries the student's name (browser history on shared office PCs).
export const generateMetadata = pageMetadata((t) => t("fees.nav.receipts"));

type Props = { params: Promise<{ locale: string; receiptId: string }> };

/** FR-FEE-011..013: one receipt; print, PDF, cancellation and refund requests. */
export default async function FeeReceiptPage({ params }: Props) {
  const { receiptId } = await params;
  if (!UUID_PATTERN.test(receiptId)) notFound();
  return <ReceiptDetailScreen receiptId={receiptId} />;
}

import { notFound } from "next/navigation";
import { InvoiceDetailScreen } from "@/features/platform/InvoiceDetailView";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("platform.invoices.detailTitle"));

type Props = { params: Promise<{ locale: string; invoiceId: string }> };

/** FR-PLT-018: one invoice with its payments; reverse a recorded payment. */
export default async function PlatformInvoicePage({ params }: Props) {
  const { invoiceId } = await params;
  if (!UUID_PATTERN.test(invoiceId)) notFound();
  return <InvoiceDetailScreen invoiceId={invoiceId} />;
}

import { InvoicesView } from "@/features/platform/BillingViews";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.invoices.title"));

export default function PlatformInvoicesPage() {
  return <InvoicesView invoices={ready([])} />;
}

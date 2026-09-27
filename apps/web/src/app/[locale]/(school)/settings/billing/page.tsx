import { BillingScreen } from "@/features/school/BillingView";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.billing.title"));

/** FR-PLT-030 (US-1204): GET /tenant/billing and /tenant/billing/invoices through the BFF. */
export default function SchoolBillingPage() {
  return <BillingScreen />;
}

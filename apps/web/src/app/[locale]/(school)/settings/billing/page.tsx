import { BillingView } from "@/features/school/BillingView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.billing.title"));

/** FR-PLT-030 (US-1204): GET /billing/subscription and /billing/invoices once wired. */
export default function SchoolBillingPage() {
  return <BillingView subscription={ready(null)} invoices={ready([])} />;
}

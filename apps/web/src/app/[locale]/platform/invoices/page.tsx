import { InvoicesScreen } from "@/features/platform/BillingViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.invoices.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-015..019: GET /platform/invoices with filters; issue, pay, void. */
export default async function PlatformInvoicesPage({ searchParams }: Props) {
  const params = await searchParams;
  return (
    <InvoicesScreen
      filters={{
        ...(first(params.status) ? { status: first(params.status) as string } : {}),
        ...(first(params.fy) ? { financialYear: first(params.fy) as string } : {}),
        ...(first(params.school) ? { tenantId: first(params.school) as string } : {}),
      }}
    />
  );
}

import { UsageScreen } from "@/features/platform/OperationsViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.usage.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-020..021: GET /platform/usage?from=&to=&tenant_id=. */
export default async function PlatformUsagePage({ searchParams }: Props) {
  const params = await searchParams;
  return (
    <UsageScreen
      filters={{
        ...(first(params.from) ? { from: first(params.from) as string } : {}),
        ...(first(params.to) ? { to: first(params.to) as string } : {}),
        ...(first(params.school) ? { tenantId: first(params.school) as string } : {}),
      }}
    />
  );
}

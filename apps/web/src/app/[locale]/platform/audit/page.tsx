import { PlatformAuditScreen } from "@/features/platform/OperationsViews";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.audit.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-029: GET /platform/audit/events (filters from the URL), CSV export, verify. */
export default async function PlatformAuditPage({ searchParams }: Props) {
  const params = await searchParams;
  return (
    <PlatformAuditScreen
      filters={{
        ...(first(params.actor) ? { actor: first(params.actor) as string } : {}),
        ...(first(params.action) ? { action: first(params.action) as string } : {}),
        ...(first(params.school) ? { tenantId: first(params.school) as string } : {}),
        ...(first(params.from) ? { from: first(params.from) as string } : {}),
        ...(first(params.to) ? { to: first(params.to) as string } : {}),
      }}
    />
  );
}

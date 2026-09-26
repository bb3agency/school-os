import { AuditScreen } from "@/features/school/screens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.audit.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-AUD-005: GET /audit/events through the BFF, filtered by the URL (GET form). */
export default async function SchoolAuditPage({ searchParams }: Props) {
  const params = await searchParams;
  return (
    <AuditScreen
      filters={{
        actor: first(params.actor),
        action: first(params.action),
        from: first(params.from),
        to: first(params.to),
      }}
    />
  );
}

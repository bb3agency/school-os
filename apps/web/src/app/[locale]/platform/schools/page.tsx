import { SchoolsScreen } from "@/features/platform/screens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.schools.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

const first = (value: string | string[] | undefined) =>
  typeof value === "string" ? value : undefined;

/** FR-PLT-001..005: GET /api/v1/platform/tenants with the URL's filters. */
export default async function PlatformSchoolsPage({ searchParams }: Props) {
  const params = await searchParams;
  return (
    <SchoolsScreen
      filters={{
        q: first(params.q) ?? "",
        ...(first(params.status) ? { status: first(params.status) as string } : {}),
        ...(first(params.tier) ? { tier: first(params.tier) as string } : {}),
        trialEnding: first(params.trial_ending) === "true",
        pastDue: first(params.past_due) === "true",
      }}
    />
  );
}

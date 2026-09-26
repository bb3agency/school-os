import { SchoolsScreen } from "@/features/platform/screens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.schools.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/** FR-PLT-001..005: GET /api/v1/platform/tenants?q= through the BFF. */
export default async function PlatformSchoolsPage({ searchParams }: Props) {
  const { q } = await searchParams;
  const query = typeof q === "string" ? q : "";
  return <SchoolsScreen query={query} />;
}

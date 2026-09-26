import { SchoolsView } from "@/features/platform/SchoolsView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.schools.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/** FR-PLT-001..005: GET /api/v1/platform/tenants?q= once the BFF is wired. */
export default async function PlatformSchoolsPage({ searchParams }: Props) {
  const { q } = await searchParams;
  const query = typeof q === "string" ? q : "";
  return <SchoolsView schools={ready([])} query={query} />;
}

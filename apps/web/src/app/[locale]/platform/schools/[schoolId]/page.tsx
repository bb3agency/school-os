import { notFound } from "next/navigation";
import { parseSchoolTab, SchoolDetailView } from "@/features/platform/SchoolDetailView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.schoolDetail.title"));

type Props = {
  params: Promise<{ locale: string; schoolId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** GET /api/v1/platform/tenants/{tenant_id} once the BFF is wired. */
export default async function PlatformSchoolDetailPage({ params, searchParams }: Props) {
  const { schoolId } = await params;
  if (!UUID.test(schoolId)) notFound();
  const { tab } = await searchParams;
  return <SchoolDetailView schoolId={schoolId} tab={parseSchoolTab(tab)} detail={ready(null)} />;
}

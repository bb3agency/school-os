import { notFound } from "next/navigation";
import { SchoolDetailScreen } from "@/features/platform/SchoolDetailView";
import { parseSchoolTab } from "@/features/platform/school-tabs";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("platform.schoolDetail.title"));

type Props = {
  params: Promise<{ locale: string; schoolId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** FR-PLT-001..005: GET /api/v1/platform/tenants/{tenant_id} and its tabs. */
export default async function PlatformSchoolDetailPage({ params, searchParams }: Props) {
  const { schoolId } = await params;
  if (!UUID_PATTERN.test(schoolId)) notFound();
  const { tab } = await searchParams;
  return <SchoolDetailScreen schoolId={schoolId} tab={parseSchoolTab(tab)} />;
}

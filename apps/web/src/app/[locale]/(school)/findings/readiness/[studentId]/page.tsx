import { notFound } from "next/navigation";
import { ReadinessStudentScreen } from "@/features/readiness/ReadinessStudentScreen";
import { DEFAULT_PROFILE, isProfileKey } from "@/features/readiness/types";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("readiness.title"));

type Props = {
  params: Promise<{ locale: string; studentId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** US-505: one student's readiness with the exact differences (another school's ID: 404). */
export default async function ReadinessStudentPage({ params, searchParams }: Props) {
  const { studentId } = await params;
  if (!UUID_PATTERN.test(studentId)) notFound();
  const query = await searchParams;
  const profile = isProfileKey(query.profile) ? query.profile : DEFAULT_PROFILE;
  return <ReadinessStudentScreen profileKey={profile} studentId={studentId} />;
}

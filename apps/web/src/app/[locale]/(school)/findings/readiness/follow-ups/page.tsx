import { DEFAULT_PROFILE, isProfileKey } from "@/features/readiness/types";
import { FollowupsScreen } from "@/features/udise-followup/FollowupsScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("udiseFollowup.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/** FO-01 (FR-UDI-021..030): follow-ups on fixes outside the school, per readiness profile. */
export default async function FollowupsPage({ searchParams }: Props) {
  const query = await searchParams;
  const profile = isProfileKey(query.profile) ? (query.profile as string) : DEFAULT_PROFILE;
  const section =
    typeof query.section === "string" && UUID_PATTERN.test(query.section) ? query.section : null;
  return <FollowupsScreen profileKey={profile} sectionId={section} />;
}

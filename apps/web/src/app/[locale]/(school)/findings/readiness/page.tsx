import { ReadinessScreen } from "@/features/readiness/ReadinessScreen";
import { DEFAULT_PROFILE, isProfileKey } from "@/features/readiness/types";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("readiness.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/** US-504, US-506: board and portal readiness by section (profile and section in the URL). */
export default async function ReadinessPage({ searchParams }: Props) {
  const query = await searchParams;
  const profile = isProfileKey(query.profile) ? query.profile : DEFAULT_PROFILE;
  const section =
    typeof query.section === "string" && UUID_PATTERN.test(query.section) ? query.section : null;
  return <ReadinessScreen profileKey={profile} sectionId={section} />;
}

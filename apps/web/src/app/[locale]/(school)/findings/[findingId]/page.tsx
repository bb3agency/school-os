import { notFound } from "next/navigation";
import { FindingDetailScreen } from "@/features/findings/FindingDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("findings.title"));

type Props = { params: Promise<{ locale: string; findingId: string }> };

/** US-502, FR-DQ-020: one finding; resolve or accept it (another school's ID answers 404). */
export default async function FindingPage({ params }: Props) {
  const { findingId } = await params;
  if (!UUID_PATTERN.test(findingId)) notFound();
  return <FindingDetailScreen findingId={findingId} />;
}

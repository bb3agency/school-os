import { notFound } from "next/navigation";
import { FlagDetailScreen } from "@/features/insights/FlagDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("insights.detail.title"));

type Props = { params: Promise<{ locale: string; flagId: string }> };

/** US-1706: one flag, why it was raised and its intervention log. */
export default async function FlagPage({ params }: Props) {
  const { flagId } = await params;
  if (!UUID_PATTERN.test(flagId)) notFound();
  return <FlagDetailScreen flagId={flagId} />;
}

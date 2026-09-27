import { notFound } from "next/navigation";
import { BreakGlassDetailScreen } from "@/features/break-glass/BreakGlassScreens";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("breakGlass.detail.title"));

type Props = { params: Promise<{ locale: string; grantId: string }> };

/** US-103 AC1/AC2: one support-access request; approve, deny or end it (step-up MFA). */
export default async function BreakGlassGrantPage({ params }: Props) {
  const { grantId } = await params;
  if (!UUID_PATTERN.test(grantId)) notFound();
  return <BreakGlassDetailScreen grantId={grantId} />;
}

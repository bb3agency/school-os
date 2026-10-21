import { notFound } from "next/navigation";
import { BreachDetailScreen } from "@/features/breaches/BreachDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("breaches.detail.crumb"));

type Props = { params: Promise<{ locale: string; breachId: string }> };

/** ADR-0050, US-2702: one breach, its three duties and clocks, drafts and closing. */
export default async function BreachPage({ params }: Props) {
  const { breachId } = await params;
  if (!UUID_PATTERN.test(breachId)) notFound();
  return <BreachDetailScreen breachId={breachId} />;
}

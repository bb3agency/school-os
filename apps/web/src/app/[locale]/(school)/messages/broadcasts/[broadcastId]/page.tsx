import { notFound } from "next/navigation";
import { BroadcastReportScreen } from "@/features/messaging/BroadcastScreens";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("messaging.broadcasts.report"));

type Props = { params: Promise<{ locale: string; broadcastId: string }> };

/** FR-MSG-017, US-2204 AC2: one WhatsApp broadcast's live delivery report (ids only in the URL). */
export default async function BroadcastReportPage({ params }: Props) {
  const { broadcastId } = await params;
  if (!UUID_PATTERN.test(broadcastId)) notFound();
  return <BroadcastReportScreen broadcastId={broadcastId} />;
}

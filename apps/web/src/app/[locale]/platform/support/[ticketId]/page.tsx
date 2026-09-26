import { notFound } from "next/navigation";
import { PlatformTicketScreen } from "@/features/platform/SupportScreens";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("platform.support.ticketTitle"));

type Props = { params: Promise<{ locale: string; ticketId: string }> };

/** FR-PLT-027: one ticket with its thread; reply, status, priority, assignee. */
export default async function PlatformTicketPage({ params }: Props) {
  const { ticketId } = await params;
  if (!UUID_PATTERN.test(ticketId)) notFound();
  return <PlatformTicketScreen ticketId={ticketId} />;
}

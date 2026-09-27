import { notFound } from "next/navigation";
import { SupportTicketScreen } from "@/features/school/SupportScreens";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("school.support.ticketTitle"));

type Props = { params: Promise<{ locale: string; ticketId: string }> };

/** FR-PLT-027: one of the school's tickets (other schools' tickets answer 404). */
export default async function SchoolTicketPage({ params }: Props) {
  const { ticketId } = await params;
  if (!UUID_PATTERN.test(ticketId)) notFound();
  return <SupportTicketScreen ticketId={ticketId} />;
}

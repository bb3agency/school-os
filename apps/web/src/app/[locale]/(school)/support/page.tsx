import { SupportScreen } from "@/features/school/SupportScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.support.title"));

/** FR-PLT-027: the school's support tickets (support.ticket.create). */
export default function SchoolSupportPage() {
  return <SupportScreen />;
}

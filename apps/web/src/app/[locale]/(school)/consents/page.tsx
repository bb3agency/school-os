import { ConsentsScreen } from "@/features/consents/ConsentsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("consents.title"));

/** ADR-0049, US-2603: answers per purpose and section, and the forms to print. */
export default function ConsentsPage() {
  return <ConsentsScreen />;
}

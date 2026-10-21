import { PurposesScreen } from "@/features/consents/PurposesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("consents.purposes.title"));

/** ADR-0049, US-2601: the school's optional consent purposes and the form families answer. */
export default function ConsentPurposesPage() {
  return <PurposesScreen />;
}

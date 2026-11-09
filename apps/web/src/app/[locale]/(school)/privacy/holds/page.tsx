import { HoldsScreen } from "@/features/holds/HoldsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("holds.title"));

/** FC-12, ADR-0059: the school's legal holds (owner and principal). */
export default function HoldsPage() {
  return <HoldsScreen />;
}

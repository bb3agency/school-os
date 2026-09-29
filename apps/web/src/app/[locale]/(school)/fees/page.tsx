import { FeeDuesScreen } from "@/features/tally/FeeDuesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("tally.dues.title"));

/**
 * US-1804, FR-TALLY-007: GET /tally/dues (`finance.read`). Behind the school's Tally connector
 * flag (ADR-0032 Proposed): while it is off the screen says the connector is not switched on.
 */
export default function FeesPage() {
  return <FeeDuesScreen />;
}

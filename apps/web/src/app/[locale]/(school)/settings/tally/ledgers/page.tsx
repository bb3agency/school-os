import { TallyLedgersScreen } from "@/features/tally/TallyLedgersScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("tally.ledgers.title"));

/**
 * US-1803, FR-TALLY-006: link Tally ledgers to students (`tally.configure`); a person decides
 * every link. Behind the school's Tally connector flag (ADR-0032 Proposed).
 */
export default function TallyLedgersPage() {
  return <TallyLedgersScreen />;
}

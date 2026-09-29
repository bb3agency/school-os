import { TallyConnectorScreen } from "@/features/tally/TallyConnectorScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("tally.connector.title"));

/**
 * US-1801..US-1803, FR-TALLY-001/005/009: connector status, office PC agents (enrolment code and
 * revocation with a recent MFA sign-in, `tally.device.manage`) and the ledger groups the agent
 * may send (`tally.configure`). Behind the school's Tally connector flag (ADR-0032 Proposed).
 */
export default function TallyConnectorPage() {
  return <TallyConnectorScreen />;
}

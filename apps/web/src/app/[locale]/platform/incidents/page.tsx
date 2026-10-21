import { IncidentsScreen } from "@/features/platform/incidents/IncidentsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("platform.incidents.title"));

/** ADR-0050, FR-DPDP-030: SchoolOS incident records with the CERT-In clock. */
export default function PlatformIncidentsPage() {
  return <IncidentsScreen />;
}

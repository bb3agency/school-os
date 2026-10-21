import { notFound } from "next/navigation";
import { IncidentDetailScreen } from "@/features/platform/incidents/IncidentDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("platform.incidents.detailTitle"));

type Props = { params: Promise<{ locale: string; incidentId: string }> };

/** ADR-0050, FR-DPDP-031, SEC-029: one incident, its schools and the two-person confirmation. */
export default async function PlatformIncidentPage({ params }: Props) {
  const { incidentId } = await params;
  if (!UUID_PATTERN.test(incidentId)) notFound();
  return <IncidentDetailScreen incidentId={incidentId} />;
}

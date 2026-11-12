import { InspectionScreen } from "@/features/compliance/InspectionScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("compliance.packs.title"));

/** FC-07 / FO-12: prepare for an inspection (packs per authority, readiness, index print). */
export default function InspectionPage() {
  return <InspectionScreen />;
}

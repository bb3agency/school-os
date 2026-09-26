import { StructureScreen } from "@/features/school/screens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.structure.title"));

/** FR-TEN-010: GET /academic-years, /classes, /sections through the BFF. */
export default function SchoolStructurePage() {
  return <StructureScreen />;
}

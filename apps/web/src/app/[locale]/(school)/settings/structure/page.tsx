import { AcademicStructureScreen } from "@/features/academic-structure/AcademicStructureScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.structure.title"));

/**
 * US-202 / FR-TEN-010: academic years, classes and sections through the BFF; add, edit and
 * "make current" for holders of tenant.structure.manage.
 */
export default function SchoolStructurePage() {
  return <AcademicStructureScreen />;
}

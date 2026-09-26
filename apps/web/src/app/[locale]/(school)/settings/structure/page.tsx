import { StructureView } from "@/features/school/StructureView";
import { ready } from "@/lib/loadable";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("school.structure.title"));

/** FR-TEN-010: data from GET /academic-years, /classes, /sections once the BFF is wired. */
export default function SchoolStructurePage() {
  return <StructureView years={ready([])} classes={ready([])} sections={ready([])} />;
}

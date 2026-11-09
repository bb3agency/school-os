import { ProgressionScreen } from "@/features/year-end/ProgressionScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("yearEnd.progression.title"));

/** FT-08 (FR-YRE-010..FR-YRE-013): the UDISE+ progression check sheet of a year. */
export default function YearEndProgressionPage() {
  return <ProgressionScreen />;
}

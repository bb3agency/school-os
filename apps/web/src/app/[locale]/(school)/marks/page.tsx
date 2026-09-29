import { MarksScreen } from "@/features/insights/MarksScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("marks.title"));

/** US-1703: exams, a section's marks grid, entry and sheet import. */
export default function MarksPage() {
  return <MarksScreen />;
}

import { YearEndScreen } from "@/features/year-end/YearEndScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("yearEnd.title"));

/** FT-08 (FR-YRE-002..FR-YRE-007): annual results per section, confirmed by the principal. */
export default function YearEndPage() {
  return <YearEndScreen />;
}

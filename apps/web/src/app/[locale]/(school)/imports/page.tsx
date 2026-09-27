import { ImportsScreen } from "@/features/imports/ImportsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("imports.list.title"));

/** US-401 / FR-IMP-001..007: spreadsheet imports (import.run). */
export default function ImportsPage() {
  return <ImportsScreen />;
}

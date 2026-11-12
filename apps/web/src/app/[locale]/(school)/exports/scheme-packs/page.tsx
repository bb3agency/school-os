import { SchemePackScreen } from "@/features/export-packs/PackScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("schemePacks.title"));

/** FO-11, FR-EXP-020..028: scheme verification attendance lists and bulk certificates. */
export default function SchemePacksPage() {
  return <SchemePackScreen />;
}

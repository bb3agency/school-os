import { AddonsScreen } from "@/features/fees/AddonsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.addons.title"));

/** FM-02 (FR-FEE-081..100): add-on fees (transport bands, hostel, activities) and who is on them. */
export default function FeeAddonsPage() {
  return <AddonsScreen />;
}

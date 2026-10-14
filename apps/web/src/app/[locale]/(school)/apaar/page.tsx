import { ApaarScreen } from "@/features/apaar/ApaarScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("apaar.title"));

/** ADR-0039, US-1901..US-1903: the APAAR consent register, forms and follow-up list. */
export default function ApaarPage() {
  return <ApaarScreen />;
}

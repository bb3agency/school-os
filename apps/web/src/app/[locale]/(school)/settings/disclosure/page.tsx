import { DisclosureScreen } from "@/features/disclosure/DisclosureScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("disclosure.title"));

/**
 * R3-1C (US-2901..US-2904, ADR-0051): the CBSE Appendix IX disclosure draft, publishing and the
 * published history (`disclosure.edit`; publishing, withdrawing and changing the link need
 * `disclosure.publish` with step-up). CBSE schools only (404 module_hidden otherwise).
 */
export default function DisclosurePage() {
  return <DisclosureScreen />;
}

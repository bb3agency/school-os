import { UdiseCorrectionsScreen } from "@/features/udise/UdiseScreens";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("udise.title"));

/** FO-02, FR-UDI-001..003: UDISE+ corrections the MEO office must make, and the requests. */
export default function UdiseCorrectionsPage() {
  return <UdiseCorrectionsScreen />;
}

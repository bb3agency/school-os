import { UdiseTodoScreen } from "@/features/udise-followup/UdiseTodoScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("udiseTodo.title"));

/** FO-03 (FR-UDI-031..040): what to repeat in UDISE+ after something happened in SchoolOS. */
export default function UdiseTodoPage() {
  return <UdiseTodoScreen />;
}

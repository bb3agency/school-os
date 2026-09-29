import { CircularsScreen } from "@/features/circulars/CircularsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("circulars.title"));

/** US-1601, US-1602: circulars inbox (document visibility applies; `document.read`). */
export default function CircularsPage() {
  return <CircularsScreen />;
}

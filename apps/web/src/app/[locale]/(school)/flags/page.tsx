import { FlagsScreen } from "@/features/insights/FlagsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("insights.title"));

/** US-1705, US-1706, US-1708: my flags, the school's flags, counts and rules. */
export default function FlagsPage() {
  return <FlagsScreen />;
}

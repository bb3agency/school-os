import { FeeSettingsScreen } from "@/features/fees/FeeSettingsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.settings.title"));

/** R2-3J (FR-MSG-014, US-2205): receipt paper, GST line and the fee reminder days. */
export default function FeeSettingsPage() {
  return <FeeSettingsScreen />;
}

import { UpiSettingsScreen } from "@/features/fees/UpiSettingsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.upi.title"));

/** US-2109 AC1, FR-FEE-015: the school's UPI ID, payee name and static QR (no money handled). */
export default function UpiSettingsPage() {
  return <UpiSettingsScreen />;
}

import { PaymentsSettingsScreen } from "@/features/payments/PaymentsSettingsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("payments.settingsTitle"));

/**
 * US-2107, FR-PAY-002..004, FR-PAY-012..014 (ADR-0042): connect the school's own Razorpay
 * account, test it, rotate the webhook secret, disconnect.
 */
export default function PaymentsSettingsPage() {
  return <PaymentsSettingsScreen />;
}

import { OAuthCallbackScreen } from "@/features/payments/OAuthCallbackScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("payments.callback.title"));

/** FR-PAY-002: Razorpay's OAuth redirect (the code and state leave the address at once). */
export default function PaymentsOAuthCallbackPage() {
  return <OAuthCallbackScreen />;
}

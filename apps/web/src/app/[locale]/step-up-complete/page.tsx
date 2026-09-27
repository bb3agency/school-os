import { MinimalShell } from "@/components/shell/MinimalShell";
import { StepUpCompleteView } from "@/features/auth/StepUpCompleteView";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("errors.stepUp.completeTitle"));

/**
 * Return address of the step-up window (ADR-0018): the BFF step-up callback lands here after
 * the user signed in again; the page that opened the window then retries its request.
 * Shows no data; a visitor without a session just sees the message.
 */
export default function StepUpCompletePage() {
  return (
    <MinimalShell>
      <StepUpCompleteView />
    </MinimalShell>
  );
}

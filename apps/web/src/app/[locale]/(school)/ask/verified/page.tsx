import { VerifiedAnswersScreen } from "@/features/ask/VerifiedAnswersScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.verified.title"));

/** US-802, FR-KB-030: verified answers, and adding one (kb.verified_answer.manage). */
export default function VerifiedAnswersPage() {
  return <VerifiedAnswersScreen />;
}

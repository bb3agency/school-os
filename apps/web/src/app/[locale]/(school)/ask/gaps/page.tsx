import { KnowledgeGapsScreen } from "@/features/ask/KnowledgeGapsScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.gaps.title"));

/** FA-06, FR-KB-085: questions Ask could not answer (kb.verified_answer.manage). */
export default function KnowledgeGapsPage() {
  return <KnowledgeGapsScreen />;
}

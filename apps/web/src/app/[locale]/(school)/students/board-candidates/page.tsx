import { BoardCandidatesScreen } from "@/features/board-candidates/BoardCandidatesScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("boardCandidates.title"));

/** FO-10 (FR-STU-026..029): board candidate medium, languages, subjects and photo tick. */
export default function BoardCandidatesPage() {
  return <BoardCandidatesScreen />;
}

import { HistoryScreen } from "@/features/ask/HistoryScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.history.title"));

/** FR-KB-012: all of your Ask chats (find, pin, rename, delete). */
export default function AskHistoryPage() {
  return <HistoryScreen />;
}

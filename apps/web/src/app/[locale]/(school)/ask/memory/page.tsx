import { MemoryScreen } from "@/features/ask/MemoryScreen";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.memory.title"));

/** Ask memory: your own preferences and work context (never facts about students or staff). */
export default function AskMemoryPage() {
  return <MemoryScreen />;
}

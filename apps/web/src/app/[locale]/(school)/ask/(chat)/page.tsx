import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.title"));

/**
 * US-801..803, FR-KB-001..012: a new chat. The chat itself is rendered by the (chat) layout,
 * so an answer keeps streaming while the URL moves on to its conversation.
 */
export default function AskPage() {
  return null;
}

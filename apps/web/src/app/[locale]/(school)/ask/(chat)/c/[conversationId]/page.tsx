import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("ask.title"));

/**
 * FR-KB-012: one conversation (deep link). The (chat) layout renders it from the URL; the
 * conversation id is read there (never trusted: the API answers 404 for anyone else's).
 */
export default function AskConversationPage() {
  return null;
}

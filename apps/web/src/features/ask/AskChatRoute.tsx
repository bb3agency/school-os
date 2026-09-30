"use client";

import { useParams } from "next/navigation";
import { AskChatProvider } from "./chat";
import { ChatScreen } from "./ChatScreen";

/** A conversation id from the URL, or null (a new chat). Not a UUID: treated as not found. */
function conversationParam(value: string | string[] | undefined): string | null {
  const id = Array.isArray(value) ? value[0] : value;
  return id ? decodeURIComponent(id) : null;
}

/** The chat for `/ask` (new) and `/ask/c/{conversationId}`, with its streaming controller. */
export function AskChatRoute() {
  const params = useParams<{ conversationId?: string | string[] }>();
  return (
    <AskChatProvider>
      <ChatScreen conversationId={conversationParam(params?.conversationId)} />
    </AskChatProvider>
  );
}

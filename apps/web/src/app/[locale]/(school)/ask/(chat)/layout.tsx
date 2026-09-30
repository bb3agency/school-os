import type { ReactNode } from "react";
import { AskChatRoute } from "@/features/ask/AskChatRoute";

/**
 * The Ask chat (`/ask` and `/ask/c/{id}`): one streaming controller and one chat screen for
 * both routes, so moving from a new chat to its conversation's URL keeps the answer, the
 * composer and focus. The pages themselves render nothing.
 */
export default function AskChatLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <AskChatRoute />
      {children}
    </>
  );
}

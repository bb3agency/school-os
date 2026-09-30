import { QueryClient } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { RenderResult } from "@testing-library/react";
import AskChatLayout from "@/app/[locale]/(school)/ask/(chat)/layout";
import AskPage from "@/app/[locale]/(school)/ask/(chat)/page";
import type { Locale } from "@/i18n/routing";
import { renderWithIntl } from "@/test/render";

/** Synthetic ids for the Ask chat tests (never real data). */
export const CHAT = {
  query: "0192f3a4-0000-7000-8000-00000000e001",
  query2: "0192f3a4-0000-7000-8000-00000000e002",
  query3: "0192f3a4-0000-7000-8000-00000000e003",
  conversation: "0192f3a4-0000-7000-8000-00000000e901",
  conversation2: "0192f3a4-0000-7000-8000-00000000e902",
  memory: "0192f3a4-0000-7000-8000-00000000e801",
  memory2: "0192f3a4-0000-7000-8000-00000000e802",
} as const;

export const ASK_ROUTE = "POST /bff/api/v1/knowledge/ask";
export const LIST_ROUTE = "GET /bff/api/v1/knowledge/conversations";
export const detailRoute = (id: string) => `GET /bff/api/v1/knowledge/conversations/${id}`;

export const sse = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** A text/event-stream response; chunks are split mid-event to exercise the parser. */
export function sseResponse(events: string[], { close = true } = {}): Response {
  const text = events.join("");
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const bytes = new TextEncoder().encode(text);
      const middle = Math.floor(bytes.length / 2);
      controller.enqueue(bytes.slice(0, middle));
      controller.enqueue(bytes.slice(middle));
      if (close) controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream; charset=utf-8" } });
}

/**
 * The Ask chat as the router renders `/ask` (layout with the controller + the empty page).
 * Its query client keeps unobserved entries (like the app's), since the chat seeds a new
 * conversation's cache before the thread observes it.
 */
export function renderChat(locale: Locale = "en"): RenderResult {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 60_000 } } });
  return renderWithIntl(
    <AskChatLayout>
      <AskPage />
    </AskChatLayout>,
    locale,
    client,
  );
}

/** Type a question into the composer and press the send button. */
export async function askQuestion(question = "When do exams begin?", buttonName = "Ask") {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/^Your question|^మీ ప్రశ్న/), question);
  await user.click(screen.getByRole("button", { name: buttonName }));
  return user;
}

export function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: CHAT.conversation,
    title: "Exam dates",
    pinned: false,
    created_at: "2026-09-28T05:00:00Z",
    updated_at: "2026-09-28T05:10:00Z",
    message_count: 1,
    version: 1,
    ...overrides,
  };
}

export function message(overrides: Record<string, unknown> = {}) {
  return {
    query_id: CHAT.query,
    question: "When do exams begin?",
    answer: "Exams begin on 22/09/2026. [1]",
    status: "answered",
    mode: "full",
    language: "en",
    citations: [
      {
        index: 1,
        source: "sos://doc/0192f3a4-0000-7000-8000-00000000c701/v2#p1",
        title: "Circular · Exam timings",
        snippet: "Exams begin on 22/09/2026 at 9 am …",
      },
    ],
    feedback: null,
    followups: [],
    created_at: "2026-09-28T05:10:00Z",
    superseded: false,
    ...overrides,
  };
}

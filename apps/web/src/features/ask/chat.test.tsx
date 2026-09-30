import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { intlErrors } from "@/test/render";
import {
  ASK_ROUTE,
  CHAT,
  askQuestion,
  detailRoute,
  message,
  renderChat,
  sse,
  sseResponse,
  summary,
} from "./chat-test-utils";

const nav = vi.hoisted(() => ({
  path: "/en/ask",
  params: { locale: "en" } as Record<string, string>,
  replace: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => nav.path,
    useRouter: () => ({
      push: nav.push,
      replace: nav.replace,
      refresh: vi.fn(),
      prefetch: vi.fn(),
    }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => nav.params,
  };
});

const DOC_SOURCE = "sos://doc/0192f3a4-0000-7000-8000-00000000c701/v2#p1";

let stub: BffStub;

function body(index = 0): Record<string, unknown> {
  return JSON.parse(stub.callsTo(ASK_ROUTE)[index]?.body ?? "{}") as Record<string, unknown>;
}

function meta(extra: Record<string, unknown> = {}, query: string = CHAT.query) {
  return sse("meta", {
    query_id: query,
    language: "en",
    mode: "full",
    conversation_id: CHAT.conversation,
    title: "Exam dates",
    ...extra,
  });
}

const finalAnswer = [
  sse("final", {
    text: "Exams begin on **22/09/2026**. [1]\n\n- Class 6: 9 am [1]\n- Class 7: 10 am",
    replaced: false,
    status: "answered",
    mode: "full",
  }),
  sse("citation", {
    index: 1,
    source: DOC_SOURCE,
    title: "Circular · Exam timings",
    snippet: "Exams begin on 22/09/2026 at 9 am …",
  }),
  sse("done", { latency_ms: 4200, cited_sources: 1, status: "answered", mode: "full" }),
];

beforeEach(() => {
  nav.path = "/en/ask";
  nav.params = { locale: "en" };
  nav.replace.mockReset();
  nav.push.mockReset();
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(["kb.ask", "document.read", "kb.verified_answer.manage"]));
});

afterEach(() => {
  uninstallBffStub();
  window.location.hash = "";
  expect(intlErrors).toEqual([]);
});

describe("Ask chat: a new conversation (FR-KB-008, FR-KB-012)", () => {
  it("empty chat greets the member and offers example cards that only fill the box", async () => {
    renderChat();
    expect(await screen.findByText(/^Good (morning|afternoon|evening), Office$/)).toBeVisible();
    const examples = screen.getByRole("list", { name: /^Example questions/ });
    expect(within(examples).getAllByRole("button")).toHaveLength(4);
    await userEvent.setup().click(within(examples).getAllByRole("button")[0] as HTMLElement);
    expect(screen.getByLabelText(/^Your question/)).toHaveValue(
      "When do the half-yearly exams begin?",
    );
    expect(stub.callsTo(ASK_ROUTE)).toHaveLength(0);
  });

  it("the user's question appears at once, then status steps, then the answer (optimistic UI)", async () => {
    stub.routes[ASK_ROUTE] = () =>
      sseResponse(
        [
          meta(),
          sse("status", { step: "understanding" }),
          sse("status", { step: "searching_documents", tool: "search_documents", count: 4 }),
        ],
        { close: false },
      );
    renderChat();
    await askQuestion();
    // The question is in the thread before any answer.
    const log = await screen.findByRole("log", { name: "Conversation" });
    expect(within(log).getByText("When do exams begin?")).toBeVisible();
    expect(await screen.findByText("Searching school documents… 4 found")).toBeInTheDocument();
    // The status region announces the step without the changing count.
    expect(document.querySelector("[role='status'][aria-live='polite']")).toHaveTextContent(
      "Searching school documents…",
    );
  });

  it("moves the URL to the new conversation and lists it (meta.conversation_id)", async () => {
    stub.routes[ASK_ROUTE] = () => sseResponse([meta(), ...finalAnswer]);
    renderChat();
    await askQuestion();
    await screen.findByText("The answer is ready.");
    expect(nav.replace).toHaveBeenCalledWith(`/en/ask/c/${CHAT.conversation}`, { scroll: false });
    // The header shows the conversation's title (the page's h1).
    expect(await screen.findByRole("heading", { level: 1, name: "Exam dates" })).toBeVisible();
  });

  it("renders the checked answer as markdown with citation chips and a Worked-for summary", async () => {
    stub.routes[ASK_ROUTE] = () =>
      sseResponse([
        meta(),
        sse("status", { step: "searching_documents", count: 3 }),
        ...finalAnswer,
      ]);
    renderChat();
    await askQuestion();
    const answer = await screen.findByRole("article", { name: "Answer" });
    expect(await within(answer).findByText("22/09/2026", { selector: "strong" })).toBeVisible();
    expect(within(answer).getAllByRole("listitem").length).toBeGreaterThanOrEqual(2);
    const chips = await within(answer).findAllByRole("link", {
      name: "Source 1: Circular · Exam timings",
    });
    expect(chips).toHaveLength(2);
    expect(within(answer).getByText("Worked for 4 seconds · 1 source")).toBeVisible();
    // The disclosure lists the steps.
    await userEvent.setup().click(within(answer).getByText("Worked for 4 seconds · 1 source"));
    expect(within(answer).getByRole("list", { name: "Steps SchoolOS took" })).toHaveTextContent(
      "Searching school documents… 3 found",
    );
  });

  it("a chip shows its source in a popover on focus; Escape closes it (WCAG 1.4.13)", async () => {
    stub.routes[ASK_ROUTE] = () => sseResponse([meta(), ...finalAnswer]);
    renderChat();
    await askQuestion();
    const answer = await screen.findByRole("article", { name: "Answer" });
    const [chip] = await within(answer).findAllByRole("link", {
      name: "Source 1: Circular · Exam timings",
    });
    chip?.focus();
    const open = await screen.findByRole("link", {
      name: "Open: Circular · Exam timings (open the document)",
    });
    expect(open).toHaveAttribute("href", "/en/documents/0192f3a4-0000-7000-8000-00000000c701");
    expect(chip).toHaveAttribute("aria-describedby");
    await userEvent.setup().keyboard("{Escape}");
    await waitFor(() =>
      expect(
        screen.queryByRole("link", { name: "Open: Circular · Exam timings (open the document)" }),
      ).toBeNull(),
    );
    expect(chip).toHaveFocus();
  });

  it("follow-up chips appear under the latest answer and send at once in the conversation", async () => {
    let n = 0;
    stub.routes[ASK_ROUTE] = () =>
      sseResponse([
        meta({}, n++ === 0 ? CHAT.query : CHAT.query2),
        ...finalAnswer,
        sse("followups", {
          questions: ["When do Class 8 exams begin?", "  ", "Is there a timetable?"],
        }),
      ]);
    renderChat();
    await askQuestion();
    const group = await screen.findByRole("group", { name: "Suggested next questions" });
    const chips = within(group).getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "When do Class 8 exams begin?",
      "Is there a timetable?",
    ]);
    await userEvent.setup().click(chips[1] as HTMLElement);
    await waitFor(() => expect(stub.callsTo(ASK_ROUTE)).toHaveLength(2));
    expect(body(1)).toEqual({
      question: "Is there a timetable?",
      conversation_id: CHAT.conversation,
    });
  });

  it("regenerate sends regenerate_of and keeps the earlier answer as version 1 of 2", async () => {
    let n = 0;
    stub.routes[ASK_ROUTE] = () => {
      n += 1;
      return sseResponse([
        meta({}, n === 1 ? CHAT.query : CHAT.query2),
        sse("final", {
          text: n === 1 ? "First answer. [1]" : "Second answer. [1]",
          replaced: false,
          status: "answered",
          mode: "full",
        }),
        sse("citation", { index: 1, source: DOC_SOURCE, title: "Circular", snippet: "x" }),
        sse("done", { latency_ms: 1000, cited_sources: 1, status: "answered", mode: "full" }),
      ]);
    };
    renderChat();
    const user = await askQuestion();
    const log = await screen.findByRole("log");
    await within(log).findByText("First answer.");
    await user.click(screen.getByRole("button", { name: "Ask again for a new answer" }));
    await within(log).findByText("Second answer.");
    expect(body(1)).toEqual({
      question: "When do exams begin?",
      conversation_id: CHAT.conversation,
      regenerate_of: CHAT.query,
    });
    expect(within(log).queryByText("First answer.")).toBeNull();
    expect(within(log).getAllByRole("article")).toHaveLength(1);
    const versions = screen.getByRole("group", { name: "Answer 2 of 2" });
    await user.click(within(versions).getByRole("button", { name: "Previous answer" }));
    expect(await within(log).findByText("First answer.")).toBeVisible();
    expect(screen.getByRole("group", { name: "Answer 1 of 2" })).toBeInTheDocument();
  });

  it("the last question can be edited in place (edit_of); Escape cancels", async () => {
    let n = 0;
    stub.routes[ASK_ROUTE] = () =>
      sseResponse([meta({}, n++ === 0 ? CHAT.query : CHAT.query2), ...finalAnswer]);
    renderChat();
    const user = await askQuestion();
    await screen.findByText("The answer is ready.");
    await user.click(screen.getByRole("button", { name: "Edit question" }));
    let box = screen.getByLabelText("Edit your question");
    expect(box).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByLabelText("Edit your question")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Edit question" }));
    box = screen.getByLabelText("Edit your question");
    await user.clear(box);
    await user.type(box, "When do Class 7 exams begin?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(stub.callsTo(ASK_ROUTE)).toHaveLength(2));
    expect(body(1)).toEqual({
      question: "When do Class 7 exams begin?",
      conversation_id: CHAT.conversation,
      edit_of: CHAT.query,
    });
  });

  it("copy puts plain text with numbered sources on the clipboard", async () => {
    stub.routes[ASK_ROUTE] = () => sseResponse([meta(), ...finalAnswer]);
    renderChat();
    // user-event provides the clipboard (navigator.clipboard) for the test.
    const user = await askQuestion();
    await screen.findByText("The answer is ready.");
    await user.click(screen.getByRole("button", { name: "Copy answer" }));
    expect(await screen.findByRole("button", { name: "Answer copied" })).toBeInTheDocument();
    expect(await navigator.clipboard.readText()).toBe(
      "Exams begin on 22/09/2026. [1]\n\n- Class 6: 9 am [1]\n- Class 7: 10 am\n\nSources:\n[1] Circular · Exam timings, page 1",
    );
  });

  it("feedback thumbs send helpful at once and show pressed state", async () => {
    stub.routes[ASK_ROUTE] = () => sseResponse([meta(), ...finalAnswer]);
    stub.routes[`POST /bff/api/v1/knowledge/queries/${CHAT.query}/feedback`] = () =>
      Response.json({
        query_id: CHAT.query,
        feedback: "helpful",
        reason: null,
        recorded_at: "2026-09-28T05:00:00Z",
      });
    renderChat();
    const user = await askQuestion();
    const up = await screen.findByRole("button", { name: "Yes, helpful" });
    await user.click(up);
    expect(up).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText(/Thank you/)).toBeInTheDocument();
  });

  it("a reused answer says so and offers a fresh one (meta.cached)", async () => {
    let n = 0;
    stub.routes[ASK_ROUTE] = () =>
      sseResponse([
        meta(
          n++ === 0 ? { cached: true, cached_from: CHAT.query3 } : {},
          n === 1 ? CHAT.query : CHAT.query2,
        ),
        ...finalAnswer,
      ]);
    renderChat();
    const user = await askQuestion();
    expect(await screen.findByText("Answered from a recent identical question.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Get a fresh answer" }));
    await waitFor(() => expect(stub.callsTo(ASK_ROUTE)).toHaveLength(2));
    expect(body(1).regenerate_of).toBe(CHAT.query);
  });

  it("restores the question into the box when the request fails", async () => {
    stub.routes[ASK_ROUTE] = () => problem(503, "ai_unavailable");
    renderChat();
    await askQuestion("Keep my words");
    expect(await screen.findByText("AI answers are unavailable")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText(/^Your question/)).toHaveValue("Keep my words"),
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("a stopped answer keeps its partial text, marked Stopped, with ask again", async () => {
    stub.routes[ASK_ROUTE] = () =>
      sseResponse([meta(), sse("delta", { text: "Exams begin on 22" })], { close: false });
    renderChat();
    const user = await askQuestion();
    const stop = await screen.findByRole("button", { name: "Stop" });
    await screen.findByRole("group", { name: "Draft answer, not checked yet" });
    stop.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Stopped")).toBeVisible();
    expect(screen.getByText(/Exams begin on 22/)).toBeVisible();
    expect(
      screen.getByText(/You stopped this answer\. What you see was not checked/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask again for a new answer" })).toBeInTheDocument();
  });
});

describe("Ask chat: memory in the chat", () => {
  it("shows 'Memory updated' with a link to Manage memory", async () => {
    stub.routes[ASK_ROUTE] = () =>
      sseResponse([
        meta(),
        ...finalAnswer,
        sse("memory", { action: "saved", item_id: CHAT.memory, text: "I teach Class 7" }),
      ]);
    renderChat();
    await askQuestion();
    expect(await screen.findByText("Memory updated")).toBeVisible();
    expect(screen.getAllByRole("link", { name: "Manage memory" })[0]).toHaveAttribute(
      "href",
      "/en/ask/memory",
    );
  });

  it("a suggestion is saved only when the member confirms it; Dismiss deletes it", async () => {
    stub.routes[ASK_ROUTE] = () =>
      sseResponse([
        meta(),
        ...finalAnswer,
        sse("memory", { action: "suggested", item_id: CHAT.memory, text: "Prefers Telugu" }),
        sse("memory", { action: "suggested", item_id: CHAT.memory2, text: "Teaches Class 7" }),
      ]);
    stub.routes[`POST /bff/api/v1/knowledge/memories/${CHAT.memory}/confirm`] = () =>
      new Response(null, { status: 204 });
    stub.routes[`DELETE /bff/api/v1/knowledge/memories/${CHAT.memory2}`] = () =>
      new Response(null, { status: 204 });
    renderChat();
    const user = await askQuestion();
    const cards = await screen.findAllByRole("group", { name: "Remember this?" });
    expect(cards).toHaveLength(2);
    expect(stub.callsTo(`POST /bff/api/v1/knowledge/memories/${CHAT.memory}/confirm`)).toHaveLength(
      0,
    );
    await user.click(screen.getByRole("button", { name: "Save: Prefers Telugu" }));
    expect(await screen.findByText("Saved to memory.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Dismiss: Teaches Class 7" }));
    expect(await screen.findByText("Not remembered.")).toBeVisible();
    expect(stub.callsTo(`POST /bff/api/v1/knowledge/memories/${CHAT.memory}/confirm`)).toHaveLength(
      1,
    );
    expect(stub.callsTo(`DELETE /bff/api/v1/knowledge/memories/${CHAT.memory2}`)).toHaveLength(1);
  });

  it("the composer shows a memory indicator only when memory is on", async () => {
    stub.routes["GET /bff/api/v1/knowledge/memory-settings"] = () =>
      Response.json({ enabled: true, school_enabled: true });
    const { unmount } = renderChat();
    const link = await screen.findByRole("link", { name: /Memory on/ });
    expect(link).toHaveAttribute("href", "/en/ask/memory");
    expect(link).toHaveAccessibleDescription(/Ask uses what it remembers/);
    unmount();
    stub.routes["GET /bff/api/v1/knowledge/memory-settings"] = () =>
      Response.json({ enabled: true, school_enabled: false });
    renderChat();
    await screen.findByLabelText(/^Your question/);
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/knowledge/memory-settings").length).toBeGreaterThan(1),
    );
    expect(screen.queryByRole("link", { name: /Memory on/ })).toBeNull();
  });
});

describe("Ask chat: an existing conversation (/ask/c/{id})", () => {
  beforeEach(() => {
    nav.path = `/en/ask/c/${CHAT.conversation}`;
    nav.params = { locale: "en", conversationId: CHAT.conversation };
  });

  it("loads the thread with versions, withheld sources and the summarised marker", async () => {
    stub.routes[detailRoute(CHAT.conversation)] = () =>
      Response.json({
        ...summary({ message_count: 3 }),
        messages: [
          message({ query_id: CHAT.query, answer: "Old answer. [1]", superseded: true }),
          message({ query_id: CHAT.query2, answer: "New answer. [1]", feedback: "helpful" }),
          message({
            query_id: CHAT.query3,
            question: "And the timetable?",
            answer: "It is on the notice board. [1]",
            summarized: true,
            citations: [
              { index: 1, source: DOC_SOURCE, title: null, snippet: null, withheld: true },
            ],
          }),
        ],
      });
    renderChat();
    expect(await screen.findByRole("heading", { level: 1, name: "Exam dates" })).toBeVisible();
    expect(await screen.findByText("New answer.")).toBeVisible();
    expect(screen.queryByText("Old answer.")).toBeNull();
    expect(screen.getByRole("group", { name: "Answer 2 of 2" })).toBeInTheDocument();
    expect(screen.getAllByText("Source you can no longer open").length).toBeGreaterThan(0);
    expect(
      screen.getByText("You no longer have access to this source, so its text is hidden."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Earlier messages were summarised to keep answers focused"),
    ).toBeInTheDocument();
    // Stored feedback shows as pressed.
    expect(screen.getAllByRole("button", { name: "Yes, helpful" })[0]).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("a past-chat source links to that message; a #m- link highlights it on load", async () => {
    window.location.hash = `#m-${CHAT.query}`;
    stub.routes[detailRoute(CHAT.conversation)] = () =>
      Response.json({
        ...summary(),
        messages: [
          message({
            citations: [
              {
                index: 1,
                source: `sos://conversation/${CHAT.conversation2}#q${CHAT.query2}`,
                title: "Your chat · Fees",
                snippet: "Fees are due on 10/10.",
              },
            ],
          }),
        ],
      });
    renderChat();
    const card = await screen.findByRole("link", { name: "Your chat · Fees (open the chat)" });
    expect(card).toHaveAttribute("href", `/en/ask/c/${CHAT.conversation2}#m-${CHAT.query2}`);
    const target = document.getElementById(`m-${CHAT.query}`);
    await waitFor(() => expect(target).toHaveClass("chat-highlight"));
    expect(target).toHaveFocus();
  });

  it("says a missing (or someone else's) chat was not found", async () => {
    stub.routes[detailRoute(CHAT.conversation)] = () => problem(404, "not_found");
    renderChat();
    expect(await screen.findByText("Chat not found")).toBeVisible();
    expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/en/ask");
  });

  it("pins, renames and deletes from the Chat options menu", async () => {
    stub.routes[detailRoute(CHAT.conversation)] = () =>
      Response.json({ ...summary(), messages: [message()] });
    const PATCH = `PATCH /bff/api/v1/knowledge/conversations/${CHAT.conversation}`;
    const DELETE = `DELETE /bff/api/v1/knowledge/conversations/${CHAT.conversation}`;
    stub.routes[PATCH] = () => Response.json(summary({ pinned: true, version: 2 }));
    stub.routes[DELETE] = () => new Response(null, { status: 204 });
    renderChat();
    const user = userEvent.setup();
    const options = await screen.findByRole("button", { name: "Chat options" });
    await screen.findByText("Exams begin on 22/09/2026.");
    await user.click(options);
    expect(options).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(1));
    expect(stub.callsTo(PATCH)[0]?.headers.get("if-match")).toBe('W/"1"');
    expect(JSON.parse(stub.callsTo(PATCH)[0]?.body ?? "{}")).toEqual({ pinned: true });

    await user.click(options);
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename" });
    const field = within(dialog).getByLabelText("Chat title");
    await user.clear(field);
    await user.type(field, "Half-yearly exams");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(2));
    expect(JSON.parse(stub.callsTo(PATCH)[1]?.body ?? "{}")).toEqual({
      title: "Half-yearly exams",
    });

    await user.click(options);
    await user.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = await screen.findByRole("dialog", { name: "Delete this chat?" });
    await user.click(within(confirm).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(stub.callsTo(DELETE)).toHaveLength(1));
    expect(nav.push).toHaveBeenCalledWith("/en/ask");
  });
});

describe("Ask chat: keyboard shortcuts", () => {
  it("/ moves to the question box; Alt+N starts a new chat", async () => {
    nav.path = `/en/ask/c/${CHAT.conversation}`;
    nav.params = { locale: "en", conversationId: CHAT.conversation };
    stub.routes[detailRoute(CHAT.conversation)] = () =>
      Response.json({ ...summary(), messages: [message()] });
    renderChat();
    const user = userEvent.setup();
    const box = await screen.findByLabelText(/^Your question/);
    await screen.findByText("Exams begin on 22/09/2026.");
    (document.activeElement as HTMLElement | null)?.blur();
    await user.keyboard("/");
    expect(box).toHaveFocus();
    expect(box).toHaveValue("");
    // Typing "/" in the box is just a character.
    await user.keyboard("/");
    expect(box).toHaveValue("/");
    await user.keyboard("{Alt>}n{/Alt}");
    expect(nav.push).toHaveBeenCalledWith("/en/ask");
  });
});

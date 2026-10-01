import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { setDocumentDownloadOpenerForTesting } from "@/features/documents/data";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import { fakeAadhaar, ID, me } from "@/test/records-fixtures";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { renderChat } from "./chat-test-utils";
import AskSearchPage from "@/app/[locale]/(school)/ask/search/page";
import VerifiedAnswersPage from "@/app/[locale]/(school)/ask/verified/page";
import { verifiedFieldMap } from "./VerifiedAnswerDialog";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/ask",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

type Schemas = components["schemas"];

const ASK = "POST /bff/api/v1/knowledge/ask";
const DOC = ID.doc;
const QUERY = "0192f3a4-0000-7000-8000-00000000e001";
const VERIFIED = "0192f3a4-0000-7000-8000-00000000e101";
const DOC_SOURCE = `sos://doc/${DOC}/v2#p1`;
const STUDENT_SOURCE = `sos://student/${ID.student}/field/admission_date?src=register`;

let stub: BffStub;

function setMe(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** A text/event-stream response; chunks are split mid-event to exercise the parser. */
function sseResponse(events: string[], { close = true } = {}) {
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
  return new Response(body, {
    headers: { "content-type": "text/event-stream; charset=utf-8" },
  });
}

const answered = [
  sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
  sse("token", { text: "Exams begin on 22/09/2026. [1]" }),
  sse("token", { text: "Sita joined class 6 in 2024. [2]" }),
  sse("citation", {
    index: 1,
    source: DOC_SOURCE,
    title: "Circular · Exam timings",
    snippet: "Exams begin on 22/09/2026 at 9 am …",
  }),
  sse("citation", {
    index: 2,
    source: STUDENT_SOURCE,
    title: "Admission register",
    snippet: "2024",
  }),
  sse("done", { latency_ms: 4120, cited_sources: 2 }),
];

async function ask(question = "When do exams begin?") {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/^Your question/), question);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  return user;
}

function body(key: string, index = 0): Record<string, unknown> {
  return JSON.parse(stub.callsTo(key)[index]?.body ?? "{}") as Record<string, unknown>;
}

beforeEach(() => {
  stub = installBffStub("staff");
  setMe(["kb.ask", "document.read"]);
});

afterEach(() => {
  setDocumentDownloadOpenerForTesting(null);
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("Ask the school (US-801, FR-KB-005, FR-KB-008)", () => {
  it("streams the answer through the BFF with CSRF, the question only in the body", async () => {
    stub.routes[ASK] = () => sseResponse(answered);
    renderChat();
    await ask();

    expect(await screen.findByText("The answer is ready.")).toBeInTheDocument();
    const [call] = stub.callsTo(ASK);
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(call?.headers.get("accept")).toBe("text/event-stream");
    expect(call?.url.search).toBe("");
    // A new chat: no conversation yet (FR-KB-012); the old per-page session_id is gone.
    expect(body(ASK)).toEqual({ question: "When do exams begin?" });

    const answer = screen.getByRole("article", { name: "Answer" });
    expect(within(answer).getByText(/Exams begin on 22\/09\/2026\./)).toBeInTheDocument();
    // The thread is a log that never reads each word; a polite status region announces the
    // checked answer once (NFR-A11Y-001).
    expect(screen.getByRole("log", { name: "Conversation" })).toHaveAttribute("aria-live", "off");
    const status = screen.getByText("The answer is ready.").closest("[role='status']");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent(/Exams begin on 22\/09\/2026\. Sita joined class 6 in 2024\./);
    expect(status).not.toHaveTextContent("[1]");
    // Source chips: [n] markers link to the listed sources (CLAUDE.md §10).
    expect(
      await within(answer).findByRole("link", { name: "Source 1: Circular · Exam timings" }),
    ).toHaveAttribute("href", `#ask-${QUERY}-source-1`);
    expect(
      within(answer).getByRole("link", { name: /Circular · Exam timings \(open the document\)/ }),
    ).toHaveAttribute("href", `/documents/${DOC}`);
    expect(
      within(answer).getByRole("link", { name: /Admission register \(open the student record\)/ }),
    ).toHaveAttribute("href", `/students/${ID.student}`);
    expect(within(answer).getByText("page 1")).toBeInTheDocument();
  });

  it("example questions only fill the question box; nothing is sent until Ask (US-801)", async () => {
    renderChat();
    const user = userEvent.setup();
    const examples = await screen.findByRole("list", { name: /^Example questions/ });
    await user.click(
      within(examples).getByRole("button", { name: "How many students are in Class 6?" }),
    );
    const box = screen.getByLabelText(/^Your question/);
    expect(box).toHaveValue("How many students are in Class 6?");
    expect(box).toHaveFocus();
    expect(stub.callsTo(ASK)).toHaveLength(0);
  });

  it("a follow-up is asked in the conversation the first answer started (FR-KB-012)", async () => {
    const CONVERSATION = "0192f3a4-0000-7000-8000-00000000e901";
    const ids = [QUERY, "0192f3a4-0000-7000-8000-00000000e002"];
    let n = 0;
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", {
          query_id: ids[n++ % 2],
          language: "en",
          mode: "full",
          conversation_id: CONVERSATION,
        }),
        ...answered.slice(1),
      ]);
    renderChat();
    const user = await ask("First?");
    await screen.findByText("The answer is ready.");
    // The composer is cleared after sending and keeps focus for the next question.
    const box = screen.getByLabelText(/^Your question/);
    expect(box).toHaveValue("");
    await user.type(box, "Second?");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(stub.callsTo(ASK)).toHaveLength(2));
    expect(body(ASK, 0)).toEqual({ question: "First?" });
    expect(body(ASK, 1)).toEqual({ question: "Second?", conversation_id: CONVERSATION });
    // Both turns are in the thread.
    expect(await screen.findAllByRole("article", { name: "Answer" })).toHaveLength(2);
  });

  it("downloads exactly the cited version of a document (US-801 AC4)", async () => {
    const opened: string[] = [];
    setDocumentDownloadOpenerForTesting((url) => opened.push(url));
    stub.routes[ASK] = () => sseResponse(answered);
    stub.routes[`GET /bff/api/v1/documents/${DOC}/download-url`] = () =>
      Response.json({ url: "https://files.example/presigned", expires_at: "2026-09-28T05:00:00Z" });
    renderChat();
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Download version 2" }));
    await waitFor(() => expect(opened).toEqual(["https://files.example/presigned"]));
    const [call] = stub.callsTo(`GET /bff/api/v1/documents/${DOC}/download-url`);
    expect(call?.url.searchParams.get("version")).toBe("2");
  });

  it("renders model output as text: no HTML, no links from the prose (docs/06 §9 rule 5)", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("token", {
          text: 'See <img src=x onerror="alert(1)"><a href="https://evil.example">this</a> and [the form](https://evil.example/f) https://evil.example/raw [1]',
        }),
        sse("citation", { index: 1, source: DOC_SOURCE, title: "<b>Circular</b>", snippet: "x" }),
        sse("citation", { index: 2, source: "https://evil.example", title: "Evil", snippet: "y" }),
        sse("done", { latency_ms: 10, cited_sources: 2 }),
      ]);
    const { container } = renderChat();
    await ask();
    await screen.findByText("The answer is ready.");
    expect(container.querySelector("img, b, script")).toBeNull();
    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.filter((href) => href.includes("evil"))).toEqual([]);
    const answer = screen.getByRole("article", { name: "Answer" });
    expect(within(answer).getByText(/https:\/\/evil\.example\/raw/)).toBeInTheDocument();
    // Markdown link targets are dropped; the label stays as text (docs/06 §9 rule 5).
    expect(within(answer).getByText(/and the form/)).toBeInTheDocument();
    // A source that is not a sos:// URI is shown without a link.
    expect(screen.getByText("Evil")).toBeInTheDocument();
    expect(screen.getByText("This source cannot be opened here.")).toBeInTheDocument();
  });

  it("says 'not found in the school records you can access' when nothing is cited (US-801 AC3, US-803)", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("token", { text: "I could not find this in the school records you can access." }),
        sse("done", { latency_ms: 800, cited_sources: 0 }),
      ]);
    renderChat();
    await ask();
    expect(
      await screen.findByText("Not found in the school records you can access"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("shows search-only passages plainly when the AI budget is used up (FR-KB-011)", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "search_only" }),
        sse("error", { type: "ai_budget_exhausted", message_key: "kb.errors.budget" }),
        sse("citation", { index: 1, source: DOC_SOURCE, title: "Circular", snippet: "Exams at 9" }),
        sse("done", { latency_ms: 300, cited_sources: 1 }),
      ]);
    renderChat();
    await ask();
    expect(await screen.findByText("AI answers are not available right now")).toBeInTheDocument();
    expect(screen.getByText(/AI budget for this month is used up/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Matching passages" })).toBeInTheDocument();
    expect(screen.getByText("Exams at 9")).toBeInTheDocument();
  });

  it.each([
    ["kb.errors.disabled", /switched off for this school/],
    ["kb.errors.unavailable", /temporarily unavailable/],
    ["kb.errors.brand_new", /temporarily unavailable/],
  ])("explains the search-only reason %s", async (key, text) => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "search_only" }),
        sse("error", { type: "x", message_key: key }),
        sse("done", { latency_ms: 300, cited_sources: 0 }),
      ]);
    renderChat();
    await ask();
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByText("No passages matched your question. Try other words.")).toBeVisible();
  });

  it("ignores event types it does not know (the API may add token deltas)", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("progress", { step: "tools" }),
        sse("token", { text: "Exams begin soon. [1]" }),
        sse("citation", { index: 1, source: DOC_SOURCE, title: "Circular", snippet: "x" }),
        ": comment\n\n",
        sse("done", { latency_ms: 10, cited_sources: 1 }),
      ]);
    renderChat();
    await ask();
    const answer = await screen.findByRole("article", { name: "Answer" });
    expect(await within(answer).findByText(/Exams begin soon\./)).toBeInTheDocument();
  });

  it("search-only via final/done even though meta says full (docs/06 §5.1, FR-KB-011)", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("delta", { text: "Exams begin on 22/09" }),
        sse("error", { type: "ai_budget_exhausted", message_key: "kb.errors.budget" }),
        sse("final", { text: "", replaced: true, status: "search_only", mode: "search_only" }),
        sse("citation", { index: 1, source: DOC_SOURCE, title: "Circular", snippet: "Exams at 9" }),
        sse("done", {
          latency_ms: 300,
          cited_sources: 1,
          status: "search_only",
          mode: "search_only",
        }),
      ]);
    renderChat();
    await ask();
    expect(await screen.findByText("AI answers are not available right now")).toBeInTheDocument();
    expect(screen.getByText(/AI budget for this month is used up/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Matching passages" })).toBeInTheDocument();
    expect(screen.queryByText(/Exams begin on 22\/09/)).toBeNull();
    expect(screen.queryByText("Not found in the school records you can access")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save as verified answer" })).toBeNull();
  });

  it("shows streamed delta text as a marked, unchecked preview without links", async () => {
    stub.routes[ASK] = () =>
      sseResponse(
        [
          sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
          sse("delta", { text: "Exams begin " }),
          sse("delta", { text: "on 22/09/2026. [1]" }),
        ],
        { close: false },
      );
    renderChat();
    await ask();
    const preview = await screen.findByRole("group", { name: "Draft answer, not checked yet" });
    expect(preview).toHaveTextContent("Exams begin on 22/09/2026. [1]");
    expect(within(preview).queryByRole("link")).toBeNull();
    // The live status line (visual) and the status region both say what is happening.
    expect(screen.getAllByText("Writing the answer…").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("article", { name: "Answer" })).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button", { name: "Yes, helpful" })).toBeNull();
    // Stop is always there while it streams.
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it("final replaces the preview, later tokens are ignored, and a changed answer says so", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("delta", { text: "Exams begin on 21/09/2026 [2] and end soon." }),
        sse("final", {
          text: "Exams begin on 22/09/2026. [1]",
          replaced: true,
          status: "answered",
          mode: "full",
        }),
        sse("token", { text: "Exams begin on 22/09/2026. [1]" }),
        sse("citation", { index: 1, source: DOC_SOURCE, title: "Circular", snippet: "x" }),
        sse("done", { latency_ms: 900, cited_sources: 1, status: "answered", mode: "full" }),
      ]);
    renderChat();
    await ask();
    expect(await screen.findByText("The answer is ready.")).toBeInTheDocument();
    const answer = screen.getByRole("article", { name: "Answer" });
    expect(within(answer).getAllByText(/Exams begin on 22\/09\/2026\./)).toHaveLength(1);
    expect(within(answer).queryByText(/21\/09\/2026/)).toBeNull();
    expect(screen.queryByRole("group", { name: "Draft answer, not checked yet" })).toBeNull();
    expect(
      await within(answer).findByRole("link", { name: "Source 1: Circular" }),
    ).toBeInTheDocument();
    expect(
      within(answer).getByText(
        "The draft was checked against the sources and changed to what they support.",
      ),
    ).toBeInTheDocument();
  });

  it("says when the question was refused (done.status refused)", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("final", { text: "Cannot help.", replaced: false, status: "refused", mode: "full" }),
        sse("token", { text: "Cannot help." }),
        sse("done", { latency_ms: 90, cited_sources: 0, status: "refused", mode: "full" }),
      ]);
    renderChat();
    await ask();
    expect(
      await screen.findByText("This question can't be answered from the school records"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Cannot help.")).toBeNull();
  });

  it("an internal error (done.status error) says to ask again, with no feedback", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("delta", { text: "Exams begin" }),
        sse("error", { type: "internal_error", message_key: "kb.errors.internal" }),
        sse("done", { latency_ms: 50, cited_sources: 0, status: "error", mode: "full" }),
      ]);
    renderChat();
    await ask();
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText(/could not finish this answer\. Ask again/)).toBeInTheDocument();
    expect(screen.getByText("The answer could not be finished.")).toBeInTheDocument();
    expect(screen.queryByText("Exams begin")).toBeNull();
    expect(screen.queryByText("Was this answer helpful?")).toBeNull();
  });

  it("shows a student count source as a chip without a link (sos://count)", async () => {
    const COUNT = "0192f3a4-0000-7000-8000-00000000e201";
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("final", {
          text: "Class 6 has 42 students. [1]",
          replaced: false,
          status: "answered",
          mode: "full",
        }),
        sse("citation", {
          index: 1,
          source: `sos://count/${COUNT}`,
          title: "Students enrolled by class",
          snippet: "Class 6: 42",
        }),
        sse("done", { latency_ms: 90, cited_sources: 1, status: "answered", mode: "full" }),
      ]);
    const { container } = renderChat();
    await ask();
    expect(await screen.findByText("Students enrolled by class")).toBeInTheDocument();
    expect(screen.getByText("Student count (numbers only)")).toBeInTheDocument();
    expect(screen.queryByText("This source cannot be opened here.")).toBeNull();
    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.filter((href) => href.includes(COUNT))).toEqual([]);
    expect(
      screen.getByRole("link", { name: "Source 1: Students enrolled by class" }),
    ).toBeVisible();
  });

  it("new stream states and kb.errors.* work in Telugu without missing messages", async () => {
    for (const events of [
      [
        sse("meta", { query_id: QUERY, language: "te", mode: "full" }),
        sse("error", { type: "internal_error", message_key: "kb.errors.internal" }),
        sse("done", { latency_ms: 5, cited_sources: 0, status: "error", mode: "full" }),
      ],
      [
        sse("meta", { query_id: QUERY, language: "te", mode: "full" }),
        sse("final", { text: "x", replaced: true, status: "refused", mode: "full" }),
        sse("done", { latency_ms: 5, cited_sources: 0, status: "refused", mode: "full" }),
      ],
      [
        sse("meta", { query_id: QUERY, language: "te", mode: "full" }),
        sse("delta", { text: "పరీక్షలు" }),
      ],
    ]) {
      stub.routes[ASK] = () => sseResponse(events);
      const { unmount } = renderChat("te");
      const user = userEvent.setup();
      await user.type(await screen.findByLabelText(/^మీ ప్రశ్న/), "ప్రశ్న?");
      await user.click(screen.getByRole("button", { name: "అడగండి" }));
      await screen.findByRole("article", { name: "సమాధానం" });
      await waitFor(() =>
        expect(document.querySelector("[role='status'][aria-live='polite']")).not.toHaveTextContent(
          /^$/,
        ),
      );
      unmount();
    }
  });

  it("explains 429 ai_rate_limited in plain words", async () => {
    stub.routes[ASK] = () => problem(429, "ai_rate_limited");
    renderChat();
    await ask();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many questionsYou asked many questions in the last minute. Wait a minute, then ask again.",
    );
  });

  it("says the answer was cut off when the stream ends before done", async () => {
    stub.routes[ASK] = () =>
      sseResponse([
        sse("meta", { query_id: QUERY, language: "en", mode: "full" }),
        sse("token", { text: "Exams begin" }),
      ]);
    renderChat();
    await ask();
    expect(await screen.findByText("The answer was cut off")).toBeInTheDocument();
    expect(screen.queryByText("Was this answer helpful?")).toBeNull();
  });

  it("Stop aborts the answer and returns focus to the question (keyboard only)", async () => {
    let signal: AbortSignal | undefined;
    stub.routes[ASK] = (request) => {
      signal = request.signal;
      return sseResponse([sse("meta", { query_id: QUERY, language: "en", mode: "full" })], {
        close: false,
      });
    };
    renderChat();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^Your question/), "When?");
    await user.keyboard("{Control>}{Enter}{/Control}");
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect((await screen.findAllByText("Writing the answer…")).length).toBeGreaterThan(0);
    stop.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("You stopped the answer.")).toBeInTheDocument();
    expect(signal?.aborted).toBe(true);
    expect(screen.getByLabelText(/^Your question/)).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("refuses a full Aadhaar number before sending anything (invariant 4)", async () => {
    renderChat();
    await ask(`Whose Aadhaar is ${fakeAadhaar()}?`);
    expect(await screen.findByText(/looks like a full Aadhaar number/)).toBeInTheDocument();
    expect(stub.callsTo(ASK)).toHaveLength(0);
  });

  it("an empty question says what to do", async () => {
    renderChat();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Ask" }));
    expect(await screen.findByText("Fill in this field.")).toBeInTheDocument();
    expect(stub.callsTo(ASK)).toHaveLength(0);
  });

  it("without kb.ask says so and never calls the API", async () => {
    setMe(["document.read"]);
    renderChat();
    expect(await screen.findByText("You can't ask questions here yet")).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Your question/)).toBeNull();
  });

  it("works in Telugu without missing messages", async () => {
    stub.routes[ASK] = () => sseResponse(answered);
    renderChat("te");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^మీ ప్రశ్న/), "పరీక్షలు ఎప్పుడు?");
    await user.click(screen.getByRole("button", { name: "అడగండి" }));
    expect(await screen.findByText("సమాధానం సిద్ధంగా ఉంది.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "ఆధారాలు" })).toBeInTheDocument();
  });
});

describe("feedback on an answer (US-801 AC4, FR-KB-009)", () => {
  it("helpful is sent at once", async () => {
    stub.routes[ASK] = () => sseResponse(answered);
    stub.routes[`POST /bff/api/v1/knowledge/queries/${QUERY}/feedback`] = () =>
      Response.json({
        query_id: QUERY,
        feedback: "helpful",
        reason: null,
        recorded_at: "2026-09-28T05:00:00Z",
      });
    renderChat();
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Yes, helpful" }));
    expect(await screen.findByText(/Thank you/)).toBeInTheDocument();
    expect(body(`POST /bff/api/v1/knowledge/queries/${QUERY}/feedback`)).toEqual({
      feedback: "helpful",
    });
  });

  it("not helpful sends a reason code, never free text", async () => {
    stub.routes[ASK] = () => sseResponse(answered);
    const key = `POST /bff/api/v1/knowledge/queries/${QUERY}/feedback`;
    stub.routes[key] = () =>
      Response.json({
        query_id: QUERY,
        feedback: "not_helpful",
        reason: "outdated",
        recorded_at: "2026-09-28T05:00:00Z",
      });
    renderChat();
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "No, not helpful" }));
    await user.click(screen.getByRole("radio", { name: "It is out of date" }));
    await user.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(await screen.findByText(/Thank you/)).toBeInTheDocument();
    expect(body(key)).toEqual({ feedback: "not_helpful", reason: "outdated" });
    expect(stub.callsTo(key)[0]?.headers.get("x-csrf-token")).toBe(CSRF);
  });

  it("offers exactly the reason codes the API accepts (FeedbackIn.reason)", async () => {
    stub.routes[ASK] = () => sseResponse(answered);
    renderChat();
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "No, not helpful" }));
    const values = screen
      .getAllByRole("radio")
      .map((radio) => (radio as HTMLInputElement).value)
      .sort();
    expect(values).toEqual(
      ["wrong_source", "outdated", "incomplete", "not_found_but_exists", "wrong_language"].sort(),
    );
  });

  it("explains a 404 for someone else's question", async () => {
    stub.routes[ASK] = () => sseResponse(answered);
    stub.routes[`POST /bff/api/v1/knowledge/queries/${QUERY}/feedback`] = () =>
      problem(404, "not_found");
    renderChat();
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Yes, helpful" }));
    expect(await screen.findByText("Question not found")).toBeInTheDocument();
  });
});

function verified(
  overrides: Partial<Schemas["VerifiedAnswerOut"]> = {},
): Schemas["VerifiedAnswerOut"] {
  return {
    id: VERIFIED,
    question: "When do exams begin?",
    language: "en",
    answer_text: "Exams begin on 22/09/2026 at 9 am.",
    citations: [{ source: DOC_SOURCE, cited_text: "Exams begin on 22/09/2026 at 9 am" }],
    status: "active",
    verified_by: ID.user,
    verified_by_name: null,
    verified_at: "2026-09-27T05:30:00Z",
    review_due: "2027-03-31",
    version: 1,
    created_at: "2026-09-27T05:30:00Z",
    ...overrides,
  };
}

describe("verified answers (US-802, FR-KB-030)", () => {
  it("saves an answer as verified, prefilled from the answer (document sources only)", async () => {
    setMe(["kb.ask", "document.read", "kb.verified_answer.manage"]);
    stub.routes[ASK] = () => sseResponse(answered);
    stub.routes["POST /bff/api/v1/knowledge/verified-answers"] = () =>
      Response.json(verified(), { status: 201 });
    renderChat();
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Save as verified answer" }));
    const dialog = await screen.findByRole("dialog", { name: "Save a verified answer" });
    expect(within(dialog).getByLabelText(/^Answer/)).toHaveValue(
      "Exams begin on 22/09/2026. Sita joined class 6 in 2024.",
    );
    expect(within(dialog).getByLabelText(/^Source 1/)).toHaveValue(DOC_SOURCE);
    expect(within(dialog).queryByLabelText(/^Source 2/)).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Save verified answer" }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/knowledge/verified-answers")).toHaveLength(1),
    );
    const [call] = stub.callsTo("POST /bff/api/v1/knowledge/verified-answers");
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(body("POST /bff/api/v1/knowledge/verified-answers")).toEqual({
      question: "When do exams begin?",
      language: "en",
      answer_text: "Exams begin on 22/09/2026. Sita joined class 6 in 2024.",
      review_due: null,
      citations: [{ source: DOC_SOURCE, cited_text: "Exams begin on 22/09/2026 at 9 am" }],
    });
  });

  it("shows a quote the page does not contain on its field (422 citation_text_not_found)", async () => {
    setMe(["kb.ask", "kb.verified_answer.manage"]);
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () => page([]);
    stub.routes["POST /bff/api/v1/knowledge/verified-answers"] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "citations[0].cited_text",
            code: "citation_text_not_found",
            message_key: "errors.citation_text_not_found",
          },
        ],
      });
    renderWithIntl(<VerifiedAnswersPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a verified answer" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Question/), "When do exams begin?");
    await user.type(within(dialog).getByLabelText(/^Answer/), "On 22/09/2026.");
    await user.type(within(dialog).getByLabelText(/^Source 1/), DOC_SOURCE);
    await user.type(within(dialog).getByLabelText(/^Quoted text for source 1/), "Not there");
    await user.click(within(dialog).getByRole("button", { name: "Save verified answer" }));
    expect(
      await within(dialog).findByText(
        "This text is not on that page. Copy the exact words from the document.",
      ),
    ).toBeInTheDocument();
  });

  it("with Telugu switched off, offers no answer language and saves English (ADR-0036)", async () => {
    setMe(["kb.ask", "kb.verified_answer.manage"]);
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () => page([]);
    stub.routes["POST /bff/api/v1/knowledge/verified-answers"] = () =>
      Response.json(verified(), { status: 201 });
    renderWithIntl(<VerifiedAnswersPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a verified answer" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByLabelText(messages.en.ask.verified.language)).toBeNull();
    expect(dialog.textContent ?? "").not.toMatch(/Telugu|[ఀ-౿]/);
    await user.type(within(dialog).getByLabelText(/^Question/), "When do exams begin?");
    await user.type(within(dialog).getByLabelText(/^Answer/), "On 22/09/2026.");
    await user.type(within(dialog).getByLabelText(/^Source 1/), DOC_SOURCE);
    await user.type(within(dialog).getByLabelText(/^Quoted text for source 1/), "Exams begin");
    await user.click(within(dialog).getByRole("button", { name: "Save verified answer" }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/knowledge/verified-answers")).toHaveLength(1),
    );
    expect(body("POST /bff/api/v1/knowledge/verified-answers")).toMatchObject({ language: "en" });
  });

  it("offers Telugu and mixed answer languages when Telugu is switched on (ADR-0036)", async () => {
    setMe(["kb.ask", "kb.verified_answer.manage"]);
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () => page([]);
    renderWithIntl(<VerifiedAnswersPage />, { telugu: true });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a verified answer" }));
    const dialog = await screen.findByRole("dialog");
    const select = within(dialog).getByLabelText(messages.en.ask.verified.language);
    expect([...(select as HTMLSelectElement).options].map((option) => option.value)).toEqual([
      "en",
      "te",
      "mixed",
    ]);
  });

  it("checks sources before sending: only sos://doc pages", async () => {
    setMe(["kb.ask", "kb.verified_answer.manage"]);
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () => page([]);
    renderWithIntl(<VerifiedAnswersPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a verified answer" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^Question/), "Q?");
    await user.type(within(dialog).getByLabelText(/^Answer/), "A.");
    await user.type(within(dialog).getByLabelText(/^Source 1/), "https://evil.example");
    await user.type(within(dialog).getByLabelText(/^Quoted text for source 1/), "x");
    await user.click(within(dialog).getByRole("button", { name: "Add another source" }));
    expect(within(dialog).getByLabelText(/^Source 2/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove source 2" }));
    await user.click(within(dialog).getByRole("button", { name: "Save verified answer" }));
    expect(await within(dialog).findByText("Check this value and try again.")).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/knowledge/verified-answers")).toHaveLength(0);
  });

  it("maps the API's citation field paths to the form rows", () => {
    expect(verifiedFieldMap("citations[2].cited_text")).toBe("cited_text_2");
    expect(verifiedFieldMap("citations.0.source")).toBe("source_0");
    expect(verifiedFieldMap("question")).toBe("question");
  });

  it("lists verified answers with who verified them, when, and review flags (US-802 AC1)", async () => {
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () =>
      page([
        verified(),
        verified({
          id: "0192f3a4-0000-7000-8000-00000000e102",
          question: "Uniform days?",
          status: "needs_review",
          verified_by: "0192f3a4-0000-7000-8000-00000000c599",
          review_due: null,
        }),
      ]);
    renderWithIntl(<VerifiedAnswersPage />);
    expect(await screen.findByRole("heading", { name: "When do exams begin?" })).toBeVisible();
    expect(
      screen.getByText(/Verified by you · on 27\/09\/2026 · check again by 31\/03\/2027/),
    ).toBeVisible();
    expect(screen.getByText(/Verified by a staff member/)).toBeVisible();
    expect(screen.getByText(/A source document changed or was deleted/)).toBeVisible();
    expect(
      screen.getAllByRole("link", { name: /Source 1 \(open the document\)/ })[0],
    ).toHaveAttribute("href", `/documents/${DOC}`);
    expect(screen.queryByRole("button", { name: "Add a verified answer" })).toBeNull();
  });

  it("shows the verifier's name when the API gives it (VerifiedAnswerOut.verified_by_name)", async () => {
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () =>
      page([
        verified({
          verified_by: "0192f3a4-0000-7000-8000-00000000c599",
          verified_by_name: "Lakshmi Devi",
        }),
      ]);
    renderWithIntl(<VerifiedAnswersPage />);
    expect(await screen.findByText(/Verified by Lakshmi Devi · on 27\/09\/2026/)).toBeVisible();
  });

  it("review confirms an answer again with If-Match; retire withdraws it (FR-KB-030)", async () => {
    setMe(["kb.ask", "kb.verified_answer.manage"]);
    const REVIEW = `POST /bff/api/v1/knowledge/verified-answers/${VERIFIED}/review`;
    const RETIRE = `POST /bff/api/v1/knowledge/verified-answers/${VERIFIED}/retire`;
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () =>
      page([verified({ status: "needs_review", version: 3 })]);
    stub.routes[REVIEW] = () => Response.json(verified({ status: "active", version: 4 }));
    stub.routes[RETIRE] = () => Response.json(verified({ status: "retired", version: 4 }));
    renderWithIntl(<VerifiedAnswersPage />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Check and confirm" }));
    let dialog = await screen.findByRole("dialog", { name: "Confirm this verified answer" });
    await user.click(within(dialog).getByRole("button", { name: "Confirm answer" }));
    await waitFor(() => expect(stub.callsTo(REVIEW)).toHaveLength(1));
    expect(stub.callsTo(REVIEW)[0]?.headers.get("if-match")).toBe('W/"3"');
    expect(stub.callsTo(REVIEW)[0]?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(body(REVIEW)).toEqual({});

    await user.click(screen.getByRole("button", { name: "Retire" }));
    dialog = await screen.findByRole("dialog", { name: "Retire this verified answer" });
    await user.click(within(dialog).getByRole("button", { name: "Retire answer" }));
    await waitFor(() => expect(stub.callsTo(RETIRE)).toHaveLength(1));
    expect(stub.callsTo(RETIRE)[0]?.headers.get("if-match")).toBe('W/"3"');
  });

  it("explains 409 verified_answer_retired", async () => {
    setMe(["kb.ask", "kb.verified_answer.manage"]);
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () => page([verified()]);
    stub.routes[`POST /bff/api/v1/knowledge/verified-answers/${VERIFIED}/retire`] = () =>
      problem(409, "verified_answer_retired");
    renderWithIntl(<VerifiedAnswersPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Retire" }));
    const dialog = await screen.findByRole("dialog", { name: "Retire this verified answer" });
    await user.click(within(dialog).getByRole("button", { name: "Retire answer" }));
    expect(await within(dialog).findByText("Already retired")).toBeInTheDocument();
  });

  it("review and retire are offered only to kb.verified_answer.manage, never on retired answers", async () => {
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () => page([verified()]);
    const { unmount } = renderWithIntl(<VerifiedAnswersPage />);
    expect(await screen.findByRole("heading", { name: "When do exams begin?" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retire" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Check and confirm" })).toBeNull();
    unmount();

    setMe(["kb.ask", "kb.verified_answer.manage"]);
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () =>
      page([verified({ status: "retired" })]);
    renderWithIntl(<VerifiedAnswersPage />);
    expect(await screen.findByRole("heading", { name: "When do exams begin?" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retire" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Check and confirm" })).toBeNull();
  });

  it("the verified-answer actions work in Telugu without missing messages", async () => {
    setMe(["kb.ask", "kb.verified_answer.manage"]);
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () =>
      page([
        verified({
          status: "needs_review",
          verified_by: "0192f3a4-0000-7000-8000-00000000c599",
          verified_by_name: "Lakshmi Devi",
        }),
      ]);
    renderWithIntl(<VerifiedAnswersPage />, "te");
    expect(await screen.findByText(/Lakshmi Devi/)).toBeVisible();
    expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(3);
  });

  it("finds answers on the loaded page by their words, without a request", async () => {
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () =>
      page([
        verified(),
        verified({ id: "0192f3a4-0000-7000-8000-00000000e102", question: "Uniform days?" }),
      ]);
    renderWithIntl(<VerifiedAnswersPage />);
    expect(await screen.findByRole("heading", { name: "Uniform days?" })).toBeVisible();
    const before = stub.callsTo("GET /bff/api/v1/knowledge/verified-answers").length;
    await userEvent.setup().type(screen.getByLabelText("Find on this page"), "uniform");
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "When do exams begin?" })).toBeNull(),
    );
    expect(screen.getByRole("heading", { name: "Uniform days?" })).toBeVisible();
    expect(screen.getByText("1 of 2 answers on this page match.")).toBeVisible();
    expect(stub.callsTo("GET /bff/api/v1/knowledge/verified-answers")).toHaveLength(before);
  });

  it("filters by status", async () => {
    stub.routes["GET /bff/api/v1/knowledge/verified-answers"] = () => page([]);
    renderWithIntl(<VerifiedAnswersPage />);
    expect(await screen.findByText("No verified answers yet")).toBeVisible();
    await userEvent.setup().selectOptions(screen.getByLabelText("Show"), "needs_review");
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/knowledge/verified-answers")
          .some((call) => call.url.searchParams.get("status") === "needs_review"),
      ).toBe(true),
    );
  });
});

describe("search documents (FR-KB-001, FR-KB-002, SEC-008)", () => {
  it("posts the search text in the body and lists passages with their documents", async () => {
    stub.routes["POST /bff/api/v1/knowledge/search"] = () =>
      Response.json({
        data: [
          {
            source: DOC_SOURCE,
            document_id: DOC,
            version_no: 2,
            page_from: 1,
            page_to: 1,
            doc_type: "circular",
            title: "Circular · Exam timings",
            issued_on: "2026-09-15",
            snippet: "Exams begin on 22/09/2026",
            score: 0.8,
          },
        ],
      });
    renderWithIntl(<AskSearchPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^Search for/), "exam timings");
    await user.selectOptions(screen.getByLabelText("Document type"), "circular");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("1 passage found.")).toBeInTheDocument();
    const [call] = stub.callsTo("POST /bff/api/v1/knowledge/search");
    expect(call?.url.search).toBe("");
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      query: "exam timings",
      limit: 20,
      doc_types: ["circular"],
    });
    expect(
      screen.getByRole("link", { name: /Circular · Exam timings \(open the document\)/ }),
    ).toHaveAttribute("href", `/documents/${DOC}`);
    expect(screen.getByText("Circular · issued 15/09/2026")).toBeInTheDocument();
  });

  it("says when nothing matched", async () => {
    stub.routes["POST /bff/api/v1/knowledge/search"] = () => Response.json({ data: [] });
    renderWithIntl(<AskSearchPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^Search for/), "nothing");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Nothing found")).toBeInTheDocument();
  });

  it("without document.read says so", async () => {
    setMe(["kb.ask"]);
    renderWithIntl(<AskSearchPage />);
    expect(await screen.findByText("You can't search documents")).toBeInTheDocument();
  });
});

describe("navigation (UX only; the API checks every call)", () => {
  it("shows Ask the school to kb.ask holders only", () => {
    const { unmount } = renderWithIntl(
      <SchoolShell permissions={["kb.ask"]}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(
      within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", {
        name: "Ask the school",
      }),
    ).toHaveAttribute("href", "/ask");
    unmount();
    renderWithIntl(
      <SchoolShell permissions={["document.read"]}>
        <p>x</p>
      </SchoolShell>,
    );
    expect(
      within(screen.getByRole("navigation", { name: "Main" })).queryByRole("link", {
        name: "Ask the school",
      }),
    ).toBeNull();
  });

  it("tabs link Ask, Search documents and Verified answers", async () => {
    renderChat();
    const tabs = await screen.findByRole("navigation", { name: "Ask the school sections" });
    expect(within(tabs).getByRole("link", { name: "Ask" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Search documents" })).toHaveAttribute(
      "href",
      "/ask/search",
    );
    expect(within(tabs).getByRole("link", { name: "Verified answers" })).toHaveAttribute(
      "href",
      "/ask/verified",
    );
  });
});

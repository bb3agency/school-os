import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { fakeAadhaar, me } from "@/test/records-fixtures";
import { intlErrors, renderWithIntl } from "@/test/render";
import AskHistoryPage from "@/app/[locale]/(school)/ask/history/page";
import AskMemoryPage from "@/app/[locale]/(school)/ask/memory/page";
import { CHAT, LIST_ROUTE, summary } from "./chat-test-utils";

const nav = vi.hoisted(() => ({ path: "/en/ask/history" }));

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => nav.path,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const A = CHAT.conversation;
const B = CHAT.conversation2;
const C = "0192f3a4-0000-7000-8000-00000000e903";
const conv = (id: string) => `/bff/api/v1/knowledge/conversations/${id}`;
const MEM = "/bff/api/v1/knowledge/memories";
const SETTINGS = "/bff/api/v1/knowledge/memory-settings";

let stub: BffStub;

beforeEach(() => {
  nav.path = "/en/ask/history";
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["kb.ask"]));
});

afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function listOf(...items: Array<Record<string, unknown>>) {
  return Response.json({ data: items, next_cursor: null });
}

describe("All chats (FR-KB-012)", () => {
  beforeEach(() => {
    stub.routes[`GET ${LIST_ROUTE.slice(4)}`] = () =>
      listOf(
        summary({ id: A, title: "Exam dates", updated_at: "2026-09-29T05:00:00Z" }),
        summary({
          id: B,
          title: "Transfer certificate steps",
          pinned: true,
          updated_at: "2026-09-01T05:00:00Z",
        }),
        // No title yet: the API sends "" (ConversationOut.title is a string).
        summary({ id: C, title: "", message_count: 3, updated_at: "2026-09-28T05:00:00Z" }),
      );
  });

  it("lists pinned chats first and filters loaded titles as you type (no request)", async () => {
    renderWithIntl(<AskHistoryPage />);
    const pinned = await screen.findByRole("region", { name: "Pinned" });
    expect(
      within(pinned).getByRole("link", { name: /Transfer certificate steps/ }),
    ).toHaveAttribute("href", `/en/ask/c/${B}`);
    const others = screen.getByRole("region", { name: "Chats" });
    expect(
      within(others)
        .getAllByRole("link")
        .map((l) => l.textContent),
    ).toEqual(["Exam dates", "New chat"]);
    expect(screen.getByText(/3 questions/)).toBeInTheDocument();
    const before = stub.callsTo(LIST_ROUTE).length;
    await userEvent.setup().type(screen.getByLabelText("Find a chat"), "exam");
    expect(screen.getByText("1 of 3 loaded chats match.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Transfer certificate/ })).toBeNull();
    expect(stub.callsTo(LIST_ROUTE)).toHaveLength(before);
  });

  it("pin is shown at once and rolled back when the API refuses", async () => {
    stub.routes[`PATCH ${conv(A)}`] = () => problem(500, "internal");
    renderWithIntl(<AskHistoryPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Pin Exam dates" }));
    // Optimistic: it moved to Pinned before the API answered.
    await waitFor(() => expect(stub.callsTo(`PATCH ${conv(A)}`)).toHaveLength(1));
    expect(stub.callsTo(`PATCH ${conv(A)}`)[0]?.headers.get("if-match")).toBe('W/"1"');
    expect(await screen.findByText(/could not be saved, so it was undone/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Pin Exam dates" })).toBeInTheDocument();
  });

  it("renames in a dialog (If-Match) and deletes after confirming", async () => {
    // The API's state: the list refetched after each change shows it.
    let title = "Exam dates";
    let deleted = false;
    stub.routes[`GET ${LIST_ROUTE.slice(4)}`] = () =>
      deleted ? listOf() : listOf(summary({ id: A, title }));
    stub.routes[`PATCH ${conv(A)}`] = () => {
      title = "Half-yearly exams";
      return Response.json(summary({ id: A, title, version: 2 }));
    };
    stub.routes[`DELETE ${conv(A)}`] = () => {
      deleted = true;
      return new Response(null, { status: 204 });
    };
    renderWithIntl(<AskHistoryPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Rename Exam dates" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename" });
    const field = within(dialog).getByLabelText("Chat title");
    expect(field).toHaveValue("Exam dates");
    await user.clear(field);
    await user.type(field, "Half-yearly exams");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("link", { name: "Half-yearly exams" })).toBeInTheDocument();
    expect(JSON.parse(stub.callsTo(`PATCH ${conv(A)}`)[0]?.body ?? "{}")).toEqual({
      title: "Half-yearly exams",
    });

    await user.click(screen.getByRole("button", { name: "Delete Half-yearly exams" }));
    const confirm = await screen.findByRole("dialog", { name: "Delete this chat?" });
    expect(confirm).toHaveTextContent("“Half-yearly exams” and its answers will be removed");
    await user.click(within(confirm).getByRole("button", { name: "Delete chat" }));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Half-yearly exams" })).toBeNull(),
    );
    expect(stub.callsTo(`DELETE ${conv(A)}`)).toHaveLength(1);
  });

  it("a refused title (422 title_personal_number) is explained and the old title comes back", async () => {
    stub.routes[`PATCH ${conv(A)}`] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "title",
            code: "title_personal_number",
            message_key: "errors.title_personal_number",
          },
        ],
      });
    renderWithIntl(<AskHistoryPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Rename Exam dates" }));
    const field = within(await screen.findByRole("dialog", { name: "Rename" })).getByLabelText(
      "Chat title",
    );
    await user.clear(field);
    await user.type(field, "Roll 1234 5678");
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Remove the number from the title")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Exam dates" })).toBeInTheDocument();
  });

  it("a full Aadhaar number in a title never leaves the browser (invariant 4)", async () => {
    renderWithIntl(<AskHistoryPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Rename Exam dates" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename" });
    const field = within(dialog).getByLabelText("Chat title");
    await user.clear(field);
    await user.type(field, `Student ${fakeAadhaar()}`);
    await user.keyboard("{Enter}");
    expect(await within(dialog).findByText(/looks like a full Aadhaar number/)).toBeVisible();
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(stub.callsTo(`PATCH ${conv(A)}`)).toHaveLength(0);
  });

  it("a chat changed in another window (412) says so", async () => {
    stub.routes[`PATCH ${conv(A)}`] = () => problem(412, "precondition_failed");
    renderWithIntl(<AskHistoryPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Pin Exam dates" }));
    expect(await screen.findByText("Changed somewhere else")).toBeInTheDocument();
  });

  it("a refused delete puts the chat back", async () => {
    stub.routes[`DELETE ${conv(A)}`] = () => problem(503, "unavailable");
    renderWithIntl(<AskHistoryPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Exam dates" }));
    const confirm = await screen.findByRole("dialog", { name: "Delete this chat?" });
    await user.click(within(confirm).getByRole("button", { name: "Delete chat" }));
    expect(await screen.findByText(/could not be saved, so it was undone/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Exam dates" })).toBeInTheDocument();
  });

  it("loads the next page with the cursor", async () => {
    stub.routes[`GET ${LIST_ROUTE.slice(4)}`] = (_request, url) =>
      url.searchParams.get("cursor") === "next"
        ? listOf(summary({ id: C, title: "Older chat", updated_at: "2026-08-01T00:00:00Z" }))
        : Response.json({ data: [summary({ id: A })], next_cursor: "next" });
    renderWithIntl(<AskHistoryPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Load more chats" }));
    expect(await screen.findByRole("link", { name: "Older chat" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more chats" })).toBeNull();
  });

  it("says what to do when there are no chats, in Telugu too", async () => {
    stub.routes[`GET ${LIST_ROUTE.slice(4)}`] = () => listOf();
    const { unmount } = renderWithIntl(<AskHistoryPage />);
    expect(await screen.findByText("No chats yet")).toBeInTheDocument();
    unmount();
    renderWithIntl(<AskHistoryPage />, "te");
    expect(await screen.findByText("ఇంకా చాట్‌లు లేవు")).toBeInTheDocument();
  });
});

describe("recent chats in the one sidebar (FR-KB-012, docs/17 §5.2)", () => {
  const shell = () =>
    renderWithIntl(
      <SchoolShell permissions={["kb.ask"]}>
        <p>x</p>
      </SchoolShell>,
    );

  beforeEach(() => {
    stub.routes[`GET ${LIST_ROUTE.slice(4)}`] = () =>
      listOf(
        summary({ id: A, title: "Exam dates" }),
        summary({
          id: B,
          title: "A very long chat title about the transfer certificate rules",
          pinned: true,
        }),
      );
  });

  it("shows New chat, the recents (pinned first), All chats and Memory; the open chat is the one current page", async () => {
    nav.path = `/en/ask/c/${A}`;
    shell();
    const menu = screen.getByRole("navigation", { name: "Main" });
    const recents = await within(menu).findByRole("list", { name: "Recent" });
    const links = within(recents).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([`/en/ask/c/${B}`, `/en/ask/c/${A}`]);
    // Truncated visually; the full title stays the name and the tooltip.
    expect(links[0]).toHaveAttribute(
      "title",
      "A very long chat title about the transfer certificate rules",
    );
    expect(links[0]).toHaveAccessibleName(/^Pinned:\s?A very long chat title/);
    expect(links[1]).toHaveAttribute("aria-current", "page");
    expect(within(menu).getAllByRole("link", { current: "page" })).toHaveLength(1);
    expect(within(menu).getByRole("link", { name: "Ask the school" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(within(menu).getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/en/ask");
    expect(within(menu).getByRole("link", { name: "All chats" })).toHaveAttribute(
      "href",
      "/en/ask/history",
    );
    expect(within(menu).getByRole("link", { name: "Memory" })).toHaveAttribute(
      "href",
      "/en/ask/memory",
    );
  });

  it("stays out of the way on other pages (no request, no sub-list)", async () => {
    nav.path = "/en/students";
    shell();
    await Promise.resolve();
    expect(screen.queryByRole("link", { name: "New chat" })).toBeNull();
    expect(stub.callsTo(LIST_ROUTE)).toHaveLength(0);
  });

  it("marks All chats current on the history page and works in Telugu", async () => {
    nav.path = "/te/ask/history";
    renderWithIntl(
      <SchoolShell permissions={["kb.ask"]}>
        <p>x</p>
      </SchoolShell>,
      "te",
    );
    const menu = screen.getAllByRole("navigation")[0] as HTMLElement;
    expect(await within(menu).findByRole("link", { name: "అన్ని చాట్‌లు" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(menu).getAllByRole("link", { current: "page" })).toHaveLength(1);
  });
});

describe("Manage memory", () => {
  // MemoryOut: `active` (in use) or `pending` (suggested in a chat, expires unless saved).
  const item = (id: string, text: string, extra: Record<string, unknown> = {}) => ({
    id,
    text,
    source: "explicit",
    status: "active",
    created_at: "2026-09-28T05:00:00Z",
    updated_at: "2026-09-28T05:00:00Z",
    expires_at: null,
    version: 1,
    ...extra,
  });
  const suggested = (id: string, text: string) =>
    item(id, text, {
      source: "suggested",
      status: "pending",
      expires_at: "2026-09-29T05:00:00Z",
    });
  // GET /knowledge/memories is Page[MemoryOut] (one page).
  const pageOf = (...items: Array<Record<string, unknown>>) =>
    Response.json({ data: items, next_cursor: null });
  // A refused text: 422 validation_error with the reason as the field error's code.
  const refused = (code: string) =>
    problem(422, "validation_error", {
      errors: [{ field: "text", code, message_key: `errors.${code}` }],
    });

  beforeEach(() => {
    nav.path = "/en/ask/memory";
    stub.routes[`GET ${SETTINGS}`] = () => Response.json({ enabled: true, school_enabled: true });
    stub.routes[`GET ${MEM}`] = () =>
      pageOf(
        item(CHAT.memory, "I teach Class 7 mathematics"),
        suggested(CHAT.memory2, "Prefers answers in Telugu"),
      );
  });

  it("explains memory, lists items and switches memory off (PUT)", async () => {
    stub.routes[`PUT ${SETTINGS}`] = () => Response.json({ enabled: false, school_enabled: true });
    renderWithIntl(<AskMemoryPage />);
    expect(await screen.findByText(/not for facts about students or staff/)).toBeInTheDocument();
    expect(await screen.findByText("I teach Class 7 mathematics")).toBeInTheDocument();
    expect(screen.getByText("Suggested in a chat, not saved yet")).toBeInTheDocument();
    const toggle = screen.getByRole("switch", { name: "Use memory in Ask" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await userEvent.setup().click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(JSON.parse(stub.callsTo(`PUT ${SETTINGS}`)[0]?.body ?? "{}")).toEqual({
      enabled: false,
    });
  });

  it("is off and disabled, with the reason, when the school has switched memory off", async () => {
    stub.routes[`GET ${SETTINGS}`] = () => Response.json({ enabled: true, school_enabled: false });
    renderWithIntl(<AskMemoryPage />);
    const toggle = await screen.findByRole("switch", { name: "Use memory in Ask" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Your school has switched memory off/)).toBeInTheDocument();
    expect(screen.getByLabelText("Add something to remember")).toBeDisabled();
  });

  it("edits (If-Match), saves a suggestion and deletes, each at once", async () => {
    let text = "I teach Class 7 mathematics";
    stub.routes[`GET ${MEM}`] = () =>
      pageOf(item(CHAT.memory, text), suggested(CHAT.memory2, "Prefers answers in Telugu"));
    stub.routes[`PATCH ${MEM}/${CHAT.memory}`] = () => {
      text = "I teach Class 8 mathematics";
      return Response.json(item(CHAT.memory, text, { version: 2 }));
    };
    stub.routes[`POST ${MEM}/${CHAT.memory2}/confirm`] = () =>
      Response.json(item(CHAT.memory2, "Prefers answers in Telugu", { version: 2 }));
    stub.routes[`DELETE ${MEM}/${CHAT.memory}`] = () => new Response(null, { status: 204 });
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Edit: I teach Class 7 mathematics" }),
    );
    const field = screen.getByLabelText("Memory");
    await user.clear(field);
    await user.type(field, "I teach Class 8 mathematics");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("I teach Class 8 mathematics")).toBeInTheDocument();
    expect(stub.callsTo(`PATCH ${MEM}/${CHAT.memory}`)[0]?.headers.get("if-match")).toBe('W/"1"');

    await user.click(screen.getByRole("button", { name: "Save: Prefers answers in Telugu" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST ${MEM}/${CHAT.memory2}/confirm`)).toHaveLength(1),
    );

    await user.click(screen.getByRole("button", { name: "Delete: I teach Class 8 mathematics" }));
    await waitFor(() => expect(stub.callsTo(`DELETE ${MEM}/${CHAT.memory}`)).toHaveLength(1));
  });

  it("forget everything confirms first and rolls back if the API refuses", async () => {
    stub.routes[`DELETE ${MEM}`] = () => problem(500, "internal");
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Forget everything" }));
    const dialog = await screen.findByRole("dialog", { name: "Forget everything?" });
    expect(dialog).toHaveTextContent("forget all 2 items");
    await user.click(within(dialog).getByRole("button", { name: "Forget everything" }));
    expect(await screen.findByText(/could not be saved, so it was undone/)).toBeInTheDocument();
    expect(await screen.findByText("I teach Class 7 mathematics")).toBeInTheDocument();
  });

  it("explains a refused memory (422 memory_others) and keeps the text", async () => {
    stub.routes[`POST ${MEM}`] = () => refused("memory_others");
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    const field = await screen.findByLabelText("Add something to remember");
    await user.type(field, "Ravi in Class 6 has asthma");
    await user.click(screen.getByRole("button", { name: "Remember" }));
    expect(await screen.findByText("This can't be remembered")).toBeInTheDocument();
    expect(
      screen.getByText(/not facts about students, parents or staff/, { selector: "p" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(field).toHaveValue("Ravi in Class 6 has asthma"));
  });

  it.each([
    ["memory_personal_number", /looks like an Aadhaar or other personal number/],
    ["memory_date", /It has a date/],
    ["memory_long_number", /It has a long number/],
    ["memory_too_long", /at most 200 characters/],
    ["memory_empty", /Type what Ask should remember/],
    ["memory_seen_record", /names someone from the school's records/],
    ["memory_unsure", /could not tell whether this is only about you/],
  ])("says why %s was refused and what to write instead", async (code, body) => {
    stub.routes[`POST ${MEM}`] = () => refused(code);
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Add something to remember"), "Something");
    await user.click(screen.getByRole("button", { name: "Remember" }));
    expect(await screen.findByText(body)).toBeInTheDocument();
  });

  it("keeps the text when the memory check is not available (503 memory_check_unavailable)", async () => {
    stub.routes[`POST ${MEM}`] = () => problem(503, "memory_check_unavailable");
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    const field = await screen.findByLabelText("Add something to remember");
    await user.type(field, "I prefer short answers");
    await user.click(screen.getByRole("button", { name: "Remember" }));
    expect(await screen.findByText("Memory check not available")).toBeInTheDocument();
    expect(screen.getByText(/nothing was saved\. Try again in a few minutes/)).toBeInTheDocument();
    await waitFor(() => expect(field).toHaveValue("I prefer short answers"));
    // The temporary row is gone again.
    expect(screen.queryByText("I prefer short answers", { selector: "li p" })).toBeNull();
  });

  it("explains a full memory (409 memory_full) and stops adding at 30 items", async () => {
    stub.routes[`POST ${MEM}`] = () => problem(409, "memory_full");
    const { unmount } = renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Add something to remember"), "One more");
    await user.click(screen.getByRole("button", { name: "Remember" }));
    expect(await screen.findByText("Memory is full")).toBeInTheDocument();
    expect(screen.getByText(/up to 30 items\. Delete ones you no longer need/)).toBeInTheDocument();
    unmount();

    stub.routes[`GET ${MEM}`] = () =>
      pageOf(
        ...Array.from({ length: 30 }, (_, i) =>
          item(
            `0192f3a4-0000-7000-8000-${(0xe700 + i).toString(16).padStart(12, "0")}`,
            `Item ${i}`,
          ),
        ),
      );
    renderWithIntl(<AskMemoryPage />);
    expect(await screen.findByText("30 items")).toBeInTheDocument();
    expect(screen.getByLabelText("Add something to remember")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remember" })).toBeDisabled();
    expect(screen.getByText(/Memory is full \(30 items\)/)).toBeInTheDocument();
  });

  it("while memory is off for the member: add, edit and save are off, delete still works; 409 memory_off rereads the switch", async () => {
    stub.routes[`GET ${SETTINGS}`] = () => Response.json({ enabled: false, school_enabled: true });
    stub.routes[`DELETE ${MEM}/${CHAT.memory2}`] = () => new Response(null, { status: 204 });
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    expect(await screen.findByText(/Memory is off for you/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Use memory in Ask" })).not.toBeDisabled();
    expect(screen.getByLabelText("Add something to remember")).toBeDisabled();
    expect(
      await screen.findByRole("button", { name: "Edit: I teach Class 7 mathematics" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save: Prefers answers in Telugu" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Dismiss: Prefers answers in Telugu" }));
    await waitFor(() => expect(stub.callsTo(`DELETE ${MEM}/${CHAT.memory2}`)).toHaveLength(1));
  });

  it("a memory_off refusal explains how to switch memory on and reads the switch again", async () => {
    let school = true;
    stub.routes[`GET ${SETTINGS}`] = () => Response.json({ enabled: true, school_enabled: school });
    stub.routes[`POST ${MEM}/${CHAT.memory2}/confirm`] = () => {
      school = false; // switched off by the principal meanwhile
      return problem(409, "memory_off");
    };
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Save: Prefers answers in Telugu" }),
    );
    expect(await screen.findByText("Memory is off")).toBeInTheDocument();
    expect(await screen.findByText(/Your school has switched memory off/)).toBeInTheDocument();
  });

  it("refuses an Aadhaar number before sending (invariant 4)", async () => {
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    await user.type(
      await screen.findByLabelText("Add something to remember"),
      `My id ${fakeAadhaar()}`,
    );
    await user.click(screen.getByRole("button", { name: "Remember" }));
    expect(await screen.findByText(/looks like a full Aadhaar number/)).toBeInTheDocument();
    expect(stub.callsTo(`POST ${MEM}`)).toHaveLength(0);
  });

  it("shows the empty state, and works in Telugu", async () => {
    stub.routes[`GET ${MEM}`] = () => pageOf();
    const { unmount } = renderWithIntl(<AskMemoryPage />);
    expect(await screen.findByText("Nothing remembered yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forget everything" })).toBeNull();
    unmount();
    renderWithIntl(<AskMemoryPage />, "te");
    expect(await screen.findByText("ఇంకా ఏమీ గుర్తుంచుకోలేదు")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Ask లో జ్ఞాపకం వాడండి" })).toBeInTheDocument();
  });
});

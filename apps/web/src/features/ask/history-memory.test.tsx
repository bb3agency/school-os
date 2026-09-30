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
        summary({ id: C, title: null, message_count: 3, updated_at: "2026-09-28T05:00:00Z" }),
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
  const item = (id: string, text: string, extra: Record<string, unknown> = {}) => ({
    id,
    text,
    source: "explicit",
    status: "saved",
    created_at: "2026-09-28T05:00:00Z",
    updated_at: "2026-09-28T05:00:00Z",
    version: 1,
    ...extra,
  });

  beforeEach(() => {
    nav.path = "/en/ask/memory";
    stub.routes[`GET ${SETTINGS}`] = () => Response.json({ enabled: true, school_enabled: true });
    stub.routes[`GET ${MEM}`] = () =>
      Response.json([
        item(CHAT.memory, "I teach Class 7 mathematics"),
        item(CHAT.memory2, "Prefers answers in Telugu", { source: "suggested", status: "pending" }),
      ]);
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
      Response.json([
        item(CHAT.memory, text),
        item(CHAT.memory2, "Prefers answers in Telugu", { source: "suggested", status: "pending" }),
      ]);
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

  it("explains a refused memory (422 memory_not_allowed) and keeps the text", async () => {
    stub.routes[`POST ${MEM}`] = () => problem(422, "memory_not_allowed");
    renderWithIntl(<AskMemoryPage />);
    const user = userEvent.setup();
    const field = await screen.findByLabelText("Add something to remember");
    await user.type(field, "Ravi in Class 6 has asthma");
    await user.click(screen.getByRole("button", { name: "Remember" }));
    expect(await screen.findByText("This can't be remembered")).toBeInTheDocument();
    expect(
      screen.getByText(/not facts about students or staff/, { selector: "p" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(field).toHaveValue("Ravi in Class 6 has asthma"));
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
    stub.routes[`GET ${MEM}`] = () => Response.json([]);
    const { unmount } = renderWithIntl(<AskMemoryPage />);
    expect(await screen.findByText("Nothing remembered yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forget everything" })).toBeNull();
    unmount();
    renderWithIntl(<AskMemoryPage />, "te");
    expect(await screen.findByText("ఇంకా ఏమీ గుర్తుంచుకోలేదు")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Ask లో జ్ఞాపకం వాడండి" })).toBeInTheDocument();
  });
});

/* eslint-disable no-restricted-properties -- these tests prove the palette writes nothing to
   browser storage, so they clear and read it. */
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import {
  CommandPalette,
  SEARCH_DEBOUNCE_MS,
  clearPaletteRecents,
  paletteRecents,
  type CommandItem,
  type PaletteSearch,
} from "./CommandPalette";

/*
 * D72 (docs/17 §5.9, invariant 3): student search in the Ctrl+K palette. Synthetic names only.
 */

const router = { push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() };
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => "/", useRouter: () => router };
});

const pm = messages.en.ui.palette;

const ITEMS: CommandItem[] = [
  { id: "go:/", label: "Home", group: "Go to", href: "/" },
  { id: "go:/imports", label: "Imports", group: "Go to", href: "/imports" },
  { id: "do:add", label: "Add a student", group: "Actions", href: "/students/new" },
];
const GROUPS = ["Go to", "Actions"];

function row(n: number): CommandItem {
  return {
    id: `student:0190a0b0-0000-7000-8000-00000000000${n % 10}${n}`,
    label: `Sample pupil ${n}`,
    group: "ignored",
    href: `/students/s-${n}`,
    hint: `9A · ADM/${n}`,
  };
}

function fakeSearch(run: PaletteSearch["run"]): PaletteSearch {
  return {
    group: pm.groupStudents,
    run,
    messages: {
      loading: pm.studentsLoading,
      empty: pm.studentsEmpty,
      error: pm.studentsError,
      offline: pm.studentsOffline,
    },
  };
}

function palette(search?: PaletteSearch, onClose = vi.fn()) {
  return (
    <CommandPalette
      open
      onClose={onClose}
      items={ITEMS}
      groups={GROUPS}
      search={search}
      searchPlaceholder={pm.placeholderStudents}
    />
  );
}

function status(): HTMLElement {
  return screen.getByRole("status");
}

beforeEach(() => {
  router.push.mockReset();
  clearPaletteRecents();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("D72 palette student search: when it asks the server", () => {
  it("waits for 2 characters and a pause, then searches once with the trimmed text", async () => {
    const user = userEvent.setup();
    const run = vi.fn<PaletteSearch["run"]>(async () => [row(1)]);
    renderWithIntl(palette(fakeSearch(run)));
    const box = screen.getByRole("combobox");
    expect(box).toHaveAttribute("placeholder", pm.placeholderStudents);
    await user.type(box, "s");
    await act(() => new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_MS + 100)));
    expect(run).not.toHaveBeenCalled();
    await user.type(box, "am ");
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(run.mock.calls[0]?.[0]).toBe("sam");
    const group = await screen.findByRole("group", { name: pm.groupStudents });
    expect(within(group).getByRole("option")).toHaveTextContent("Sample pupil 1");
    expect(within(group).getByRole("option")).toHaveTextContent("9A · ADM/1");
  });

  it("aborts the previous request on new input and never shows its rows", async () => {
    const user = userEvent.setup();
    const signals: AbortSignal[] = [];
    let release: (rows: CommandItem[]) => void = () => undefined;
    const run = vi.fn<PaletteSearch["run"]>((query, signal) => {
      signals.push(signal);
      if (query === "sa") {
        return new Promise<CommandItem[]>((resolve) => {
          release = resolve;
        });
      }
      return Promise.resolve([row(2)]);
    });
    renderWithIntl(palette(fakeSearch(run)));
    const box = screen.getByRole("combobox");
    await user.type(box, "sa");
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    await user.type(box, "m");
    expect(signals[0]?.aborted).toBe(true);
    await waitFor(() => expect(run).toHaveBeenCalledTimes(2));
    act(() => release([row(9)]));
    const group = await screen.findByRole("group", { name: pm.groupStudents });
    expect(group).toHaveTextContent("Sample pupil 2");
    expect(screen.queryByText("Sample pupil 9")).toBeNull();
  });

  it("shows at most 8 students", async () => {
    const user = userEvent.setup();
    const run = vi.fn<PaletteSearch["run"]>(async () =>
      Array.from({ length: 12 }, (_, n) => row(n)),
    );
    renderWithIntl(palette(fakeSearch(run)));
    await user.type(screen.getByRole("combobox"), "pupil");
    const group = await screen.findByRole("group", { name: pm.groupStudents });
    expect(within(group).getAllByRole("option")).toHaveLength(8);
  });
});

describe("D72 palette student search: states, announced politely", () => {
  it("loading, then no students", async () => {
    const user = userEvent.setup();
    let release: (rows: CommandItem[]) => void = () => undefined;
    const run = vi.fn<PaletteSearch["run"]>(
      () =>
        new Promise<CommandItem[]>((resolve) => {
          release = resolve;
        }),
    );
    renderWithIntl(palette(fakeSearch(run)));
    expect(status()).toHaveAttribute("aria-live", "polite");
    await user.type(screen.getByRole("combobox"), "zq");
    expect(await screen.findByText(pm.studentsLoading)).toBeInTheDocument();
    await waitFor(() => expect(status()).toHaveTextContent(pm.studentsLoading));
    await waitFor(() => expect(run).toHaveBeenCalled());
    act(() => release([]));
    await waitFor(() => expect(status()).toHaveTextContent("Nothing matches. Try another word."));
    expect(screen.getByText("Nothing matches. Try another word.")).toBeInTheDocument();
    expect(screen.queryByText(pm.studentsLoading)).toBeNull();
  });

  it("no students while pages still match: says so under the pages", async () => {
    const user = userEvent.setup();
    const run = vi.fn<PaletteSearch["run"]>(async () => []);
    renderWithIntl(palette(fakeSearch(run)));
    await user.type(screen.getByRole("combobox"), "imp");
    expect(await screen.findByText(pm.studentsEmpty)).toBeInTheDocument();
    await waitFor(() => expect(status()).toHaveTextContent(pm.studentsEmpty));
    expect(screen.getByRole("option", { name: /Imports/ })).toBeInTheDocument();
  });

  it("error: says it could not search; the pages still work", async () => {
    const user = userEvent.setup();
    const run = vi.fn<PaletteSearch["run"]>(async () => {
      throw new Error("500");
    });
    renderWithIntl(palette(fakeSearch(run)));
    await user.type(screen.getByRole("combobox"), "home");
    expect(await screen.findByText(pm.studentsError)).toBeInTheDocument();
    await waitFor(() => expect(status()).toHaveTextContent(pm.studentsError));
    expect(screen.getByRole("option", { name: /Home/ })).toBeInTheDocument();
  });

  it("offline: does not call the server and says why", async () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const user = userEvent.setup();
    const run = vi.fn<PaletteSearch["run"]>(async () => [row(1)]);
    renderWithIntl(palette(fakeSearch(run)));
    await user.type(screen.getByRole("combobox"), "sample");
    expect(await screen.findByText(pm.studentsOffline)).toBeInTheDocument();
    await waitFor(() => expect(status()).toHaveTextContent(pm.studentsOffline));
    expect(run).not.toHaveBeenCalled();
  });
});

describe("D72 palette student search: keyboard only", () => {
  it("arrows move from the pages into the students; Enter opens the student page", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const run = vi.fn<PaletteSearch["run"]>(async () => [row(1), row(2)]);
    renderWithIntl(palette(fakeSearch(run), onClose));
    const box = screen.getByRole("combobox");
    // "s" matches the action "Add a student" too: Actions first, then Students.
    await user.type(box, "stud");
    const group = await screen.findByRole("group", { name: pm.groupStudents });
    const options = screen.getAllByRole("option");
    expect(options[0]).toHaveTextContent("Add a student");
    expect(box).toHaveAttribute("aria-activedescendant", options[0]?.id);
    await user.keyboard("{ArrowDown}{ArrowDown}");
    const second = within(group).getAllByRole("option")[1];
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", second?.id);
    await user.keyboard("{Enter}");
    expect(router.push).toHaveBeenCalledWith("/students/s-2");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("D72 recents: pages and actions only, in memory for the tab", () => {
  it("remembers a page, never a student; nothing goes to browser storage", async () => {
    const user = userEvent.setup();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const run = vi.fn<PaletteSearch["run"]>(async () => [row(3)]);
    const first = renderWithIntl(palette(fakeSearch(run)));
    await user.type(screen.getByRole("combobox"), "imp");
    await user.keyboard("{Enter}");
    expect(router.push).toHaveBeenCalledWith("/imports");
    first.unmount();

    const second = renderWithIntl(palette(fakeSearch(run)));
    const recent = screen.getByRole("group", { name: pm.groupRecent });
    expect(within(recent).getByRole("option")).toHaveTextContent("Imports");
    await user.type(screen.getByRole("combobox"), "pupil");
    const students = await screen.findByRole("group", { name: pm.groupStudents });
    await user.click(within(students).getByRole("option"));
    expect(router.push).toHaveBeenCalledWith("/students/s-3");
    second.unmount();

    expect(paletteRecents()).toEqual(["go:/imports"]);
    expect(paletteRecents().join(" ")).not.toMatch(/student:|pupil/i);
    renderWithIntl(palette(fakeSearch(run)));
    expect(screen.getByRole("group", { name: pm.groupRecent })).not.toHaveTextContent(/pupil/i);
    expect(setItem).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("a recent entry whose page is no longer offered is left out", async () => {
    const user = userEvent.setup();
    const first = renderWithIntl(palette());
    await user.type(screen.getByRole("combobox"), "imp");
    await user.keyboard("{Enter}");
    first.unmount();
    renderWithIntl(
      <CommandPalette
        open
        onClose={vi.fn()}
        items={ITEMS.filter((item) => item.id !== "go:/imports")}
        groups={GROUPS}
      />,
    );
    expect(screen.queryByRole("group", { name: pm.groupRecent })).toBeNull();
  });
});

describe("D72 school console: student search by permission, through the scoped API", () => {
  let stub: BffStub;
  beforeEach(() => {
    stub = installBffStub("staff");
  });
  afterEach(uninstallBffStub);

  it("with student.read_basic: POST /students/search, the name in the body, 8 rows at most", async () => {
    stub.routes["POST /bff/api/v1/students/search"] = () =>
      page([
        {
          id: "0190a0b0-0000-7000-8000-000000000001",
          display_name: "Sample Pupil",
          admission_no: "2019/0457",
          status: "active",
          class_section: "9A",
          section_id: null,
          match: { field: "full_name", score: 0.9 },
        },
      ]);
    const user = userEvent.setup();
    renderWithIntl(
      <SchoolShell permissions={["student.read_basic"]}>
        <p>page</p>
      </SchoolShell>,
    );
    await user.keyboard("{Control>}k{/Control}");
    await user.type(screen.getByRole("combobox"), "sample");
    const group = await screen.findByRole("group", { name: pm.groupStudents });
    expect(within(group).getByRole("option")).toHaveTextContent("Sample Pupil");
    expect(within(group).getByRole("option")).toHaveTextContent("9A · 2019/0457");
    const calls = stub.callsTo("POST /bff/api/v1/students/search");
    expect(calls).toHaveLength(1);
    expect(JSON.parse(calls[0]?.body ?? "{}")).toEqual({ query: "sample", limit: 8 });
    expect(calls[0]?.url.search).toBe("");
    await user.keyboard("{Enter}");
    expect(router.push).toHaveBeenCalledWith("/students/0190a0b0-0000-7000-8000-000000000001");
  });

  it("without it (or with unknown permissions): no Students group and no request", async () => {
    const user = userEvent.setup();
    for (const permissions of [["attendance.read"], null]) {
      const view = renderWithIntl(
        <SchoolShell permissions={permissions}>
          <p>page</p>
        </SchoolShell>,
      );
      await user.keyboard("{Control>}k{/Control}");
      const box = screen.getByRole("combobox");
      expect(box).toHaveAttribute("placeholder", pm.placeholder);
      await user.type(box, "sample");
      await act(() => new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_MS + 150)));
      expect(screen.queryByRole("group", { name: pm.groupStudents })).toBeNull();
      view.unmount();
    }
    expect(stub.callsTo("POST /bff/api/v1/students/search")).toHaveLength(0);
  });
});

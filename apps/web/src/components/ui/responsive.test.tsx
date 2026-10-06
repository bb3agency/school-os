import { act, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { closeDialog } from "@/lib/dialog-motion";
import { renderWithIntl } from "@/test/render";
import { NarrowSwitch } from "./NarrowSwitch";
import { DataTable, StackedRows, Table, stackPlace, type Column } from "./Table";

/**
 * Mobile rules (docs/17 §5.7, NFR-A11Y-001): key record lists stack as cards below 640px,
 * record field tables reflow, dialogs leave as bottom sheets on phones.
 */

interface Row {
  id: string;
  name: string;
  count: number;
}

const ROWS: Row[] = [
  { id: "a", name: "Synthetica Ravi", count: 3 },
  { id: "b", name: "Synthetica Anjali", count: 12 },
];

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Name", cell: (row) => <a href={`/s/${row.id}`}>{row.name}</a> },
  { key: "count", header: "Open findings", numeric: true, cell: (row) => row.count },
  { key: "open", header: "", cell: (row) => <button type="button">Open {row.name}</button> },
];

/** A matchMedia stand-in whose `(width < 40rem)` answer can change while mounted. */
function stubWidth(narrow: boolean) {
  const listeners = new Set<() => void>();
  const state = { narrow };
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      get matches() {
        return query === "(width < 40rem)" ? state.narrow : false;
      },
      addEventListener: (_: string, fn: () => void) => listeners.add(fn),
      removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
    })),
  );
  return (next: boolean) => {
    state.narrow = next;
    act(() => listeners.forEach((fn) => fn()));
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as { animate?: unknown }).animate;
  document.body.innerHTML = "";
});

describe("stackPlace", () => {
  it("first column is the title, empty headers are actions, the rest are fields", () => {
    expect(COLUMNS.map((column, i) => stackPlace(column, i))).toEqual([
      "title",
      "field",
      "actions",
    ]);
    expect(stackPlace({ ...COLUMNS[0]!, stack: "field" }, 0)).toBe("field");
  });
});

describe("StackedRows: the card layout of a table", () => {
  it("is one list named by the caption; title, label/value pairs and actions per row", () => {
    renderWithIntl(
      <StackedRows caption="Students" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />,
    );
    const list = screen.getByRole("list", { name: "Students" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const first = items[0]!;
    expect(within(first).getByRole("link", { name: "Synthetica Ravi" })).toBeInTheDocument();
    // The header is the label of its value (a description list), numbers keep tabular figures.
    const term = within(first).getByText("Open findings");
    expect(term.tagName).toBe("DT");
    expect(term.nextElementSibling).toHaveTextContent("3");
    expect(term.nextElementSibling).toHaveClass("tabular-nums");
    expect(within(first).getByRole("button", { name: "Open Synthetica Ravi" })).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("a wide field puts its label above a value that spans the card", () => {
    const columns: Column<Row>[] = [
      COLUMNS[0]!,
      { key: "note", header: "Reason", stack: "wide", cell: (row) => `Long note ${row.id}` },
    ];
    renderWithIntl(
      <StackedRows caption="Payments" columns={columns} rows={ROWS} rowKey={(r) => r.id} />,
    );
    const first = screen.getAllByRole("listitem")[0]!;
    const term = within(first).getByText("Reason");
    expect(term.tagName).toBe("DT");
    expect(term).toHaveClass("col-span-2");
    expect(term.nextElementSibling).toHaveTextContent("Long note a");
    expect(term.nextElementSibling).toHaveClass("col-span-2");
  });

  it("shows the caption unless it is hidden (the list keeps it as its name)", () => {
    renderWithIntl(
      <StackedRows caption="Students" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />,
    );
    expect(screen.getByText("Students", { selector: "p" })).toBeInTheDocument();
  });
});

describe("DataTable stacked (docs/17 §5.7)", () => {
  const table = (
    <DataTable
      stacked
      caption="Students"
      columns={COLUMNS}
      state={{ status: "ready", data: ROWS }}
      rowKey={(r) => r.id}
      emptyTitle="No students"
    />
  );

  it("is a table where the width is unknown (server, no matchMedia)", () => {
    renderWithIntl(table);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Students" })).toBeNull();
  });

  it("is a list of cards below 640px and a table again when the window widens", () => {
    const setNarrow = stubWidth(true);
    renderWithIntl(table);
    expect(screen.getByRole("list", { name: "Students" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    setNarrow(false);
    expect(screen.getByRole("table")).toBeInTheDocument();
    // Exactly one layout is ever in the DOM: one link per student.
    expect(screen.getAllByRole("link", { name: "Synthetica Ravi" })).toHaveLength(1);
  });

  it("without `stacked` a table stays a (sideways scrolling) table on a phone", () => {
    stubWidth(true);
    renderWithIntl(
      <DataTable
        caption="Ledger"
        columns={COLUMNS}
        state={{ status: "ready", data: ROWS }}
        rowKey={(r) => r.id}
        emptyTitle="Nothing"
      />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /Ledger/ })).toBeInTheDocument();
  });

  it("keeps its loading and empty states", () => {
    stubWidth(true);
    renderWithIntl(
      <DataTable
        stacked
        caption="Students"
        columns={COLUMNS}
        state={{ status: "ready", data: [] }}
        rowKey={(r) => r.id}
        emptyTitle="No students yet"
      />,
    );
    expect(screen.getByText("No students yet")).toBeInTheDocument();
  });
});

describe("NarrowSwitch", () => {
  it("renders only one of its two layouts", () => {
    const setNarrow = stubWidth(false);
    renderWithIntl(<NarrowSwitch wide={<p>wide</p>} narrow={<p>narrow</p>} />);
    expect(screen.getByText("wide")).toBeInTheDocument();
    expect(screen.queryByText("narrow")).toBeNull();
    setNarrow(true);
    expect(screen.getByText("narrow")).toBeInTheDocument();
    expect(screen.queryByText("wide")).toBeNull();
  });
});

describe("Table reflow", () => {
  it("only adds the below-640px block layout when asked", () => {
    const { container, rerender } = renderWithIntl(<Table reflow />);
    expect(container.querySelector("table")?.className).toContain("max-sm:[&_tr]:block");
    rerender(<Table />);
    expect(container.querySelector("table")?.className).not.toContain("max-sm:");
  });
});

describe("dialogs as bottom sheets on phones (docs/17 §5.7)", () => {
  function openDialog(): HTMLDialogElement {
    const dialog = document.createElement("dialog");
    document.body.append(dialog);
    dialog.showModal();
    return dialog;
  }

  it("a modal leaves downwards below 640px and shrinks back on wider screens", () => {
    const animate = vi.fn(
      () => ({ finished: new Promise(() => undefined) }) as unknown as Animation,
    );
    Object.defineProperty(HTMLElement.prototype, "animate", {
      configurable: true,
      writable: true,
      value: animate,
    });
    stubWidth(true);
    closeDialog(openDialog(), "modal");
    expect(animate).toHaveBeenLastCalledWith(
      [{ transform: "translateY(0)" }, { transform: "translateY(100%)" }],
      expect.objectContaining({ duration: 150 }),
    );
    // The menu drawer keeps its own exit on a phone.
    closeDialog(openDialog(), "drawer");
    expect(animate).toHaveBeenLastCalledWith(
      [{ transform: "translateX(0)" }, { transform: "translateX(-100%)" }],
      expect.objectContaining({ duration: 150 }),
    );
    vi.unstubAllGlobals();
    stubWidth(false);
    closeDialog(openDialog(), "modal");
    expect(animate).toHaveBeenLastCalledWith(
      [
        { opacity: 1, transform: "none" },
        { opacity: 0, transform: "scale(0.97)" },
      ],
      expect.objectContaining({ duration: 150 }),
    );
  });
});

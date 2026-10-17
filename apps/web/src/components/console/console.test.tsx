/* eslint-disable no-restricted-properties -- the table remembers hidden columns and density,
   UI preferences only (no tokens, no personal data); these tests seed and read them. */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Button } from "@/components/ui/Button";
import { loading, ready, type Loadable } from "@/lib/loadable";
import { renderWithIntl } from "@/test/render";
import { DataTableV2, type ColumnV2 } from "./DataTableV2";
import { rankItems } from "./rank";

interface Row {
  id: string;
  name: string;
  klass: string;
  due: string;
}

const ROWS: Row[] = [
  { id: "1", name: "Sample student A", klass: "7-B", due: "₹1,25,000.00" },
  { id: "2", name: "Sample student B", klass: "8-A", due: "₹0.00" },
];

const COLUMNS: ColumnV2<Row>[] = [
  { key: "name", header: "Name", cell: (row) => row.name },
  { key: "class", header: "Class", cell: (row) => row.klass },
  { key: "due", header: "Due (₹)", cell: (row) => row.due, numeric: true },
];

function table(
  state: Loadable<readonly Row[]>,
  extra: Partial<Parameters<typeof DataTableV2<Row>>[0]> = {},
) {
  return (
    <DataTableV2<Row>
      caption="Students"
      columns={COLUMNS}
      state={state}
      rowKey={(row) => row.id}
      emptyTitle="No students yet"
      emptyBody="Import the admission register to start."
      id="test-students"
      {...extra}
    />
  );
}

afterEach(() => {
  window.localStorage.clear();
});

describe("rankItems (palette search)", () => {
  const items = [
    { id: "a", label: "Students" },
    { id: "b", label: "Student imports" },
    { id: "c", label: "Fee setup", keywords: "Fees" },
    { id: "d", label: "Settings" },
  ];

  it("keeps order for an empty query and ranks word starts first", () => {
    expect(rankItems(items, "").map((item) => item.id)).toEqual(["a", "b", "c", "d"]);
    expect(rankItems(items, "imp").map((item) => item.id)).toEqual(["b"]);
    expect(rankItems(items, "st")[0]?.id).toBe("a");
  });

  it("matches keywords slightly lower and is case-insensitive", () => {
    expect(rankItems(items, "FEES").map((item) => item.id)).toContain("c");
    expect(rankItems(items, "xyz")).toEqual([]);
  });
});

describe("DataTableV2 (docs/17 §4.3)", () => {
  it("a named, focusable scroll region with a sticky header and numeric columns in figures", () => {
    renderWithIntl(table(ready(ROWS)));
    const region = screen.getByRole("region", { name: "Students" });
    expect(region).toHaveAttribute("tabindex", "0");
    const header = screen.getByRole("columnheader", { name: "Due (₹)" });
    expect(header).toHaveClass("sticky", "top-0", "num", "text-end");
    expect(screen.getByRole("cell", { name: "₹1,25,000.00" })).toHaveClass("num", "tabular-nums");
  });

  it("states: loading, error with retry, first use, no results with Clear filters", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithIntl(table(loading));
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    unmount();

    const onRetry = vi.fn();
    const second = renderWithIntl(table({ status: "error" }, { onRetry }));
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
    second.unmount();

    const third = renderWithIntl(table(ready([])));
    expect(screen.getByText("No students yet")).toBeInTheDocument();
    third.unmount();

    const onClear = vi.fn();
    renderWithIntl(
      table(ready([]), {
        chips: [{ id: "class", label: "Class", value: "9-C" }],
        onRemoveChip: vi.fn(),
        onClearFilters: onClear,
      }),
    );
    expect(screen.getByText("No rows match these filters")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Active filters" })).toHaveTextContent("Class: 9-C");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onClear).toHaveBeenCalled();
  });

  it("column chooser hides a column and remembers it; the first column stays", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithIntl(table(ready(ROWS)));
    await user.click(screen.getByRole("button", { name: "Columns" }));
    const panel = screen.getByRole("dialog", { name: "Columns" });
    expect(within(panel).getByRole("checkbox", { name: "Name" })).toBeDisabled();
    await user.click(within(panel).getByRole("checkbox", { name: "Class" }));
    expect(screen.queryByRole("columnheader", { name: "Class" })).toBeNull();
    expect(window.localStorage.getItem("sos.table.test-students.hidden")).toBe("class");
    // Density for this table only.
    await user.click(within(panel).getByRole("radio", { name: "Compact" }));
    expect(screen.getByRole("table")).toHaveAttribute("data-density", "compact");
    unmount();
    renderWithIntl(table(ready(ROWS)));
    expect(screen.queryByRole("columnheader", { name: "Class" })).toBeNull();
    expect(screen.getByRole("table")).toHaveAttribute("data-density", "compact");
  });

  it("selection: named checkboxes, select all, a bulk bar with the count and actions", async () => {
    const user = userEvent.setup();
    const exportRows = vi.fn();
    renderWithIntl(
      table(ready(ROWS), {
        selection: {
          rowLabel: (row) => `Select ${row.name}`,
          actions: (selected) => (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => exportRows(selected.map((row) => row.id))}
            >
              Export selected
            </Button>
          ),
        },
      }),
    );
    expect(screen.queryByRole("region", { name: "Actions for the selected rows" })).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "Select Sample student B" }));
    const bar = screen.getByRole("region", { name: "Actions for the selected rows" });
    expect(within(bar).getByRole("status")).toHaveTextContent("1 selected");
    expect(screen.getByRole("checkbox", { name: "Select all rows on this page" })).toHaveProperty(
      "indeterminate",
      true,
    );
    await user.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
    expect(within(bar).getByRole("status")).toHaveTextContent("2 selected");
    await user.click(within(bar).getByRole("button", { name: "Export selected" }));
    expect(exportRows).toHaveBeenCalledWith(["1", "2"]);
    await user.click(within(bar).getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByRole("region", { name: "Actions for the selected rows" })).toBeNull();
  });

  it("keyboard: Space toggles a row's checkbox", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      table(ready(ROWS), {
        selection: { rowLabel: (row) => `Select ${row.name}`, actions: () => null },
      }),
    );
    const box = screen.getByRole("checkbox", { name: "Select Sample student A" });
    box.focus();
    await user.keyboard(" ");
    expect(box).toBeChecked();
    expect(box.closest("tr")).toHaveAttribute("aria-selected", "true");
  });
});

import { act, fireEvent, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { FormDialog } from "@/features/students/FormDialog";
import * as z from "@/lib/zod";
import { renderWithIntl } from "@/test/render";
import { ActionDialog } from "./ActionDialog";
import { inlineShift } from "./keep-in-view";
import { Menu } from "./Menu";
import { Pagination } from "./Pagination";
import { SaveBar } from "./SaveBar";
import { SegmentedControl } from "./SegmentedControl";

/**
 * Phone rules added by the mobile pass (docs/17 §5.7): docked bars stand on the bottom bar,
 * panels stay on screen, segmented controls and paging fill a phone's width, dialogs open
 * from a menu without a hidden trigger, and DataTableV2 keeps selection on phones.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

function stubNarrow(narrow: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query === "(width < 40rem)" ? narrow : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

describe("inlineShift: overlay panels stay inside a phone's screen", () => {
  it("leaves a panel that fits", () => {
    expect(inlineShift(20, 300, 360)).toBe(0);
  });
  it("moves a panel sticking out on the right back in, keeping 8px", () => {
    expect(inlineShift(200, 488, 360)).toBe(-136);
  });
  it("moves a panel sticking out on the left back in", () => {
    expect(inlineShift(-40, 248, 360)).toBe(48);
  });
  it("keeps the start edge in view when the panel is wider than the screen", () => {
    expect(inlineShift(100, 500, 320)).toBe(-92);
  });
  it("ignores an unmeasured panel (no layout, e.g. a test DOM)", () => {
    expect(inlineShift(0, 0, 0)).toBe(0);
  });
});

describe("Menu panel on a phone", () => {
  it("is nudged inside the screen with the CSSOM translate (no style attribute markup)", () => {
    renderWithIntl(
      <Menu label="More" entries={[{ id: "a", label: "Archive", onSelect: () => undefined }]} />,
    );
    const rect = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ left: 200, right: 1100, top: 0, bottom: 10 } as DOMRect);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const menu = screen.getByRole("menu");
    // jsdom's clientWidth is 0, so the window width (1024) counts: 84px back in.
    expect(menu.style.translate).toBe("-84px 0");
    rect.mockRestore();
  });
});

describe("SaveBar", () => {
  it("docks above the phone bottom bar and bleeds to its card by default", () => {
    renderWithIntl(
      <SaveBar dirty>
        <button type="submit">Save</button>
      </SaveBar>,
    );
    const bar = screen.getByRole("status").parentElement!;
    expect(bar).toHaveAttribute("data-dock");
    expect(bar).toHaveAttribute("data-placement", "inset");
    expect(bar.className).toContain("bottom-[var(--dock-offset)]");
    expect(bar.className).toContain("-mx-4");
  });

  it("has a frame of its own and no negative margins on the page", () => {
    renderWithIntl(
      <SaveBar dirty={false} placement="page">
        <button type="submit">Save</button>
      </SaveBar>,
    );
    const bar = screen.getByRole("status").parentElement!;
    expect(bar).toHaveAttribute("data-placement", "page");
    expect(bar.className).not.toContain("-mx-4");
    expect(bar.className).toContain("rounded-lg border");
  });
});

describe("SegmentedControl", () => {
  const options = [
    { value: "a", label: "All" },
    { value: "b", label: "Mine" },
  ];
  it("fills a phone's width by default", () => {
    renderWithIntl(<SegmentedControl legend="Show" options={options} />);
    const group = screen.getByRole("group", { name: "Show" });
    expect(group.className).toContain("max-sm:w-full");
    expect(screen.getByText("All").closest("label")?.className).toContain("max-sm:flex-1");
  });
  it("keeps its width with block={false}", () => {
    renderWithIntl(<SegmentedControl legend="Show" options={options} block={false} />);
    expect(screen.getByRole("group", { name: "Show" }).className).not.toContain("max-sm:w-full");
  });
});

describe("Pagination on a phone", () => {
  it("lets Previous and Next share the width", () => {
    renderWithIntl(
      <Pagination
        from={1}
        to={50}
        hasPrevious={false}
        hasNext
        onPrevious={() => undefined}
        onNext={() => undefined}
      />,
    );
    const next = screen.getByRole("button", { name: /Next/ });
    expect(next.parentElement?.className).toContain("max-sm:[&>*]:flex-1");
  });
});

const schema = z.object({});

function ControlledAction({ onClosed }: { onClosed: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open from menu
      </button>
      <ActionDialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) onClosed();
        }}
        title="Cancel receipt"
        confirmLabel="Cancel receipt"
        schema={schema}
        submit={async () => ({})}
      />
    </>
  );
}

describe("ActionDialog controlled open", () => {
  it("renders no trigger, opens from outside and reports its close", () => {
    const onClosed = vi.fn();
    renderWithIntl(<ControlledAction onClosed={onClosed} />);
    expect(screen.queryByRole("button", { name: "Cancel receipt" })).toBeNull();
    const opener = screen.getByRole("button", { name: "Open from menu" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Cancel receipt" });
    expect(dialog).toHaveAttribute("open");
    act(() => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    });
    expect(dialog).not.toHaveAttribute("open");
    expect(onClosed).toHaveBeenCalledTimes(1);
    expect(opener).toHaveFocus();
    // Closed stays closed until the parent opens it again.
    fireEvent.click(opener);
    expect(dialog).toHaveAttribute("open");
  });

  it("still works with its own trigger (uncontrolled)", () => {
    renderWithIntl(
      <ActionDialog
        triggerLabel="Archive"
        title="Archive this"
        confirmLabel="Archive"
        schema={schema}
        submit={async () => ({})}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    expect(screen.getByRole("dialog", { name: "Archive this" })).toHaveAttribute("open");
  });
});

describe("FormDialog controlled open", () => {
  it("opens without a trigger and closes when the parent says so", () => {
    function Host() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen((value) => !value)}>
            Toggle
          </button>
          <FormDialog
            open={open}
            title="Edit details"
            confirmLabel="Save"
            schema={schema}
            submit={async () => ({})}
            problems="students.errors"
          />
        </>
      );
    }
    renderWithIntl(<Host />);
    const dialog = document.querySelector("dialog")!;
    expect(dialog).not.toHaveAttribute("open");
    // Only the parent's own button: no trigger of the dialog's.
    expect(screen.getAllByRole("button")).toHaveLength(1);
    const toggle = screen.getByRole("button", { name: "Toggle" });
    fireEvent.click(toggle);
    expect(dialog).toHaveAttribute("open");
    act(() => {
      (dialog as HTMLDialogElement).close();
    });
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(dialog).toHaveAttribute("open");
  });
});

interface Row {
  id: string;
  name: string;
}

const COLUMNS: ColumnV2<Row>[] = [
  { key: "name", header: "Name", cell: (row) => row.name },
  { key: "id", header: "ID", cell: (row) => row.id },
];

describe("DataTableV2 on phones and with view chips", () => {
  it("keeps row selection in the stacked cards", () => {
    stubNarrow(true);
    renderWithIntl(
      <DataTableV2
        caption="Dues"
        columns={COLUMNS}
        state={{ status: "ready", data: [{ id: "r1", name: "Synthetica Ravi" }] }}
        rowKey={(row) => row.id}
        emptyTitle="Nothing"
        stacked
        selection={{
          rowLabel: (row) => `Select ${row.name}`,
          actions: (selected) => <span>{selected.length} chosen</span>,
        }}
      />,
    );
    const list = screen.getByRole("list", { name: "Dues" });
    // No second frame inside the table's own.
    expect(list.parentElement?.className).not.toContain("border");
    const box = within(list).getByRole("checkbox", { name: "Select Synthetica Ravi" });
    fireEvent.click(box);
    expect(
      screen.getByRole("region", { name: "Actions for the selected rows" }),
    ).toBeInTheDocument();
    expect(screen.getByText("1 chosen")).toBeInTheDocument();
  });

  it("shows the first-use state despite chips when noResultsState is off", () => {
    renderWithIntl(
      <DataTableV2
        caption="Approvals"
        columns={COLUMNS}
        state={{ status: "ready", data: [] }}
        rowKey={(row) => row.id}
        chips={[{ id: "mine", label: "View", value: "Waiting for me" }]}
        onRemoveChip={() => undefined}
        emptyTitle="Nothing waiting for you"
        noResultsState={false}
      />,
    );
    expect(screen.getByText("Nothing waiting for you")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Active filters" })).toBeInTheDocument();
  });
});

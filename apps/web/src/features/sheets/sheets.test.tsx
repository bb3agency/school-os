import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentSheetScreen, editsBody } from "@/features/documents/DocumentSheet";
import { ImportSheetScreen } from "@/features/imports/ImportSheet";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { ATTRIBUTES, fakeAadhaar, ID, me } from "@/test/records-fixtures";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { downloadName, setSheetSaverForTesting } from "./download";
import { SheetGrid, cellProblem, normaliseCellValue, type SheetGridRow } from "./SheetGrid";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/imports",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** Sheet editor screens (US-401 AC5/AC6, US-701 AC5; FR-IMP-008/009, FR-DOC-009..011). */

type Schemas = components["schemas"];
const sh = messages.en.sheets;
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  setSheetSaverForTesting(undefined);
  uninstallBffStub();
});

/* ---------------------------------------------------------------- grid */

const GRID_COLUMNS = [
  { index: 0, letter: "A", header: "Adm No", restricted: false, editable: true },
  { index: 1, header: "Name", letter: "B", restricted: false, editable: true },
  { index: 2, header: "Religion", letter: "C", restricted: true, editable: false },
];

function gridRows(): SheetGridRow[] {
  return [
    {
      rowNo: 2,
      cells: [{ value: "ADM-1" }, { value: "Synthetica One" }, { value: null, restricted: true }],
    },
    {
      rowNo: 3,
      cells: [
        { value: "ADM-2" },
        { value: "=SUM(A1)", formula: true },
        { restricted: true, value: null },
      ],
    },
  ];
}

function Harness({
  onCommit,
  editable = true,
}: {
  onCommit: (row: number, column: number, value: string | null) => void;
  editable?: boolean;
}) {
  const [rows] = useState(gridRows);
  return (
    <SheetGrid
      caption="Rows"
      columns={GRID_COLUMNS}
      rows={rows}
      editable={editable}
      onCommit={onCommit}
    />
  );
}

/** Tab reaches the scroll region (WCAG 2.1.1), then the grid's one cell tab stop. */
async function tabIntoGrid(user: ReturnType<typeof userEvent.setup>) {
  await user.tab();
  expect(screen.getByRole("region", { name: /Scroll sideways/ })).toHaveFocus();
  await user.tab();
}

describe("SheetGrid: keyboard use (arrows, Enter, Escape)", () => {
  it("moves with the arrow keys, edits with Enter and cancels with Escape", async () => {
    const commit = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<Harness onCommit={commit} />);
    const grid = screen.getByRole("grid", { name: "Rows" });
    const cells = within(grid).getAllByRole("gridcell");
    // One tab stop inside the grid: the first cell (after the sideways-scroll region).
    expect(cells.filter((cell) => cell.tabIndex === 0)).toHaveLength(1);
    await tabIntoGrid(user);
    expect(cells[0]).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(cells[1]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(cells[4]).toHaveFocus();
    await user.keyboard("{ArrowUp}{Home}");
    expect(cells[0]).toHaveFocus();
    await user.keyboard("{Control>}{End}{/Control}");
    expect(cells[5]).toHaveFocus();

    // Escape cancels without saving and puts focus back on the cell.
    await user.keyboard("{Control>}{Home}{/Control}{ArrowRight}{Enter}");
    const input = screen.getByRole("textbox", {
      name: sh.grid.editLabel.replace("{column}", "Name").replace("{row}", "2"),
    });
    expect(input).toHaveFocus();
    await user.clear(input);
    await user.type(input, "Something else");
    await user.keyboard("{Escape}");
    expect(commit).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    await waitFor(() => expect(cells[1]).toHaveFocus());

    // Enter saves the NFC, trimmed value; blank clears the cell.
    await user.keyboard("{Enter}");
    const again = screen.getByRole("textbox");
    await user.clear(again);
    await user.type(again, "  సింథెటిక  {Enter}");
    expect(commit).toHaveBeenCalledWith(2, 1, "సింథెటిక");
  });

  it("refuses a full Aadhaar number before anything is sent", async () => {
    const commit = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<Harness onCommit={commit} />);
    await tabIntoGrid(user);
    await user.keyboard("{Enter}");
    const input = screen.getByRole("textbox");
    await user.clear(input);
    await user.type(input, `${fakeAadhaar()}{Enter}`);
    expect(commit).not.toHaveBeenCalled();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(sh.grid.problem.aadhaar)).toBeInTheDocument();
  });

  it("never edits restricted cells and shows formulas as text", async () => {
    const commit = vi.fn();
    const user = userEvent.setup();
    renderWithIntl(<Harness onCommit={commit} />);
    const cells = within(screen.getByRole("grid")).getAllByRole("gridcell");
    expect(cells[2]).toHaveAttribute("aria-readonly", "true");
    expect(within(cells[2] as HTMLElement).getByText(sh.grid.restrictedSr)).toBeInTheDocument();
    expect(within(cells[4] as HTMLElement).getByText("=SUM(A1)")).toBeInTheDocument();
    expect(within(cells[4] as HTMLElement).getByText(sh.grid.formula)).toBeInTheDocument();
    await tabIntoGrid(user);
    await user.keyboard("{ArrowRight}{ArrowRight}{Enter}");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("is read-only when it may not be edited", async () => {
    const user = userEvent.setup();
    renderWithIntl(<Harness onCommit={vi.fn()} editable={false} />);
    expect(screen.getByRole("grid")).toHaveAttribute("aria-readonly", "true");
    expect(screen.getByText(sh.grid.keyboardHelpRead)).toBeInTheDocument();
    await tabIntoGrid(user);
    await user.keyboard("{Enter}");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("checks values like the API", () => {
    expect(cellProblem(fakeAadhaar())).toBe("aadhaar");
    expect(cellProblem("x".repeat(1001))).toBe("tooLong");
    expect(cellProblem("a\u0007b")).toBe("control");
    expect(cellProblem("Synthetica")).toBeNull();
    expect(normaliseCellValue("   ")).toBeNull();
    expect(normaliseCellValue(" é ")).toBe("é");
    expect(downloadName('attachment; filename="import-0192-sheet.csv"', "x.csv")).toBe(
      "import-0192-sheet.csv",
    );
    expect(downloadName('attachment; filename="../../evil name.csv"', "x.csv")).toBe(
      "_.._evil_name.csv",
    );
    expect(downloadName(null, "fallback.xlsx")).toBe("fallback.xlsx");
  });
});

/* ---------------------------------------------------------------- import sheet */

function importSheet(extra: Partial<Schemas["ImportSheetOut"]> = {}): Schemas["ImportSheetOut"] {
  return {
    import_id: ID.import,
    status: "validated",
    version: 4,
    editable: true,
    read_only_reason: null,
    header_row: 1,
    total_rows: 2,
    offset: 0,
    edited_cells: 0,
    columns: [
      {
        index: 0,
        letter: "A",
        header: "Adm No",
        target: "admission_no",
        restricted: false,
        editable: true,
      },
      {
        index: 1,
        letter: "B",
        header: "Name",
        target: "full_name",
        restricted: false,
        editable: true,
      },
      {
        index: 2,
        letter: "C",
        header: "Religion",
        target: null,
        restricted: true,
        editable: false,
      },
    ],
    data: [
      {
        row_no: 2,
        cells: [
          { value: "ADM-1", edited: false, restricted: false, formula: false },
          { value: "Synthetica One", edited: false, restricted: false, formula: false },
          { value: null, edited: false, restricted: true, formula: false },
        ],
        status: "valid",
        errors: [],
        warnings: [],
      },
      {
        row_no: 3,
        cells: [
          { value: null, edited: false, restricted: false, formula: false },
          { value: "Synthetica Two", edited: false, restricted: false, formula: false },
          { value: null, edited: false, restricted: true, formula: false },
        ],
        status: "error",
        errors: [{ field: "admission_no", code: "missing", message_key: "errors.missing" }],
        warnings: [],
      },
    ],
    next_cursor: null,
    ...extra,
  };
}

function importRoutes(sheet: Schemas["ImportSheetOut"] = importSheet()) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["import.run", "import.commit"]));
  stub.routes["GET /bff/api/v1/attributes"] = () => Response.json(ATTRIBUTES);
  stub.routes[`GET /bff/api/v1/imports/${ID.import}/sheet`] = () => Response.json(sheet);
}

describe("US-401 AC5/AC6: the import sheet", () => {
  it("shows every row with its check, hides restricted columns and saves an edit", async () => {
    importRoutes();
    stub.routes[`PATCH /bff/api/v1/imports/${ID.import}/sheet/rows/3`] = () =>
      Response.json({
        row: {
          row_no: 3,
          cells: [
            { value: "ADM-9", edited: true, restricted: false, formula: false },
            { value: "Synthetica Two", edited: false, restricted: false, formula: false },
            { value: null, edited: false, restricted: true, formula: false },
          ],
          status: "valid",
          errors: [],
          warnings: [],
        },
        version: 5,
        status: "validated",
        row_count: 2,
        error_count: 0,
        changed_rows: [],
      });
    const user = userEvent.setup();
    renderWithIntl(<ImportSheetScreen importId={ID.import} />);
    const grid = await screen.findByRole("grid", { name: sh.import.gridTitle });
    expect(within(grid).getByText("Synthetica One")).toBeInTheDocument();
    expect(screen.getByText(/1 column holds restricted details/)).toBeInTheDocument();
    expect(within(grid).getByText(messages.en.imports.rows.status.error)).toBeInTheDocument();

    // Row 3, column A: from the check cell of row 2, down and right.
    const cells = within(grid).getAllByRole("gridcell");
    (cells[0] as HTMLElement).focus();
    await user.keyboard("{ArrowDown}{ArrowRight}{Enter}");
    const input = screen.getByRole("textbox");
    await user.type(input, "ADM-9{Enter}");

    await screen.findByText(sh.import.savedValid.replace("{row}", "3"));
    const [call] = stub.callsTo(`PATCH /bff/api/v1/imports/${ID.import}/sheet/rows/3`);
    expect(call?.headers.get("if-match")).toBe('W/"4"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ cells: [{ column: 0, value: "ADM-9" }] });
    expect(screen.getByRole("status")).toHaveTextContent(
      sh.import.savedValid.replace("{row}", "3"),
    );
    expect(intlErrors).toEqual([]);
  });

  it("explains a change someone else made meanwhile (412)", async () => {
    importRoutes();
    stub.routes[`PATCH /bff/api/v1/imports/${ID.import}/sheet/rows/2`] = () =>
      problem(412, "precondition_failed");
    const user = userEvent.setup();
    renderWithIntl(<ImportSheetScreen importId={ID.import} />);
    const grid = await screen.findByRole("grid");
    (within(grid).getAllByRole("gridcell")[1] as HTMLElement).focus();
    await user.keyboard("{Enter}");
    await user.type(screen.getByRole("textbox"), "-X{Enter}");
    expect(await screen.findByText(sh.errors.precondition_failed.title)).toBeInTheDocument();
  });

  it("is read-only after the rows were added and downloads the sheet", async () => {
    importRoutes(
      importSheet({ editable: false, read_only_reason: "committed", status: "committed" }),
    );
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/sheet/export`] = () =>
      new Response("﻿Adm No\r\n", {
        headers: {
          "content-type": "text/csv",
          "content-disposition": 'attachment; filename="import-0192f3a4-sheet.csv"',
        },
      });
    const saved = vi.fn();
    setSheetSaverForTesting(saved);
    const user = userEvent.setup();
    renderWithIntl(<ImportSheetScreen importId={ID.import} />);
    expect(await screen.findByText(sh.import.readOnly.committed)).toBeInTheDocument();
    expect(screen.getByRole("grid")).toHaveAttribute("aria-readonly", "true");
    await user.click(screen.getByRole("button", { name: sh.import.download.csv }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(saved.mock.calls[0]?.[1]).toBe("import-0192f3a4-sheet.csv");
    const [call] = stub.callsTo(`GET /bff/api/v1/imports/${ID.import}/sheet/export`);
    expect(call?.url.searchParams.get("format")).toBe("csv");
  });

  it("renders in Telugu without missing messages", async () => {
    importRoutes();
    renderWithIntl(<ImportSheetScreen importId={ID.import} />, "te");
    expect(
      await screen.findByRole("grid", { name: messages.te.sheets.import.gridTitle }),
    ).toBeInTheDocument();
    expect(intlErrors).toEqual([]);
  });
});

/* ---------------------------------------------------------------- document sheet */

function documentSheet(
  extra: Partial<Schemas["DocumentSheetOut"]> = {},
): Schemas["DocumentSheetOut"] {
  return {
    document_id: ID.doc,
    version_no: 1,
    version: 7,
    kind: "xlsx",
    sheet_count: 1,
    editable: true,
    read_only_reason: null,
    total_rows: 2,
    offset: 0,
    columns: [
      { index: 0, letter: "A", header: "Receipt" },
      { index: 1, letter: "B", header: "Amount" },
    ],
    data: [
      {
        row_no: 2,
        cells: [
          { value: "R-001", formula: false },
          { value: "1200", formula: false },
        ],
      },
      {
        row_no: 3,
        cells: [
          { value: "R-002", formula: false },
          { value: "950", formula: false },
        ],
      },
    ],
    next_cursor: null,
    ...extra,
  };
}

describe("US-701 AC5: document sheets", () => {
  it("keeps changes on the page, saves them as a new version and downloads them", async () => {
    stub.routes[`GET /bff/api/v1/documents/${ID.doc}/sheet`] = () => Response.json(documentSheet());
    stub.routes[`POST /bff/api/v1/documents/${ID.doc}/sheet/versions`] = () =>
      Response.json({ id: ID.doc }, { status: 202 });
    stub.routes[`POST /bff/api/v1/documents/${ID.doc}/sheet/export`] = () =>
      new Response(new Uint8Array([0x50, 0x4b]), {
        headers: { "content-disposition": 'attachment; filename="circular-sheet.xlsx"' },
      });
    const saved = vi.fn();
    setSheetSaverForTesting(saved);
    const user = userEvent.setup();
    renderWithIntl(<DocumentSheetScreen documentId={ID.doc} />);
    const grid = await screen.findByRole("grid", { name: sh.document.gridTitle });
    (within(grid).getAllByRole("gridcell")[3] as HTMLElement).focus();
    await user.keyboard("{Enter}");
    const input = screen.getByRole("textbox");
    await user.clear(input);
    await user.type(input, "1300{Enter}");
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    expect(within(grid).getByText(sh.grid.edited)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: sh.document.download.xlsx }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    const [download] = stub.callsTo(`POST /bff/api/v1/documents/${ID.doc}/sheet/export`);
    expect(JSON.parse(download?.body ?? "{}")).toEqual({
      format: "xlsx",
      base_version_no: 1,
      edits: [{ row_no: 3, column: 1, value: "1300" }],
    });

    await user.click(screen.getByRole("button", { name: sh.document.save }));
    await screen.findByText(sh.document.saved.replace("{version}", "2"));
    const [save] = stub.callsTo(`POST /bff/api/v1/documents/${ID.doc}/sheet/versions`);
    expect(save?.headers.get("if-match")).toBe('W/"7"');
    expect(save?.headers.get("idempotency-key")).toBeTruthy();
    expect(JSON.parse(save?.body ?? "{}")).toEqual({
      base_version_no: 1,
      edits: [{ row_no: 3, column: 1, value: "1300" }],
    });
    expect(
      screen.getByText(sh.document.pending.replace(/\{count.*$/, "No unsaved changes")),
    ).toBeInTheDocument();
  });

  it("says why a sheet can't be saved", async () => {
    stub.routes[`GET /bff/api/v1/documents/${ID.doc}/sheet`] = () =>
      Response.json(
        documentSheet({ editable: false, read_only_reason: "formulas", sheet_count: 2 }),
      );
    renderWithIntl(<DocumentSheetScreen documentId={ID.doc} />);
    expect(await screen.findByText(sh.document.readOnly.formulas)).toBeInTheDocument();
    expect(screen.getByText(/1 more sheet that is not shown here/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: sh.document.save })).toBeNull();
  });

  it("explains a file that can't be opened as a sheet", async () => {
    stub.routes[`GET /bff/api/v1/documents/${ID.doc}/sheet`] = () =>
      problem(409, "import_file_sheet");
    renderWithIntl(<DocumentSheetScreen documentId={ID.doc} />);
    expect(await screen.findByText(sh.document.cannotOpenTitle)).toBeInTheDocument();
  });

  it("sends edits in row and column order", () => {
    const edits = new Map<string, string | null>([
      ["4:1", "b"],
      ["2:3", null],
      ["4:0", "a"],
    ]);
    expect(editsBody(edits)).toEqual([
      { row_no: 2, column: 3, value: null },
      { row_no: 4, column: 0, value: "a" },
      { row_no: 4, column: 1, value: "b" },
    ]);
  });
});

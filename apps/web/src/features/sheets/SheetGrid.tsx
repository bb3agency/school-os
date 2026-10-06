"use client";

import { useTranslations } from "next-intl";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Icon } from "@/components/ui/Icon";
import { SearchInput } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { cn } from "@/lib/cn";
import { useUnsavedChangesWarning } from "./unsaved";

/**
 * Spreadsheet grid for the sheet editor (US-401 AC5, US-701 AC5; FR-IMP-008, FR-DOC-009/010;
 * docs/17 §4.1).
 *
 * WAI-ARIA grid pattern with one tab stop (roving tabindex): Tab enters the grid on the last
 * focused cell and Tab again leaves it; the arrow keys move between cells (at an edge the key
 * is left to the browser, so the region still scrolls), Home/End go to the first/last cell of
 * the row and Ctrl+Home/Ctrl+End to the first/last cell of the grid. Enter or F2 (or a double
 * click) on a cell that may be changed opens an input with its value: Enter saves, Escape
 * cancels and puts focus back on the cell, Tab saves and leaves the grid. Values are single-line
 * text of at most 1,000 characters; a full Aadhaar number is refused here before anything is
 * sent (the API refuses it too). Restricted (C3) cells never carry a value and cannot be edited;
 * formula cells are shown as inert text. Changed cells are marked ("Changed" once saved, "Not
 * saved yet" while only on the page).
 *
 * The header row stays in view while the rows scroll inside their own labelled, focusable
 * region (sideways and down), so the page never scrolls sideways at 375px or 1366×768. A search
 * box (and a column choice) filters the rows of the page that is shown. No motion (docs/17 §2.6).
 */

export const MAX_CELL_CHARS = 1000;
const CONTROL = /[\u0000-\u001f\u007f]/;
const ALL = "all";

export interface SheetGridColumn {
  index: number;
  letter: string;
  header: string | null;
  /** What the column fills (e.g. the student field), shown under the header. */
  detail?: string | null | undefined;
  restricted: boolean;
  editable: boolean;
}

export interface SheetGridCell {
  value: string | null;
  /** Changed in SchoolOS and saved. */
  edited?: boolean | undefined;
  /** Changed on this page but not saved yet. */
  pending?: boolean | undefined;
  restricted?: boolean | undefined;
  formula?: boolean | undefined;
}

export interface SheetGridRow {
  rowNo: number;
  cells: readonly SheetGridCell[];
  /** Check result shown in the first column (e.g. a status pill with the problems). */
  check?: ReactNode;
  /** Marks the row as having problems (row header styling). */
  invalid?: boolean | undefined;
}

export type CellProblem = "aadhaar" | "tooLong" | "control";

/** The browser-side check of a new cell value (the API applies the same rules). */
export function cellProblem(value: string): CellProblem | null {
  if (containsFullAadhaar(value)) return "aadhaar";
  if (CONTROL.test(value)) return "control";
  if (value.length > MAX_CELL_CHARS) return "tooLong";
  return null;
}

/** A typed value as the API receives it: NFC, trimmed, blank = clear the cell. */
export function normaliseCellValue(value: string): string | null {
  const text = value.normalize("NFC").trim();
  return text === "" ? null : text;
}

/** Rows of the page whose (visible) cells contain `query`, in `column` or in any column. */
export function filterRows(
  rows: readonly SheetGridRow[],
  columns: readonly SheetGridColumn[],
  query: string,
  column: number | null,
): readonly SheetGridRow[] {
  const needle = query.normalize("NFC").trim().toLocaleLowerCase();
  if (!needle) return rows;
  const searched = columns.filter(
    (item) => !item.restricted && (column === null || item.index === column),
  );
  return rows.filter((row) =>
    searched.some((item) => {
      const cell = row.cells[item.index];
      if (!cell || cell.restricted || cell.value === null) return false;
      return cell.value.normalize("NFC").toLocaleLowerCase().includes(needle);
    }),
  );
}

export interface SheetGridProps {
  /** Names the grid and its scroll region. */
  caption: string;
  columns: readonly SheetGridColumn[];
  rows: readonly SheetGridRow[];
  /** Header of the check column; without it the grid has no check column. */
  checkHeader?: string | undefined;
  /** Cells can be opened for editing (per column `editable` still applies). */
  editable: boolean;
  /** While a save runs, editing is paused. */
  busy?: boolean | undefined;
  /** Show the "find on this page" search (default on). */
  searchable?: boolean | undefined;
  /**
   * Called with the new value (`null` clears the cell) when the user saves a changed cell.
   * Resolves once the parent has handled it; the grid then puts focus back on the cell (or,
   * after Tab, leaves it where Tab went).
   */
  onCommit?: (rowNo: number, column: number, value: string | null) => Promise<void> | void;
}

interface Position {
  row: number;
  col: number;
}

interface Editing extends Position {
  draft: string;
  original: string;
  problem: CellProblem | null;
}

function focusKey(row: number, col: number): string {
  return `${row}:${col}`;
}

const TABBABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Focus the next (or previous) tab stop outside `container`, as Tab would from it. */
function focusOutside(container: HTMLElement, forward: boolean): void {
  const stops = [...document.querySelectorAll<HTMLElement>(TABBABLE)].filter(
    (element) =>
      !container.contains(element) && element.tabIndex >= 0 && !element.closest("[inert]"),
  );
  const relation = forward ? Node.DOCUMENT_POSITION_FOLLOWING : Node.DOCUMENT_POSITION_PRECEDING;
  const candidates = stops.filter(
    (element) => (container.compareDocumentPosition(element) & relation) !== 0,
  );
  const target = forward ? candidates[0] : candidates[candidates.length - 1];
  target?.focus();
}

export function SheetGrid({
  caption,
  columns,
  rows,
  checkHeader,
  editable,
  busy = false,
  searchable = true,
  onCommit,
}: SheetGridProps) {
  const t = useTranslations("sheets.grid");
  const tc = useTranslations("common");
  const tt = useTranslations("touch");
  // The pointer of the last press on a cell: a touch tap opens the editor (docs/17 §4.1, §5.7).
  const lastPointer = useRef("");
  const helpId = useId();
  const errorId = useId();
  const table = useRef<HTMLTableElement>(null);
  const cells = useRef(new Map<string, HTMLElement>());
  const [active, setActive] = useState<Position>({ row: 0, col: 0 });
  const [editing, setEditing] = useState<Editing | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const [query, setQuery] = useState("");
  const [searchColumn, setSearchColumn] = useState<string>(ALL);
  const hasCheck = Boolean(checkHeader);
  const offset = hasCheck ? 1 : 0;
  const colCount = columns.length + offset;

  const shown = useMemo(
    () => filterRows(rows, columns, query, searchColumn === ALL ? null : Number(searchColumn)),
    [rows, columns, query, searchColumn],
  );
  const rowCount = shown.length;
  const filtering = query.trim() !== "";

  // An open editor with a changed value is unsaved work: warn before the page is left.
  useUnsavedChangesWarning(Boolean(editing && editing.draft !== editing.original), t("leaveDraft"));

  // Keep the active cell inside the grid when the page, the rows or the filter change.
  const row = Math.min(active.row, Math.max(rowCount - 1, 0));
  const col = Math.min(active.col, Math.max(colCount - 1, 0));

  useEffect(() => {
    if (focusRequest === 0) return;
    const cell = cells.current.get(focusKey(row, col));
    cell?.focus();
    // Keep the focused cell fully visible inside the scroll region (not under the header).
    cell?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [focusRequest, row, col]);

  function moveTo(next: Position): boolean {
    const target = {
      row: Math.max(0, Math.min(rowCount - 1, next.row)),
      col: Math.max(0, Math.min(colCount - 1, next.col)),
    };
    if (target.row === row && target.col === col) return false;
    setActive(target);
    setFocusRequest((n) => n + 1);
    return true;
  }

  function columnAt(position: number): SheetGridColumn | undefined {
    return position < offset ? undefined : columns[position - offset];
  }

  function canEdit(r: number, c: number): boolean {
    const column = columnAt(c);
    const cell = column ? shown[r]?.cells[column.index] : undefined;
    return Boolean(
      editable && !busy && onCommit && column?.editable && !column.restricted && !cell?.restricted,
    );
  }

  function startEditing(r: number, c: number) {
    if (!canEdit(r, c)) return;
    const column = columnAt(c);
    const value = column ? (shown[r]?.cells[column.index]?.value ?? "") : "";
    setActive({ row: r, col: c });
    setEditing({ row: r, col: c, draft: value, original: value, problem: null });
  }

  function stopEditing() {
    setEditing(null);
    setFocusRequest((n) => n + 1);
  }

  /** Save the editor's value. `leave`: Tab was pressed, focus goes past the grid. */
  async function save(current: Editing, leave?: "forward" | "backward") {
    const column = columnAt(current.col);
    const cell = column ? shown[current.row]?.cells[column.index] : undefined;
    const target = shown[current.row];
    const problem = cellProblem(current.draft);
    if (problem) {
      setEditing({ ...current, problem });
      return;
    }
    const value = normaliseCellValue(current.draft);
    const unchanged = !column || !target || !onCommit || value === (cell?.value ?? null);
    setEditing(null);
    if (leave && table.current) focusOutside(table.current, leave === "forward");
    else if (unchanged) setFocusRequest((n) => n + 1);
    if (unchanged) return;
    try {
      await onCommit(target.rowNo, column.index, value);
    } finally {
      if (!leave) setFocusRequest((n) => n + 1);
    }
  }

  function onCellKeyDown(event: KeyboardEvent<HTMLElement>, r: number, c: number) {
    if (editing) return;
    const ctrl = event.ctrlKey || event.metaKey;
    const moves: Record<string, Position | undefined> = {
      ArrowUp: { row: r - 1, col: c },
      ArrowDown: { row: r + 1, col: c },
      ArrowLeft: { row: r, col: c - 1 },
      ArrowRight: { row: r, col: c + 1 },
      Home: ctrl ? { row: 0, col: 0 } : { row: r, col: 0 },
      End: ctrl ? { row: rowCount - 1, col: colCount - 1 } : { row: r, col: colCount - 1 },
    };
    const next = moves[event.key];
    if (next) {
      // At an edge nothing moves and the key is left to the browser (the region scrolls).
      if (moveTo(next)) event.preventDefault();
      return;
    }
    if ((event.key === "Enter" || event.key === "F2") && canEdit(r, c)) {
      event.preventDefault();
      startEditing(r, c);
    }
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!editing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      void save(editing);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation(); // do not close a surrounding dialog
      stopEditing();
    } else if (event.key === "Tab") {
      event.preventDefault();
      void save(editing, event.shiftKey ? "backward" : "forward");
    }
  }

  function register(r: number, c: number) {
    return (element: HTMLElement | null) => {
      const key = focusKey(r, c);
      if (element) cells.current.set(key, element);
      else cells.current.delete(key);
    };
  }

  function cellContent(cell: SheetGridCell | undefined, column: SheetGridColumn) {
    if (column.restricted || cell?.restricted) {
      return (
        <span className="inline-flex items-center gap-1 text-ink-muted">
          <Icon name="lock" className="size-3.5" />
          <span aria-hidden="true">{t("restricted")}</span>
          <span className="sr-only">{t("restrictedSr")}</span>
        </span>
      );
    }
    const value = cell?.value ?? null;
    return (
      <span className="inline-flex flex-wrap items-baseline gap-1.5">
        {value === null ? (
          <span className="text-ink-subtle">
            <span aria-hidden="true">—</span>
            <span className="sr-only">{t("empty")}</span>
          </span>
        ) : (
          <span className={cn("break-words whitespace-pre-wrap", cell?.formula && "font-mono")}>
            {value}
          </span>
        )}
        {cell?.formula ? (
          <span className="rounded bg-warning-soft px-1 text-xs text-warning-ink">
            {t("formula")}
          </span>
        ) : null}
        {cell?.pending ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-warning-ink">
            <Icon name="alert" className="size-3" />
            {t("pending")}
          </span>
        ) : cell?.edited ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-info-ink">
            <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
            {t("edited")}
          </span>
        ) : null}
      </span>
    );
  }

  const searchColumns = columns.filter((column) => !column.restricted);

  return (
    <div className="space-y-3">
      {searchable && rows.length > 0 ? (
        <div className="flex flex-wrap items-end gap-3" role="search" data-print="hide">
          <SearchInput
            label={t("searchLabel")}
            labelVisible
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
            wrapperClassName="w-full sm:max-w-xs"
            autoComplete="off"
          />
          <SelectField
            label={t("searchColumn")}
            value={searchColumn}
            onChange={(event) => setSearchColumn(event.currentTarget.value)}
            className="w-full sm:max-w-56"
            options={[
              { value: ALL, label: t("allColumns") },
              ...searchColumns.map((column) => ({
                value: String(column.index),
                label: `${column.letter} · ${column.header || t("noHeader")}`,
              })),
            ]}
          />
          <p aria-live="polite" aria-atomic="true" className="pb-2 text-sm text-ink-muted">
            {filtering ? t("searchCount", { shown: rowCount, total: rows.length }) : ""}
          </p>
        </div>
      ) : null}
      <p id={helpId} className="text-sm text-ink-muted">
        {editable ? t("keyboardHelpEdit") : t("keyboardHelpRead")}
        {editable ? <span className="hidden pointer-coarse:inline"> {tt("sheetHelp")}</span> : null}
      </p>
      <TableScroll
        label={t("scrollLabel", { caption })}
        framed
        className="max-h-[70dvh] overflow-y-auto scroll-pt-20 print:max-h-none"
      >
        <Table
          ref={table}
          role="grid"
          density="compact"
          stickyFirstColumn
          aria-label={caption}
          aria-describedby={helpId}
          aria-rowcount={rowCount + 1}
          aria-colcount={colCount + 1}
          aria-readonly={editable ? undefined : true}
        >
          <THead>
            <Tr role="row" aria-rowindex={1}>
              <Th
                role="columnheader"
                aria-colindex={1}
                className="sticky top-0 z-[2] w-14 bg-surface-muted"
              >
                {t("rowNumber")}
              </Th>
              {hasCheck ? (
                <Th
                  role="columnheader"
                  aria-colindex={2}
                  className="sticky top-0 z-[2] min-w-40 bg-surface-muted"
                >
                  {checkHeader}
                </Th>
              ) : null}
              {columns.map((column, i) => (
                <Th
                  key={column.index}
                  role="columnheader"
                  aria-colindex={i + offset + 2}
                  className="sticky top-0 z-[2] min-w-36 bg-surface-muted align-bottom whitespace-normal"
                >
                  <span className="flex flex-col gap-0.5">
                    <span className="flex items-baseline gap-1.5">
                      <span className="font-mono text-ink-subtle">{column.letter}</span>
                      <span className="font-semibold text-ink">
                        {column.header || t("noHeader")}
                      </span>
                    </span>
                    {column.detail ? (
                      <span className="font-normal text-ink-muted">{column.detail}</span>
                    ) : null}
                    {column.restricted ? (
                      <span className="inline-flex items-center gap-1 font-normal text-ink-muted">
                        <Icon name="lock" className="size-3" />
                        {t("restrictedColumn")}
                      </span>
                    ) : null}
                  </span>
                </Th>
              ))}
            </Tr>
          </THead>
          <TBody>
            {shown.length === 0 && filtering ? (
              <Tr role="row" aria-rowindex={2}>
                <Td role="gridcell" aria-colindex={1} colSpan={colCount + 1}>
                  {t("noMatch")}
                </Td>
              </Tr>
            ) : null}
            {shown.map((line, r) => (
              <Tr key={line.rowNo} role="row" aria-rowindex={r + 2}>
                <Th
                  scope="row"
                  role="rowheader"
                  aria-colindex={1}
                  className={cn("font-mono text-xs", line.invalid && "text-danger")}
                >
                  {line.rowNo}
                </Th>
                {hasCheck ? (
                  <Td
                    ref={register(r, 0)}
                    role="gridcell"
                    aria-colindex={2}
                    aria-readonly
                    tabIndex={row === r && col === 0 ? 0 : -1}
                    onFocus={() => setActive({ row: r, col: 0 })}
                    onKeyDown={(event) => onCellKeyDown(event, r, 0)}
                    className="focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus"
                  >
                    {line.check}
                  </Td>
                ) : null}
                {columns.map((column, i) => {
                  const c = i + offset;
                  const cell = line.cells[column.index];
                  const isEditing = editing?.row === r && editing.col === c;
                  const label = t("editLabel", {
                    column: column.header || column.letter,
                    row: line.rowNo,
                  });
                  return (
                    <Td
                      key={column.index}
                      ref={register(r, c)}
                      role="gridcell"
                      aria-colindex={c + 2}
                      aria-readonly={canEdit(r, c) ? undefined : true}
                      tabIndex={!isEditing && row === r && col === c ? 0 : -1}
                      onFocus={(event) => {
                        if (event.target === event.currentTarget) setActive({ row: r, col: c });
                      }}
                      onKeyDown={(event) => {
                        if (event.target === event.currentTarget) onCellKeyDown(event, r, c);
                      }}
                      onDoubleClick={() => startEditing(r, c)}
                      onPointerDown={(event) => {
                        lastPointer.current = event.pointerType;
                      }}
                      onClick={() => {
                        // Touch has no double click: one tap (never a scroll, which fires no
                        // click) opens the editor. Mouse and keyboard keep their behaviour.
                        const touch =
                          lastPointer.current === "touch" || lastPointer.current === "pen";
                        lastPointer.current = "";
                        if (touch && !isEditing) startEditing(r, c);
                      }}
                      className={cn(
                        "max-w-72 min-w-36 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus",
                        cell?.pending
                          ? "bg-warning-soft"
                          : cell?.edited
                            ? "bg-info-soft"
                            : undefined,
                      )}
                    >
                      {isEditing && editing ? (
                        <span className="flex flex-col gap-1">
                          <input
                            // Focus moves into the input when editing starts.
                            // eslint-disable-next-line jsx-a11y/no-autofocus
                            autoFocus
                            type="text"
                            value={editing.draft}
                            maxLength={MAX_CELL_CHARS + 1}
                            aria-label={label}
                            aria-invalid={editing.problem ? true : undefined}
                            aria-describedby={editing.problem ? errorId : undefined}
                            onChange={(event) =>
                              setEditing({ ...editing, draft: event.target.value, problem: null })
                            }
                            onKeyDown={onInputKeyDown}
                            enterKeyHint="done"
                            // 16px on touch screens, so iOS does not zoom the page on focus.
                            className="w-full min-w-32 rounded-md border border-border-control bg-surface px-2 py-1 text-sm text-ink pointer-coarse:min-h-11 pointer-coarse:text-base"
                          />
                          {editing.problem ? (
                            <span id={errorId} className="text-xs text-danger">
                              {t(`problem.${editing.problem}`)}
                            </span>
                          ) : (
                            <span className="text-xs text-ink-muted pointer-coarse:hidden">
                              {t("editHint")}
                            </span>
                          )}
                          {/* No Enter or Escape key on most phones' screens: buttons instead,
                              on touch screens only (keyboard use stays exactly as it was). */}
                          <span className="hidden gap-2 pointer-coarse:flex">
                            <button
                              type="button"
                              onClick={() => void save(editing)}
                              className="inline-flex min-h-11 items-center rounded-md border border-action bg-action px-3 text-sm font-semibold text-on-action"
                            >
                              {tc("save")}
                            </button>
                            <button
                              type="button"
                              onClick={stopEditing}
                              className="inline-flex min-h-11 items-center rounded-md border border-border-soft bg-surface px-3 text-sm font-semibold text-ink"
                            >
                              {tc("cancel")}
                            </button>
                          </span>
                        </span>
                      ) : (
                        cellContent(cell, column)
                      )}
                    </Td>
                  );
                })}
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    </div>
  );
}

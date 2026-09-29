"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { cn } from "@/lib/cn";

/**
 * Spreadsheet grid for the sheet editor (US-401 AC5, US-701 AC5; FR-IMP-008, FR-DOC-009/010).
 *
 * WAI-ARIA grid pattern with one tab stop: Tab enters the grid on the last focused cell, the
 * arrow keys move between cells, Home/End go to the first/last cell of the row and
 * Ctrl+Home/Ctrl+End to the first/last cell of the grid. Enter (or F2, or a double click) on
 * a cell that may be changed opens an input with its value: Enter saves, Escape cancels and
 * puts focus back on the cell. Values are single-line text of at most 1,000 characters; a
 * full Aadhaar number is refused here before anything is sent (the API refuses it too).
 * Restricted (C3) cells never carry a value and cannot be edited; formula cells are shown as
 * inert text. The grid scrolls sideways inside its own region, with the row number column
 * kept in view on small screens.
 */

export const MAX_CELL_CHARS = 1000;
const CONTROL = /[\u0000-\u001f\u007f]/;

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
  edited?: boolean | undefined;
  restricted?: boolean | undefined;
  formula?: boolean | undefined;
}

export interface SheetGridRow {
  rowNo: number;
  cells: readonly SheetGridCell[];
  /** Check result shown in the first column (e.g. "Error" with the problems). */
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
  /**
   * Called with the new value (`null` clears the cell) when the user saves a changed cell.
   * Resolves once the parent has handled it; the grid then puts focus back on the cell.
   */
  onCommit?: (rowNo: number, column: number, value: string | null) => Promise<void> | void;
}

interface Position {
  row: number;
  col: number;
}

interface Editing extends Position {
  draft: string;
  problem: CellProblem | null;
}

/** Position of a cell in the navigation order: the check column (if any) comes first. */
function focusKey(row: number, col: number): string {
  return `${row}:${col}`;
}

export function SheetGrid({
  caption,
  columns,
  rows,
  checkHeader,
  editable,
  busy = false,
  onCommit,
}: SheetGridProps) {
  const t = useTranslations("sheets.grid");
  const helpId = useId();
  const errorId = useId();
  const cells = useRef(new Map<string, HTMLElement>());
  const [active, setActive] = useState<Position>({ row: 0, col: 0 });
  const [editing, setEditing] = useState<Editing | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const hasCheck = Boolean(checkHeader);
  const offset = hasCheck ? 1 : 0;
  const colCount = columns.length + offset;
  const rowCount = rows.length;

  // Keep the active cell inside the grid when the page or the rows change.
  const row = Math.min(active.row, Math.max(rowCount - 1, 0));
  const col = Math.min(active.col, Math.max(colCount - 1, 0));

  useEffect(() => {
    if (focusRequest === 0) return;
    cells.current.get(focusKey(row, col))?.focus();
  }, [focusRequest, row, col]);

  function moveTo(next: Position) {
    setActive({
      row: Math.max(0, Math.min(rowCount - 1, next.row)),
      col: Math.max(0, Math.min(colCount - 1, next.col)),
    });
    setFocusRequest((n) => n + 1);
  }

  function columnAt(position: number): SheetGridColumn | undefined {
    return position < offset ? undefined : columns[position - offset];
  }

  function canEdit(r: number, c: number): boolean {
    const column = columnAt(c);
    const cell = column ? rows[r]?.cells[column.index] : undefined;
    return Boolean(
      editable && !busy && onCommit && column?.editable && !column.restricted && !cell?.restricted,
    );
  }

  function startEditing(r: number, c: number) {
    if (!canEdit(r, c)) return;
    const column = columnAt(c);
    const value = column ? (rows[r]?.cells[column.index]?.value ?? "") : "";
    setActive({ row: r, col: c });
    setEditing({ row: r, col: c, draft: value, problem: null });
  }

  function stopEditing() {
    setEditing(null);
    setFocusRequest((n) => n + 1);
  }

  async function save(current: Editing) {
    const column = columnAt(current.col);
    const cell = column ? rows[current.row]?.cells[column.index] : undefined;
    const target = rows[current.row];
    if (!column || !target || !onCommit) return stopEditing();
    const problem = cellProblem(current.draft);
    if (problem) {
      setEditing({ ...current, problem });
      return;
    }
    const value = normaliseCellValue(current.draft);
    if (value === (cell?.value ?? null)) return stopEditing();
    setEditing(null);
    try {
      await onCommit(target.rowNo, column.index, value);
    } finally {
      setFocusRequest((n) => n + 1);
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
      event.preventDefault();
      moveTo(next);
      return;
    }
    if (event.key === "Enter" || event.key === "F2") {
      if (canEdit(r, c)) {
        event.preventDefault();
        startEditing(r, c);
      }
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
        {cell?.edited ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-info-ink">
            <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
            {t("edited")}
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <div className="space-y-2">
      <p id={helpId} className="text-sm text-ink-muted">
        {editable ? t("keyboardHelpEdit") : t("keyboardHelpRead")}
      </p>
      <TableScroll label={t("scrollLabel", { caption })} framed>
        <Table
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
              <Th role="columnheader" aria-colindex={1} className="w-14">
                {t("rowNumber")}
              </Th>
              {hasCheck ? (
                <Th role="columnheader" aria-colindex={2} className="min-w-40">
                  {checkHeader}
                </Th>
              ) : null}
              {columns.map((column, i) => (
                <Th
                  key={column.index}
                  role="columnheader"
                  aria-colindex={i + offset + 2}
                  className="min-w-36 align-bottom whitespace-normal"
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
            {rows.map((line, r) => (
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
                      className={cn(
                        "max-w-72 min-w-36 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus",
                        cell?.edited && "bg-info-soft",
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
                            className="w-full min-w-32 rounded-md border border-border-control bg-surface px-2 py-1 text-sm text-ink"
                          />
                          {editing.problem ? (
                            <span id={errorId} className="text-xs text-danger">
                              {t(`problem.${editing.problem}`)}
                            </span>
                          ) : (
                            <span className="text-xs text-ink-muted">{t("editHint")}</span>
                          )}
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

/**
 * Canned API for the sheet editor e2e (e2e/sheets.spec.ts; US-401 AC5/AC6, US-701 AC5;
 * FR-IMP-008/009, FR-DOC-009..011): an import being checked whose file opens as a sheet, and a
 * single-sheet XLSX document. Stateful as far as the spec needs: cell edits with If-Match (412
 * on a stale ETag), the row re-checked, a new document version, and downloads that carry the
 * edits (CSV with a UTF-8 BOM, formula-like text neutralised). `resetSheets()` (POST /__e2e/reset)
 * starts again.
 *
 * Synthetic data only: "Synthetica …" names, SYN-… admission numbers, no Aadhaar number of any
 * kind. Shapes follow apps/api/openapi.json through the generated types.
 */
import type { components } from "@schoolos/api-client";

type Schemas = components["schemas"];

const CLERK = "0192f3a4-0000-7000-8000-0000000000d1";
const MEMBER = "0192f3a4-0000-7000-8000-0000000000e1";

export const SHEET_IDS = {
  import: "0192f3a4-0000-7000-8000-0000000f5001",
  importDoc: "0192f3a4-0000-7000-8000-00000000d501",
  document: "0192f3a4-0000-7000-8000-00000000d502",
  documentVersion: "0192f3a4-0000-7000-8000-00000000d512",
} as const;

const NOW = "2026-10-01T05:30:00Z";

/** Answer of the canned API: JSON (problem+json from 400) or a file download. */
export type SheetAnswer =
  | { kind: "json"; status: number; body: unknown; etag?: string }
  | { kind: "file"; name: string; type: string; content: Buffer };

/* ------------------------------------------------------------------ import */

const IMPORT_HEADERS: Array<[string, string | null]> = [
  ["Admission No", "admission_no"],
  ["Student Name", "full_name"],
  ["Class", "class_section"],
  ["Remarks", null],
];

/** The uploaded file (row 1 is the header); never changed: edits are kept beside it. */
const IMPORT_FILE: Array<Array<string | null>> = [
  ["SYN-2026-201", "Synthetica Ravi Teja", "7A", null],
  [null, "Synthetica Lakshmi Prasanna", "7A", "=1+1"],
  ["SYN-2026-203", "Synthetica Arjun Rao", "7B", "New admission"],
];

interface SheetState {
  version: number;
  edits: Map<string, string | null>;
  docVersion: number;
  docVersionNo: number;
  savedVersions: number;
}

let state: SheetState = fresh();

function fresh(): SheetState {
  return { version: 3, edits: new Map(), docVersion: 5, docVersionNo: 1, savedVersions: 0 };
}

export function resetSheets(): void {
  state = fresh();
}

const key = (row: number, column: number) => `${row}:${column}`;

function importValue(rowNo: number, column: number): string | null {
  const k = key(rowNo, column);
  if (state.edits.has(k)) return state.edits.get(k) ?? null;
  return IMPORT_FILE[rowNo - 2]?.[column] ?? null;
}

function importRow(rowNo: number): Schemas["SheetRowOut"] {
  const cells = IMPORT_HEADERS.map((_, column) => {
    const value = importValue(rowNo, column);
    return {
      value,
      edited: state.edits.has(key(rowNo, column)),
      restricted: false,
      formula: typeof value === "string" && value.startsWith("="),
    };
  });
  const missing = cells[0]?.value === null;
  return {
    row_no: rowNo,
    cells,
    status: missing ? "error" : "valid",
    errors: missing
      ? [{ field: "admission_no", code: "missing", message_key: "errors.missing", ref: null }]
      : [],
    warnings: [],
  };
}

function errorCount(): number {
  return IMPORT_FILE.filter((_, i) => importRow(i + 2).status === "error").length;
}

function batch(): Schemas["ImportOut"] {
  return {
    id: SHEET_IDS.import,
    kind: "spreadsheet",
    source: "admission_register",
    status: "validated",
    document_id: SHEET_IDS.importDoc,
    file_kind: "csv",
    header_row: 1,
    columns: IMPORT_HEADERS.map(([header, target], index) => ({
      index,
      header,
      suggested: target,
      score: target ? 95 : 0,
      target,
    })),
    mapping_template_id: null,
    stats: { rows: IMPORT_FILE.length },
    row_count: IMPORT_FILE.length,
    error_count: errorCount(),
    error_code: null,
    job_id: null,
    created_by: MEMBER,
    created_at: NOW,
    updated_at: NOW,
    committed_at: null,
    revert_deadline: null,
    reverted_at: null,
    raw_file_deleted_at: null,
    version: state.version,
    can_commit: errorCount() === 0,
    can_revert: false,
  };
}

function importRows(): Schemas["ImportRowOut"][] {
  return IMPORT_FILE.map((_, i) => {
    const sheetRow = importRow(i + 2);
    return {
      row_no: sheetRow.row_no,
      status: sheetRow.status ?? "error",
      action: "create",
      student_id: null,
      admission_no: sheetRow.cells[0]?.value ?? null,
      values: { full_name: sheetRow.cells[1]?.value ?? "" },
      sensitive: [],
      section_id: null,
      class_section: sheetRow.cells[2]?.value ?? null,
      roll_no: null,
      errors: sheetRow.errors,
      warnings: sheetRow.warnings,
    };
  });
}

function importSheet(): Schemas["ImportSheetOut"] {
  return {
    import_id: SHEET_IDS.import,
    status: "validated",
    version: state.version,
    editable: true,
    read_only_reason: null,
    header_row: 1,
    total_rows: IMPORT_FILE.length,
    offset: 0,
    edited_cells: state.edits.size,
    columns: IMPORT_HEADERS.map(([header, target], index) => ({
      index,
      letter: String.fromCharCode(65 + index),
      header,
      target,
      restricted: false,
      editable: true,
    })),
    data: IMPORT_FILE.map((_, i) => importRow(i + 2)),
    next_cursor: null,
  };
}

/* ------------------------------------------------------------------ document */

const DOC_HEADER = ["Receipt", "Payer", "Amount"];
const DOC_ROWS: string[][] = [
  ["R-001", "Synthetica Parent One", "1200"],
  ["R-002", "Synthetica Parent Two", "950"],
];

function docVersion(versionNo: number, status: "ready" | "queued") {
  return {
    id: SHEET_IDS.documentVersion,
    version_no: versionNo,
    mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    size_bytes: 6144,
    status,
    error: null,
    created_at: NOW,
    uploaded_by_me: true,
  };
}

function documentDetail(): Schemas["DocumentDetail"] {
  const versions = [docVersion(1, "ready")];
  if (state.savedVersions > 0) versions.unshift(docVersion(2, "queued"));
  return {
    id: SHEET_IDS.document,
    purpose: "circular",
    doc_type: "circular",
    title: "Fee receipts September (synthetic)",
    issuer: null,
    issued_on: null,
    academic_year_id: null,
    language: null,
    sensitivity: "C1",
    status: "active",
    current_version: versions[0] ?? null,
    acl: [],
    created_by: CLERK,
    created_at: NOW,
    updated_at: NOW,
    uploaded_by_me: true,
    uploaded_by: null,
    allowed_doc_types: ["circular", "other"],
    version: state.docVersion,
    versions,
  };
}

function documentSheet(limit: number): Schemas["DocumentSheetOut"] {
  return {
    document_id: SHEET_IDS.document,
    version_no: state.docVersionNo,
    version: state.docVersion,
    kind: "xlsx",
    sheet_count: 1,
    editable: state.savedVersions === 0,
    read_only_reason: state.savedVersions === 0 ? null : "newer_version",
    total_rows: DOC_ROWS.length,
    offset: 0,
    columns: DOC_HEADER.map((header, index) => ({
      index,
      letter: String.fromCharCode(65 + index),
      header,
    })),
    data: DOC_ROWS.slice(0, limit).map((cells, i) => ({
      row_no: i + 2,
      cells: cells.map((value) => ({ value, formula: false })),
    })),
    next_cursor: null,
  };
}

/* ------------------------------------------------------------------ files */

/** OWASP CSV injection: text that would start a formula gets a leading apostrophe. */
function neutralise(value: string | null): string {
  if (value === null) return "";
  const text = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function csv(header: string[], rows: Array<Array<string | null>>): Buffer {
  const lines = [header, ...rows].map((cells) => cells.map(neutralise).join(","));
  return Buffer.from(`﻿${lines.join("\r\n")}\r\n`, "utf8");
}

const problem = (status: number, code: string, title: string): SheetAnswer => ({
  kind: "json",
  status,
  body: { type: "about:blank", title, status, code },
});

/* ------------------------------------------------------------------ routes */

export function sheetAnswer(
  method: string,
  url: URL,
  body: Record<string, unknown>,
  ifMatch: string | undefined,
): SheetAnswer | undefined {
  const path = url.pathname;
  const imp = `/api/v1/imports/${SHEET_IDS.import}`;
  const doc = `/api/v1/documents/${SHEET_IDS.document}`;

  if (path === imp && method === "GET") return { kind: "json", status: 200, body: batch() };
  if (path === `${imp}/rows` && method === "GET") {
    const rows = importRows().filter(
      (row) => url.searchParams.get("status") !== "error" || row.status === "error",
    );
    return { kind: "json", status: 200, body: { data: rows, next_cursor: null } };
  }
  if (path === `${imp}/sheet` && method === "GET") {
    return { kind: "json", status: 200, body: importSheet(), etag: `W/"${state.version}"` };
  }
  const edit = new RegExp(`^${imp}/sheet/rows/(\\d+)$`).exec(path);
  if (edit && method === "PATCH") {
    if (ifMatch !== `W/"${state.version}"`) {
      return problem(412, "precondition_failed", "The import changed meanwhile.");
    }
    const rowNo = Number(edit[1]);
    if (rowNo < 2 || rowNo > IMPORT_FILE.length + 1) return problem(404, "not_found", "Not found");
    const cells = Array.isArray(body.cells) ? (body.cells as Array<Record<string, unknown>>) : [];
    for (const cell of cells) {
      const value = typeof cell.value === "string" && cell.value.trim() ? cell.value : null;
      state.edits.set(key(rowNo, Number(cell.column)), value);
    }
    state.version += 1;
    const out: Schemas["SheetEditOut"] = {
      row: importRow(rowNo),
      version: state.version,
      status: "validated",
      row_count: IMPORT_FILE.length,
      error_count: errorCount(),
      changed_rows: [],
    };
    return { kind: "json", status: 200, body: out, etag: `W/"${state.version}"` };
  }
  if (path === `${imp}/sheet/export` && method === "GET") {
    const rows = IMPORT_FILE.map((cells, i) =>
      cells.map((_, column) => importValue(i + 2, column)),
    );
    return {
      kind: "file",
      name: "import-0192f3a4-sheet.csv",
      type: "text/csv; charset=utf-8",
      content: csv(
        IMPORT_HEADERS.map(([header]) => header),
        rows,
      ),
    };
  }

  if (path === doc && method === "GET") {
    return { kind: "json", status: 200, body: documentDetail(), etag: `W/"${state.docVersion}"` };
  }
  if (path === `${doc}/sheet` && method === "GET") {
    const limit = Number(url.searchParams.get("limit") ?? 100);
    return {
      kind: "json",
      status: 200,
      body: documentSheet(limit),
      etag: `W/"${state.docVersion}"`,
    };
  }
  const docEdits = () => {
    const edits = Array.isArray(body.edits) ? (body.edits as Array<Record<string, unknown>>) : [];
    const rows: Array<Array<string | null>> = DOC_ROWS.map((cells) => [...cells]);
    for (const item of edits) {
      const target = rows[Number(item.row_no) - 2];
      if (target) target[Number(item.column)] = typeof item.value === "string" ? item.value : null;
    }
    return { count: edits.length, rows };
  };
  if (path === `${doc}/sheet/versions` && method === "POST") {
    if (ifMatch !== `W/"${state.docVersion}"`) {
      return problem(412, "precondition_failed", "The document changed meanwhile.");
    }
    if (docEdits().count === 0) return problem(409, "version_unchanged", "Nothing changed.");
    state.savedVersions += 1;
    state.docVersion += 1;
    const out: Partial<Schemas["DocumentDetail"]> = documentDetail();
    delete out.versions;
    delete out.allowed_doc_types;
    return { kind: "json", status: 202, body: out };
  }
  if (path === `${doc}/sheet/export` && method === "POST") {
    return {
      kind: "file",
      name: "fee-receipts-sheet.csv",
      type: "text/csv; charset=utf-8",
      content: csv(DOC_HEADER, docEdits().rows),
    };
  }
  return undefined;
}

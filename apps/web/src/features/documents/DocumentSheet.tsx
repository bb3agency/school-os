"use client";

import type { components } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { downloadSheet, type SheetFormat } from "@/features/sheets/download";
import { sheetProblem } from "@/features/sheets/problems";
import { SheetGrid, type SheetGridColumn, type SheetGridRow } from "@/features/sheets/SheetGrid";
import { Pager, useCursorStack } from "@/features/students/paging";
import { LoadGate } from "@/features/students/parts";
import { ProblemAlert } from "@/features/students/ProblemAlert";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { DOCUMENT_KEYS } from "./data";
import { ifMatch } from "./types";

type Schemas = components["schemas"];
export type DocumentSheet = Schemas["DocumentSheetOut"];
type Edit = Schemas["SheetCellEdit"];

const PAGE_SIZE = 50;

export const documentSheetKey = (documentId: string, cursor: string | undefined) =>
  [...DOCUMENT_KEYS.one(documentId), "sheet", cursor ?? null] as const;

const cellKey = (rowNo: number, column: number) => `${rowNo}:${column}`;

/** Unsaved edits as the API takes them (row order, then column). */
export function editsBody(edits: ReadonlyMap<string, string | null>): Edit[] {
  return [...edits.entries()]
    .map(([key, value]) => {
      const [row, column] = key.split(":").map(Number) as [number, number];
      return { row_no: row, column, value };
    })
    .sort((a, b) => a.row_no - b.row_no || a.column - b.column);
}

/**
 * US-701 AC5, FR-DOC-009..011: an XLSX or CSV document as a table. Staff who may upload
 * correct cells here (kept on this page until saved; Enter keeps a change) and save them as
 * the next version of a single-sheet workbook without formulas; anyone who may open the file
 * downloads the sheet, with the unsaved changes, as CSV or XLSX. Personal and restricted
 * documents ask "Confirm it's you" before a download.
 */
export function DocumentSheetScreen({ documentId }: { documentId: string }) {
  const t = useTranslations("sheets.document");
  const td = useTranslations("documents");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const pages = useCursorStack();
  const key = documentSheetKey(documentId, pages.cursor);
  const query = { limit: PAGE_SIZE, ...(pages.cursor ? { cursor: pages.cursor } : {}) };
  const sheet = useApiQuery(key, () =>
    unwrap(
      api.GET("/api/v1/documents/{document_id}/sheet", {
        params: { path: { document_id: documentId }, query },
      }),
    ),
  );
  const [edits, setEdits] = useState<Map<string, string | null>>(() => new Map());
  const [status, setStatus] = useState("");
  const [failure, setFailure] = useState<unknown>(undefined);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState<SheetFormat | null>(null);

  // Unsaved changes live only on this page: warn before the tab is closed or reloaded.
  useEffect(() => {
    if (edits.size === 0) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [edits.size]);

  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: td("title"), href: "/documents" },
    { label: t("backCrumb"), href: `/documents/${documentId}` },
  ];

  if (sheet.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} breadcrumb={[...crumbs, { label: t("title") }]} />
        {sheet.status === "error" && sheet.reason === undefined ? (
          <Alert tone="warning" title={t("cannotOpenTitle")}>
            {t("cannotOpenBody")}
          </Alert>
        ) : (
          <LoadGate state={sheet} />
        )}
        <ButtonLink href={`/documents/${documentId}`} variant="secondary">
          {t("back")}
        </ButtonLink>
      </div>
    );
  }

  const data = sheet.data;
  const canEdit = data.editable;

  const columns: SheetGridColumn[] = data.columns.map((column) => ({
    index: column.index,
    letter: column.letter,
    header: column.header,
    restricted: false,
    editable: canEdit,
  }));

  const rows: SheetGridRow[] = data.data.map((row) => ({
    rowNo: row.row_no,
    cells: row.cells.map((cell, index) => {
      const key = cellKey(row.row_no, index);
      return edits.has(key)
        ? { value: edits.get(key) ?? null, edited: true, formula: false }
        : { value: cell.value, formula: cell.formula };
    }),
  }));

  function original(rowNo: number, column: number): string | null {
    const row = data.data.find((item) => item.row_no === rowNo);
    return row?.cells[column]?.value ?? null;
  }

  function keep(rowNo: number, column: number, value: string | null) {
    setFailure(undefined);
    setEdits((current) => {
      const next = new Map(current);
      const key = cellKey(rowNo, column);
      if (value === original(rowNo, column)) next.delete(key);
      else next.set(key, value);
      return next;
    });
    setStatus(t("kept", { row: rowNo }));
  }

  async function save() {
    setFailure(undefined);
    setSaving(true);
    setStatus(t("saving"));
    try {
      await unwrap(
        api.POST("/api/v1/documents/{document_id}/sheet/versions", {
          params: { path: { document_id: documentId } },
          headers: { "If-Match": ifMatch(data.version), "Idempotency-Key": newIdempotencyKey() },
          body: { base_version_no: data.version_no, edits: editsBody(edits) },
        }),
      );
      setEdits(new Map());
      setStatus(t("saved", { version: data.version_no + 1 }));
      await queryClient.invalidateQueries({ queryKey: DOCUMENT_KEYS.all });
    } catch (error) {
      setStatus("");
      setFailure(sheetProblem(error));
    } finally {
      setSaving(false);
    }
  }

  async function download(format: SheetFormat) {
    setFailure(undefined);
    setDownloading(format);
    try {
      const name = await downloadSheet({
        path: `/api/v1/documents/${documentId}/sheet/export`,
        method: "POST",
        body: { format, base_version_no: data.version_no, edits: editsBody(edits) },
        locale,
        fallbackName: `document-sheet.${format}`,
      });
      setStatus(t("downloaded", { name }));
    } catch (error) {
      setFailure(sheetProblem(error));
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        breadcrumb={[...crumbs, { label: t("title") }]}
        description={t("description", { version: data.version_no })}
        actions={
          <span className="flex flex-wrap gap-2" data-print="hide">
            {(["csv", "xlsx"] as const).map((format) => (
              <Button
                key={format}
                variant="secondary"
                onClick={() => void download(format)}
                disabled={downloading !== null}
              >
                <Icon name="arrowDown" className="size-4" />
                {downloading === format ? t("downloading") : t(`download.${format}`)}
              </Button>
            ))}
          </span>
        }
      />
      <p role="status" aria-live="polite" aria-atomic="true" className="text-sm font-medium">
        {status}
      </p>
      <ProblemAlert error={failure} namespace="sheets.errors" />
      {data.read_only_reason ? (
        <Alert tone="info" title={t("readOnlyTitle")}>
          {t(`readOnly.${data.read_only_reason}`)}
        </Alert>
      ) : null}
      {data.sheet_count > 1 ? (
        <Alert tone="info">{t("otherSheets", { count: data.sheet_count - 1 })}</Alert>
      ) : null}
      <Card
        title={t("gridTitle")}
        description={t("summary", { rows: data.total_rows })}
        actions={
          canEdit ? (
            <span className="flex flex-wrap items-center gap-2" data-print="hide">
              <span className="text-sm text-ink-muted">{t("pending", { count: edits.size })}</span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setEdits(new Map());
                  setStatus(t("discarded"));
                }}
                disabled={edits.size === 0 || saving}
              >
                {t("discard")}
              </Button>
              <Button size="sm" onClick={() => void save()} disabled={edits.size === 0 || saving}>
                {saving ? t("savingShort") : t("save")}
              </Button>
            </span>
          ) : null
        }
      >
        <div className="space-y-4">
          <SheetGrid
            caption={t("gridTitle")}
            columns={columns}
            rows={rows}
            editable={canEdit}
            busy={saving}
            onCommit={keep}
          />
          <Pager
            label={t("pagesLabel")}
            page={pages.page}
            onPrevious={pages.hasPrevious ? pages.previous : undefined}
            onNext={data.next_cursor ? () => pages.next(data.next_cursor as string) : undefined}
          />
          <p className="text-xs text-ink-muted">{t("note")}</p>
        </div>
      </Card>
      <ButtonLink href={`/documents/${documentId}`} variant="secondary">
        {t("back")}
      </ButtonLink>
    </div>
  );
}

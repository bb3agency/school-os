"use client";

import type { components } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge, Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { downloadSheet, type SheetFormat } from "@/features/sheets/download";
import { sheetProblem } from "@/features/sheets/problems";
import { SheetGrid, type SheetGridColumn, type SheetGridRow } from "@/features/sheets/SheetGrid";
import { PERM, useStaffPermissions } from "@/features/students/me";
import { Pager, useCursorStack } from "@/features/students/paging";
import { LoadGate, useAttributes } from "@/features/students/parts";
import { ProblemAlert } from "@/features/students/ProblemAlert";
import type { Locale } from "@/i18n/routing";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { IssueText, importKey, useTargetLabel } from "./ImportDetail";

type Schemas = components["schemas"];
export type ImportSheet = Schemas["ImportSheetOut"];
type SheetRow = Schemas["SheetRowOut"];

/** 100 rows a page, as the API pages the staged sheet by default. */
const PAGE_SIZE = 100;

const rowPill: Record<NonNullable<SheetRow["status"]>, PillVariant> = {
  valid: "positive",
  error: "negative",
  committed: "done",
  skipped: "tag",
  reverted: "tag",
};

export const importSheetKey = (importId: string, cursor: string | undefined) =>
  [...importKey(importId), "sheet", cursor ?? null] as const;

/** One saved edit or a problem, announced politely to screen readers (aria-live). */
type Status = { kind: "saving"; row: number } | { kind: "saved"; text: string } | null;

/** The cell being saved: shown with its new value, "Not saved yet", until the API answers. */
interface PendingCell {
  rowNo: number;
  column: number;
  value: string | null;
}

/**
 * US-401 AC5/AC6, FR-IMP-008/009: the uploaded file as a sheet, between checking and adding.
 * Every column with the field it fills, every row with its check result; cells can be
 * corrected in place before the rows are added (If-Match on each save, the row is checked
 * again at once), restricted columns stay hidden, and the sheet with its edits downloads as
 * CSV or XLSX after "Confirm it's you".
 */
export function ImportSheetScreen({ importId }: { importId: string }) {
  const t = useTranslations("sheets.import");
  const ti = useTranslations("imports.rows");
  const tl = useTranslations("imports.list");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const permissions = useStaffPermissions();
  const attributes = useAttributes();
  const label = useTargetLabel(attributes);
  const pages = useCursorStack();
  const key = importSheetKey(importId, pages.cursor);
  const query = { limit: PAGE_SIZE, ...(pages.cursor ? { cursor: pages.cursor } : {}) };
  const sheet = useApiQuery(key, () =>
    unwrap(
      api.GET("/api/v1/imports/{import_id}/sheet", {
        params: { path: { import_id: importId }, query },
      }),
    ),
  );
  const [status, setStatus] = useState<Status>(null);
  const [failure, setFailure] = useState<unknown>(undefined);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingCell | null>(null);
  const [downloading, setDownloading] = useState<SheetFormat | null>(null);

  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: tl("title"), href: "/imports" },
    { label: t("backCrumb"), href: `/imports/${importId}` },
  ];

  if (sheet.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} breadcrumb={[...crumbs, { label: t("title") }]} />
        <LoadGate state={sheet} />
        {sheet.status === "error" ? (
          <ButtonLink href={`/imports/${importId}`} variant="secondary">
            {t("back")}
          </ButtonLink>
        ) : null}
      </div>
    );
  }

  const data = sheet.data;
  const canEdit = data.editable && permissions.has(PERM.importRun);
  const restricted = data.columns.filter((column) => column.restricted).length;

  const columns: SheetGridColumn[] = data.columns.map((column) => ({
    index: column.index,
    letter: column.letter,
    header: column.header,
    detail: column.target ? t("fills", { field: label(column.target) }) : t("notImported"),
    restricted: column.restricted,
    editable: canEdit && column.editable,
  }));

  const rows: SheetGridRow[] = data.data.map((row) => ({
    rowNo: row.row_no,
    cells:
      pending && pending.rowNo === row.row_no
        ? row.cells.map((cell, index) =>
            index === pending.column ? { ...cell, value: pending.value, pending: true } : cell,
          )
        : row.cells,
    invalid: row.status === "error",
    check: (
      <span className="flex flex-col gap-1.5">
        {row.status === "valid" && row.warnings.length > 0 ? (
          <Badge tone="warning">{t("rowWarning")}</Badge>
        ) : row.status ? (
          <Pill variant={rowPill[row.status]}>{ti(`status.${row.status}`)}</Pill>
        ) : (
          <Badge tone="neutral">{t("notChecked")}</Badge>
        )}
        {row.errors.length + row.warnings.length > 0 ? (
          <ul className="space-y-1 text-xs">
            {row.errors.map((issue, i) => (
              <li key={`e${i}`} className="text-danger">
                <IssueText issue={issue} label={label} />
              </li>
            ))}
            {row.warnings.map((issue, i) => (
              <li key={`w${i}`} className="text-warning-ink">
                <IssueText issue={issue} label={label} />
              </li>
            ))}
          </ul>
        ) : null}
      </span>
    ),
  }));

  async function commit(rowNo: number, column: number, value: string | null) {
    setFailure(undefined);
    setStatus({ kind: "saving", row: rowNo });
    setPending({ rowNo, column, value });
    setBusy(true);
    try {
      const out = await unwrap(
        api.PATCH("/api/v1/imports/{import_id}/sheet/rows/{row_no}", {
          params: { path: { import_id: importId, row_no: rowNo } },
          headers: { "If-Match": `W/"${data.version}"` },
          body: { cells: [{ column, value }] },
        }),
      );
      queryClient.setQueryData<ImportSheet>(key, (old) =>
        old
          ? {
              ...old,
              version: out.version,
              status: out.status,
              data: old.data.map((row) => (row.row_no === rowNo ? out.row : row)),
            }
          : old,
      );
      const outcome =
        out.row.status === "valid"
          ? t("savedValid", { row: rowNo })
          : out.row.status === "error"
            ? t("savedErrors", { row: rowNo, count: out.row.errors.length })
            : t("savedUnchecked", { row: rowNo });
      const others =
        out.changed_rows.length > 0 ? ` ${t("otherRows", { count: out.changed_rows.length })}` : "";
      setStatus({ kind: "saved", text: outcome + others });
      // Counts on the import page, other rows' checks and the edited-cell count.
      await queryClient.invalidateQueries({ queryKey: importKey(importId) });
    } catch (error) {
      setStatus(null);
      setFailure(sheetProblem(error));
      if (error instanceof ApiError && (error.status === 412 || error.status === 409)) {
        await queryClient.invalidateQueries({ queryKey: importKey(importId) });
      }
    } finally {
      setPending(null);
      setBusy(false);
    }
  }

  async function download(format: SheetFormat) {
    setFailure(undefined);
    setDownloading(format);
    try {
      const name = await downloadSheet({
        path: `/api/v1/imports/${importId}/sheet/export?format=${format}`,
        method: "GET",
        locale,
        fallbackName: `import-sheet.${format}`,
      });
      setStatus({ kind: "saved", text: t("downloaded", { name }) });
    } catch (error) {
      setFailure(sheetProblem(error));
    } finally {
      setDownloading(null);
    }
  }

  const title = t("title");
  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        breadcrumb={[...crumbs, { label: title }]}
        description={canEdit ? t("descriptionEdit") : t("descriptionRead")}
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
        {/* "Saving…" is shown, not announced: only the outcome is read out. */}
        {status?.kind === "saving" ? (
          <span aria-hidden="true">{t("saving", { row: status.row })}</span>
        ) : (
          (status?.text ?? "")
        )}
      </p>
      <ProblemAlert error={failure} namespace={["sheets.errors", "imports.errors"]} />
      {data.read_only_reason ? (
        <Alert tone="info" title={t("readOnlyTitle")}>
          {t(`readOnly.${data.read_only_reason}`)}
        </Alert>
      ) : null}
      {restricted > 0 ? (
        <Alert tone="info">{t("restrictedNote", { count: restricted })}</Alert>
      ) : null}
      <Card
        title={t("gridTitle")}
        description={t("summary", {
          rows: data.total_rows,
          edited: data.edited_cells,
        })}
      >
        <div className="space-y-4">
          <SheetGrid
            caption={t("gridTitle")}
            columns={columns}
            rows={rows}
            checkHeader={t("checkColumn")}
            editable={canEdit}
            busy={busy}
            onCommit={commit}
          />
          <Pager
            label={t("pagesLabel")}
            page={pages.page}
            onPrevious={pages.hasPrevious ? pages.previous : undefined}
            onNext={data.next_cursor ? () => pages.next(data.next_cursor as string) : undefined}
          />
          <p className="text-xs text-ink-muted">{t("downloadNote")}</p>
        </div>
      </Card>
      <ButtonLink href={`/imports/${importId}`} variant="secondary">
        {t("back")}
      </ButtonLink>
    </div>
  );
}

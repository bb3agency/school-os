"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useRef, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { PERM, useStaffPermissions, type Permissions } from "@/features/students/me";
import { Pager, useCursorStack } from "@/features/students/paging";
import { ProblemAlert } from "@/features/students/ProblemAlert";
import { VALUE_SOURCES, isValueSource } from "@/features/students/types";
import { Link, useRouter } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { ApiError, newIdempotencyKey, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatBytes, formatCount, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  CREATING_SOURCES,
  importTone,
  type ImportBatch,
  type ImportSource,
  type ImportSummary,
  type ImportTemplate,
} from "./types";
import { extensionOf, uploadDocument, type UploadProgress } from "./upload";

export const IMPORTS_KEY = ["staff", "imports"] as const;
/** FR-IMP-001: XLSX or CSV (a Google Sheets download), at most 10 MB. */
export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
const ACCEPT = ".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv";

export function ImportStatusBadge({ status }: { status: ImportBatch["status"] }) {
  const t = useTranslations("imports.status");
  return <Badge tone={importTone[status]}>{t(status)}</Badge>;
}

export function SourceName({ source }: { source: string }) {
  const t = useTranslations("students.sources");
  return <>{isValueSource(source) ? t(source) : source}</>;
}

type FileProblem = "fileMissing" | "fileType" | "fileTooLarge";

export function checkSpreadsheet(file: File | null | undefined): FileProblem | null {
  if (!file) return "fileMissing";
  if (!["xlsx", "csv"].includes(extensionOf(file.name))) return "fileType";
  if (file.size > MAX_IMPORT_BYTES) return "fileTooLarge";
  return null;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** US-401 AC1: upload a spreadsheet and start reading it (documents upload → POST /imports). */
export function UploadSpreadsheet({ onStarted }: { onStarted: (batch: ImportBatch) => void }) {
  const t = useTranslations("imports.upload");
  const ts = useTranslations("students");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const fileRef = useRef<HTMLInputElement>(null);
  const statusId = useId();
  const [problem, setProblem] = useState<FileProblem | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const busy = progress !== null && progress.stage !== "ready";

  async function start(file: File, source: ImportSource) {
    const documentId = await uploadDocument(api, file, "import_file", { onProgress: setProgress });
    const key = newIdempotencyKey();
    // The scan result can take a moment to reach the import service: retry briefly.
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await unwrap(
          api.POST("/api/v1/imports", {
            headers: { "Idempotency-Key": key },
            body: { document_id: documentId, source, kind: "spreadsheet" },
          }),
        );
      } catch (failure) {
        if (!(failure instanceof ApiError && failure.code === "document_not_ready") || attempt >= 4) {
          throw failure;
        }
        await sleep(2000);
      }
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = fileRef.current?.files?.[0];
    const found = checkSpreadsheet(file);
    setProblem(found);
    setError(undefined);
    if (found || !file) {
      fileRef.current?.focus();
      return;
    }
    const source = new FormData(form).get("source");
    const chosen = VALUE_SOURCES.find((value) => value === source) ?? "admission_register";
    start(file, chosen)
      .then((batch) => {
        form.reset();
        setProgress(null);
        onStarted(batch);
      })
      .catch((failure: unknown) => {
        setProgress(null);
        setError(failure);
      });
  }

  const stageText = (value: UploadProgress) =>
    value.stage === "uploading" && value.percent !== undefined
      ? t("stage.uploadingPercent", { percent: value.percent })
      : t(`stage.${value.stage}`);

  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4" aria-describedby={statusId}>
      <Field
        label={t("file")}
        hint={t("fileHint", { size: formatBytes(MAX_IMPORT_BYTES, locale) ?? "10 MB" })}
        error={problem ? t(problem) : undefined}
      >
        {({ id, describedBy, invalid }) => (
          <input
            ref={fileRef}
            id={id}
            name="file"
            type="file"
            accept={ACCEPT}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={busy}
            onChange={() => setProblem(null)}
            className="block w-full rounded-md border border-border-strong bg-surface p-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary-soft file:px-3 file:py-1.5 file:font-semibold file:text-primary"
          />
        )}
      </Field>
      <SelectField
        name="source"
        label={t("source")}
        hint={t("sourceHint")}
        defaultValue="admission_register"
        disabled={busy}
        options={VALUE_SOURCES.map((value) => ({
          value,
          label: CREATING_SOURCES.includes(value)
            ? ts(`sources.${value}`)
            : t("sourceMatchOnly", { source: ts(`sources.${value}`) }),
        }))}
      />
      <Alert tone="warning" title={ts("aadhaarWarningTitle")}>
        {t("aadhaarColumn")}
      </Alert>
      <div id={statusId} role="status" aria-live="polite" className="min-h-6 space-y-2">
        {progress ? (
          <>
            <p className="text-sm font-semibold">{stageText(progress)}</p>
            {progress.stage === "uploading" ? (
              <progress
                max={100}
                value={progress.percent ?? 0}
                aria-label={t("progressLabel")}
                className="h-2 w-full"
              />
            ) : null}
          </>
        ) : null}
      </div>
      <ProblemAlert error={error} namespace="imports.errors" />
      <div className="flex justify-end">
        <Button type="submit" disabled={busy}>
          {busy ? t("working") : t("submit")}
        </Button>
      </div>
    </form>
  );
}

export interface ImportsViewProps {
  imports: Loadable<readonly ImportSummary[]>;
  templates: Loadable<readonly ImportTemplate[]>;
  permissions: Permissions;
  page: number;
  onNext?: (() => void) | undefined;
  onPrevious?: (() => void) | undefined;
  onStarted: (batch: ImportBatch) => void;
}

/** US-401 / FR-IMP-001..007: the school's spreadsheet imports and saved column templates. */
export function ImportsView({
  imports,
  templates,
  permissions,
  page,
  onNext,
  onPrevious,
  onStarted,
}: ImportsViewProps) {
  const t = useTranslations("imports.list");
  const locale = useLocale() as Locale;
  const count = (value: number) => formatCount(value, locale) ?? String(value);

  const columns: Column<ImportSummary>[] = [
    {
      key: "started",
      header: t("colStarted"),
      cell: (row) => (
        <Link href={`/imports/${row.id}`} className="font-semibold text-primary underline">
          {formatDateTime(row.created_at) ?? t("open")}
        </Link>
      ),
    },
    { key: "source", header: t("colSource"), cell: (row) => <SourceName source={row.source} /> },
    { key: "rows", header: t("colRows"), cell: (row) => count(row.row_count) },
    {
      key: "errors",
      header: t("colErrors"),
      cell: (row) =>
        row.error_count > 0 ? (
          <Badge tone="danger">{count(row.error_count)}</Badge>
        ) : (
          count(row.error_count)
        ),
    },
    { key: "status", header: t("colStatus"), cell: (row) => <ImportStatusBadge status={row.status} /> },
  ];

  const templateColumns: Column<ImportTemplate>[] = [
    { key: "name", header: t("templateName"), cell: (row) => row.name },
    { key: "source", header: t("colSource"), cell: (row) => <SourceName source={row.source} /> },
    {
      key: "columns",
      header: t("templateColumns"),
      cell: (row) => count(Object.keys(row.mapping).length),
    },
    {
      key: "used",
      header: t("templateLastUsed"),
      cell: (row) => <Value>{formatDateTime(row.last_used_at)}</Value>,
    },
  ];

  const canUpload = permissions.has(PERM.importRun) && permissions.has(PERM.documentUpload);

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <div className="grid gap-6 xl:grid-cols-[2fr_3fr]">
        {canUpload ? (
          <Card title={t("uploadTitle")} description={t("uploadDescription")}>
            <UploadSpreadsheet onStarted={onStarted} />
          </Card>
        ) : null}
        <Card title={t("historyTitle")} className={canUpload ? undefined : "xl:col-span-2"}>
          <div className="space-y-3">
            <DataTable
              caption={t("historyTitle")}
              captionHidden
              columns={columns}
              state={imports}
              rowKey={(row) => row.id}
              emptyTitle={t("emptyTitle")}
              emptyBody={t("emptyBody")}
            />
            <Pager label={t("pagesLabel")} page={page} onPrevious={onPrevious} onNext={onNext} />
          </div>
        </Card>
      </div>
      <Card title={t("templatesTitle")} description={t("templatesDescription")}>
        <DataTable
          caption={t("templatesTitle")}
          captionHidden
          columns={templateColumns}
          state={templates}
          rowKey={(row) => row.id}
          emptyTitle={t("templatesEmptyTitle")}
          emptyBody={t("templatesEmptyBody")}
        />
      </Card>
    </div>
  );
}

/** GET /imports (cursor), GET /import-templates. */
export function ImportsScreen() {
  const api = useBffClient("staff");
  const router = useRouter();
  const permissions = useStaffPermissions();
  const pages = useCursorStack();
  const query = { limit: 25, ...(pages.cursor ? { cursor: pages.cursor } : {}) };
  const list = useApiQuery([...IMPORTS_KEY, "list", query], () =>
    unwrap(api.GET("/api/v1/imports", { params: { query } })),
  );
  const templates = useApiQuery([...IMPORTS_KEY, "templates"], () =>
    unwrap(api.GET("/api/v1/import-templates")),
  );
  const imports: Loadable<readonly ImportSummary[]> =
    list.status === "ready" ? { status: "ready", data: list.data.data } : list;
  const next = list.status === "ready" ? list.data.next_cursor : null;
  return (
    <ImportsView
      imports={imports}
      templates={templates}
      permissions={permissions}
      page={pages.page}
      onNext={next ? () => pages.next(next) : undefined}
      onPrevious={pages.hasPrevious ? pages.previous : undefined}
      onStarted={(batch) => router.push(`/imports/${batch.id}`)}
    />
  );
}

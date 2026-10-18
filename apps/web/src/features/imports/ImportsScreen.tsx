"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useRef, useState, type FormEvent } from "react";
import * as z from "@/lib/zod";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { Icon } from "@/components/ui/Icon";
import { Field } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Steps } from "@/components/ui/Steps";
import { Value } from "@/components/ui/Value";
import { TENANT_KEY } from "@/features/settings/data";
import { PERM, useStaffPermissions, type Permissions } from "@/features/students/me";
import { Pager, useCursorStack } from "@/features/students/paging";
import { ProblemAlert } from "@/features/students/ProblemAlert";
import { VALUE_SOURCES, isValueSource } from "@/features/students/types";
import { Link, useRouter } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { ApiError, newIdempotencyKey, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formValues } from "@/lib/forms";
import { formatBytes, formatCount, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import {
  CREATING_SOURCES,
  type ImportBatch,
  type ImportSource,
  type ImportSummary,
  type ImportTemplate,
} from "./types";
import { FileDropZone, fileInputClasses } from "./parts";
import { ERP_PRESET, TemplateLibrary } from "./presets";
import { extensionOf, uploadDocument, type UploadProgress } from "./upload";

export const IMPORTS_KEY = ["staff", "imports"] as const;
/** FR-IMP-001: XLSX or CSV (a Google Sheets download), at most 10 MB. */
export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
/** The upload card's id: the header's upload link jumps to it (the form's submit is the primary). */
const UPLOAD_ANCHOR = "upload-spreadsheet";
const ACCEPT =
  ".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv";

/** Workflow pills: busy = in progress, waiting for you = review, added = done. */
const importPill: Record<ImportBatch["status"], PillVariant> = {
  uploaded: "progress",
  parsing: "progress",
  parsed: "review",
  validating: "progress",
  validated: "review",
  committing: "progress",
  committed: "done",
  reverting: "progress",
  reverted: "tag",
  failed: "negative",
};

export function ImportStatusBadge({ status }: { status: ImportBatch["status"] }) {
  const t = useTranslations("imports.status");
  return <Pill variant={importPill[status]}>{t(status)}</Pill>;
}

/** Upload → Map columns → Check rows → Add: the step an import has reached (4 = all done). */
export function importStep(status: ImportBatch["status"]): number {
  switch (status) {
    case "uploaded":
    case "parsing":
    case "failed":
      return 0;
    case "parsed":
      return 1;
    case "validating":
      return 2;
    case "validated":
    case "committing":
      return 3;
    default:
      return 4;
  }
}

const STEP_IDS = ["upload", "map", "check", "add"] as const;

/** The import's stages as `Steps`; the step it failed at is marked failed ("Stopped"). */
export function ImportSteps({ current, failed = false }: { current: number; failed?: boolean }) {
  const t = useTranslations("imports.steps");
  return (
    <Steps
      label={t("label")}
      current={current}
      steps={STEP_IDS.map((id, index) => ({
        id,
        label: t(id),
        ...(failed && index === current ? { failed: true } : {}),
      }))}
    />
  );
}

export function SourceName({ source }: { source: string }) {
  const t = useTranslations("students.sources");
  return <>{isValueSource(source) ? t(source) : source}</>;
}

export type FileProblem = "fileMissing" | "fileType" | "fileTooLarge";

export function checkSpreadsheet(file: File | null | undefined): FileProblem | null {
  if (!file) return "fileMissing";
  if (!["xlsx", "csv"].includes(extensionOf(file.name))) return "fileType";
  if (file.size > MAX_IMPORT_BYTES) return "fileTooLarge";
  return null;
}

const uploadSchema = z.object({
  source: z.enum(VALUE_SOURCES, { error: "chooseOption" }),
});

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Pause between "the scan is not visible to imports yet" retries (tests shorten it). */
let notReadyRetryMs = 2000;
export function setNotReadyRetryForTesting(ms: number): void {
  notReadyRetryMs = ms;
}

/** US-401 AC1: upload a spreadsheet and start reading it (documents upload → POST /imports). */
export function UploadSpreadsheet({
  onStarted,
  defaultSource = "admission_register",
  submitLabel,
}: {
  onStarted: (batch: ImportBatch) => void;
  /** The source pre-selected (the ERP refresh starts with office records, US-204). */
  defaultSource?: ImportSource;
  submitLabel?: string;
}) {
  const t = useTranslations("imports.upload");
  const ts = useTranslations("students");
  const tv = useTranslations("validation");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const fileRef = useRef<HTMLInputElement>(null);
  const statusId = useId();
  const fileId = `${statusId}-file`;
  const sourceId = `${statusId}-source`;
  const [problem, setProblem] = useState<FileProblem | null>(null);
  const [sourceError, setSourceError] = useState<string | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const busy = progress !== null;

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
        const notReady = failure instanceof ApiError && failure.code === "document_not_ready";
        if (!notReady || attempt >= 4) throw failure;
        await sleep(notReadyRetryMs);
      }
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const file = fileRef.current?.files?.[0];
    const found = checkSpreadsheet(file);
    const parsed = uploadSchema.safeParse(formValues(form));
    setProblem(found);
    setSourceError(parsed.success ? undefined : tv("chooseOption"));
    setError(undefined);
    if (found || !file || !parsed.success) {
      // The error summary takes focus and links to each field (docs/17 §4.4).
      setAttempt((value) => value + 1);
      return;
    }
    start(file, parsed.data.source)
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

  const summary: SummaryError[] = [
    ...(problem ? [{ fieldId: fileId, message: t(problem) }] : []),
    ...(sourceError ? [{ fieldId: sourceId, message: sourceError }] : []),
  ];

  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4">
      <ErrorSummary errors={summary} attempt={attempt} />
      <Field
        id={fileId}
        label={t("file")}
        hint={t("fileHint", { size: formatBytes(MAX_IMPORT_BYTES, locale) ?? "10 MB" })}
        error={problem ? t(problem) : undefined}
      >
        {({ id, describedBy, invalid }) => (
          <FileDropZone title={t("dropTitle")} disabled={busy}>
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
              className={fileInputClasses}
            />
          </FileDropZone>
        )}
      </Field>
      <SelectField
        id={sourceId}
        name="source"
        label={t("source")}
        hint={t("sourceHint")}
        defaultValue={defaultSource}
        disabled={busy}
        error={sourceError}
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
            <p className="text-sm">{stageText(progress)}</p>
            {progress.stage === "uploading" ? (
              <progress
                max={100}
                value={progress.percent ?? 0}
                aria-label={t("progressLabel")}
                className="h-2 w-full accent-primary"
              />
            ) : null}
          </>
        ) : null}
      </div>
      <ProblemAlert error={error} namespace="imports.errors" />
      <div className="flex justify-end">
        <Button type="submit" disabled={busy} aria-describedby={statusId}>
          {busy ? t("working") : (submitLabel ?? t("submit"))}
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
  /** FR-TEN-022: the school runs SchoolOS alongside its current ERP (US-204). */
  alongside?: { erpName: string } | null;
  /** US-204: an ERP refresh was started (opens the mapping with the ERP preset). */
  onRefreshStarted?: (batch: ImportBatch) => void;
}

/**
 * US-204 / FR-IMP-033: "Refresh from your ERP export". The file is imported as office records
 * (`manual_entry`) with the ERP preset; admission-register values are never replaced and every
 * difference becomes a DQ-030 finding.
 */
export function ErpRefreshCard({
  erpName,
  onStarted,
}: {
  erpName: string;
  onStarted: (batch: ImportBatch) => void;
}) {
  const t = useTranslations("imports.refresh");
  return (
    <Card
      title={erpName ? t("titleNamed", { name: erpName }) : t("title")}
      description={t("description")}
    >
      <div className="space-y-4">
        <Alert tone="info">{t("registerKept")}</Alert>
        <UploadSpreadsheet
          onStarted={onStarted}
          defaultSource="manual_entry"
          submitLabel={t("submit")}
        />
      </div>
    </Card>
  );
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
  alongside = null,
  onRefreshStarted,
}: ImportsViewProps) {
  const t = useTranslations("imports.list");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const historyId = useId();
  const templatesId = useId();
  const count = (value: number) => formatCount(value, locale) ?? String(value);

  const columns: ColumnV2<ImportSummary>[] = [
    {
      key: "started",
      header: t("colStarted"),
      cell: (row) => (
        <Link
          href={`/imports/${row.id}`}
          className="num font-semibold text-primary underline underline-offset-4"
        >
          {formatDateTime(row.created_at) ?? t("open")}
          <span className="sr-only">{t("openHint")}</span>
        </Link>
      ),
    },
    { key: "source", header: t("colSource"), cell: (row) => <SourceName source={row.source} /> },
    {
      key: "rows",
      header: t("colRows"),
      numeric: true,
      cell: (row) => count(row.row_count),
    },
    {
      key: "errors",
      header: t("colErrors"),
      numeric: true,
      cell: (row) =>
        row.error_count > 0 ? (
          <Pill variant="negative">{count(row.error_count)}</Pill>
        ) : (
          count(row.error_count)
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <ImportStatusBadge status={row.status} />,
    },
  ];

  const templateColumns: ColumnV2<ImportTemplate>[] = [
    { key: "name", header: t("templateName"), cell: (row) => row.name },
    { key: "source", header: t("colSource"), cell: (row) => <SourceName source={row.source} /> },
    {
      key: "columns",
      header: t("templateColumns"),
      numeric: true,
      cell: (row) => count(Object.keys(row.mapping).length),
    },
    {
      key: "used",
      header: t("templateLastUsed"),
      cell: (row) => (
        <span className="num">
          <Value>{formatDateTime(row.last_used_at)}</Value>
        </span>
      ),
    },
  ];

  const canUpload = permissions.has(PERM.importRun) && permissions.has(PERM.documentUpload);
  // The one primary action jumps to the upload card on this page (nothing else changes).
  const uploadLink = (
    <a href={`#${UPLOAD_ANCHOR}`} className={buttonClasses("secondary")}>
      <Icon name="upload" className="size-4" />
      {t("uploadAction")}
    </a>
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
        actions={canUpload ? uploadLink : undefined}
      />
      {canUpload && alongside ? (
        <ErpRefreshCard erpName={alongside.erpName} onStarted={onRefreshStarted ?? onStarted} />
      ) : null}
      {canUpload ? (
        <div id={UPLOAD_ANCHOR} tabIndex={-1} className="scroll-mt-20 focus:outline-none">
          <Card title={t("uploadTitle")} description={t("uploadDescription")}>
            <div className="space-y-5">
              <ImportSteps current={0} />
              <UploadSpreadsheet onStarted={onStarted} />
            </div>
          </Card>
        </div>
      ) : null}
      {permissions.has(PERM.importRun) ? <TemplateLibrary /> : null}
      <section aria-labelledby={historyId} className="space-y-3">
        <h2 id={historyId} className="text-lg font-semibold text-ink">
          {t("historyTitle")}
        </h2>
        <DataTableV2
          id="imports"
          stacked
          caption={t("historyTitle")}
          captionHidden
          columns={columns}
          state={imports}
          rowKey={(row) => row.id}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
          emptyAction={canUpload ? uploadLink : undefined}
          footer={
            onNext || onPrevious ? (
              <Pager label={t("pagesLabel")} page={page} onPrevious={onPrevious} onNext={onNext} />
            ) : undefined
          }
        />
      </section>
      <section aria-labelledby={templatesId} className="space-y-3">
        <div className="space-y-1">
          <h2 id={templatesId} className="text-lg font-semibold text-ink">
            {t("templatesTitle")}
          </h2>
          <p className="text-sm text-ink-muted">{t("templatesDescription")}</p>
        </div>
        <DataTableV2
          id="import-templates"
          stacked
          caption={t("templatesTitle")}
          captionHidden
          columns={templateColumns}
          state={templates}
          rowKey={(row) => row.id}
          emptyTitle={t("templatesEmptyTitle")}
          emptyBody={t("templatesEmptyBody")}
        />
      </section>
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
  // FR-TEN-022: the school's operating mode decides whether the ERP refresh is offered.
  const tenant = useApiQuery(TENANT_KEY, () => unwrap(api.GET("/api/v1/tenant")));
  const alongside =
    tenant.status === "ready" && tenant.data.settings.operating_mode === "alongside"
      ? { erpName: tenant.data.settings.current_erp_name ?? "" }
      : null;
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
      alongside={alongside}
      onRefreshStarted={(batch) => router.push(`/imports/${batch.id}?preset=${ERP_PRESET}`)}
    />
  );
}

"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { Badge, Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card, cardClasses } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField, type SelectOption } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { GuardedTextField } from "@/features/students/fields";
import { FormDialog } from "@/features/students/FormDialog";
import { PERM, useStaffPermissions, type Permissions } from "@/features/students/me";
import { Pager, useCursorStack } from "@/features/students/paging";
import { LoadGate, useAttributeIndex, useAttributes } from "@/features/students/parts";
import { ProblemAlert } from "@/features/students/ProblemAlert";
import type { Attribute } from "@/features/students/types";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { apiFieldErrors } from "@/lib/api-errors";
import { newIdempotencyKey, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formValues } from "@/lib/forms";
import { formatCount, formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import type { Loadable } from "@/lib/loadable";
import { checkbox } from "@/lib/validation";
import {
  IMPORTS_KEY,
  ImportStatusBadge,
  ImportSteps,
  importStep,
  SourceName,
} from "./ImportsScreen";
import { POLL_MS, usePolledQuery } from "./poll";
import {
  IMPORT_BUSY,
  MAPPING_EDITABLE,
  SPECIAL_TARGETS,
  type ImportBatch,
  type ImportColumn,
  type ImportRow,
  type RowIssue,
  type RowStatusFilter,
} from "./types";

export const importKey = (id: string) => [...IMPORTS_KEY, id] as const;
const IGNORE = "ignore";
const TARGET_PATTERN = /^[a-z][a-z0-9_]{1,63}$/;

/** Rows exist once the file has been checked at least once. */
const HAS_ROWS: ReadonlySet<ImportBatch["status"]> = new Set([
  "validated",
  "committing",
  "committed",
  "reverting",
  "reverted",
]);

/** The uploaded file can be shown as a sheet once it was read (FR-IMP-008). */
const HAS_SHEET: ReadonlySet<ImportBatch["status"]> = new Set([
  "parsed",
  "validating",
  "validated",
  "committing",
  "committed",
  "reverting",
  "reverted",
]);

/** US-401 AC5: between checking and adding, open the file as a sheet to correct cells. */
function SheetLink({ importId, editable }: { importId: string; editable: boolean }) {
  const t = useTranslations("sheets.import");
  return (
    <Card
      title={t("openTitle")}
      description={editable ? t("openDescriptionEdit") : t("openDescriptionRead")}
    >
      <div data-print="hide">
        <ButtonLink href={`/imports/${importId}/sheet`} variant="secondary">
          <Icon name="layers" className="size-4" />
          {t("open")}
        </ButtonLink>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ labels */

/** Field name for an issue or a mapping target: attribute label, special target, or column. */
export function useTargetLabel(attributes: Loadable<readonly Attribute[]>) {
  const t = useTranslations("imports.targets");
  const index = useAttributeIndex(attributes);
  return (key: string): string => {
    if (key === IGNORE) return t("ignore");
    if ((SPECIAL_TARGETS as readonly string[]).includes(key)) return translateOr(t, key, "row");
    if (index.byKey.has(key)) return index.label(key);
    if (/^columns\.\d+$/.test(key)) return t("column", { number: Number(key.slice(8)) + 1 });
    return translateOr(t, key, "row");
  };
}

/** One row error or warning in plain language: "Date of birth: write the date as DD/MM/YYYY". */
export function IssueText({ issue, label }: { issue: RowIssue; label: (key: string) => string }) {
  const t = useTranslations("imports.issues");
  return (
    <>
      <span className="font-semibold">{label(issue.field)}:</span>{" "}
      {translateOr(t, issue.code, "generic", { row: issue.ref ?? "" })}
    </>
  );
}

/* ------------------------------------------------------------------ mapping */

interface MappingFormProps {
  batch: ImportBatch;
  attributes: readonly Attribute[];
  label: (key: string) => string;
  onChecked: () => void;
}

const mappingSchema = z.record(z.string(), z.string());

/** The starting choice for a column: saved mapping, else the suggestion, else "ignore". */
export function initialTarget(column: ImportColumn): string {
  return column.target ?? column.suggested ?? IGNORE;
}

/** Targets chosen for more than one column (each field can be filled from one column only). */
export function duplicateTargets(targets: Record<number, string>): Set<number> {
  const seen = new Map<string, number[]>();
  for (const [index, target] of Object.entries(targets)) {
    if (target === IGNORE) continue;
    seen.set(target, [...(seen.get(target) ?? []), Number(index)]);
  }
  return new Set([...seen.values()].filter((list) => list.length > 1).flat());
}

/**
 * US-401 AC1 / FR-IMP-002: choose which field each column fills (suggestions from English and
 * Telugu headers are pre-selected and marked), then check every row. PUT /mapping (If-Match)
 * → POST /validate. Nothing is saved to student records here.
 */
function MappingForm({ batch, attributes, label, onChecked }: MappingFormProps) {
  const t = useTranslations("imports.mapping");
  const ti = useTranslations("imports.issues");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const errorId = useId();
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);

  const options: SelectOption[] = useMemo(
    () => [
      { value: IGNORE, label: label(IGNORE) },
      // Only fields this source may fill (e.g. Aadhaar last 4 digits only from Aadhaar).
      ...attributes
        .filter((item) => !item.allowed_sources || item.allowed_sources.includes(batch.source))
        .map((item) => ({ value: item.key, label: label(item.key) })),
      ...SPECIAL_TARGETS.map((key) => ({ value: key, label: label(key) })),
    ],
    [attributes, label, batch.source],
  );

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = mappingSchema.parse(formValues(form));
    const targets: Record<number, string> = {};
    for (const column of batch.columns) {
      const value = raw[`col-${column.index}`] ?? IGNORE;
      targets[column.index] = TARGET_PATTERN.test(value) ? value : IGNORE;
    }
    const duplicates = duplicateTargets(targets);
    const local: Record<number, string> = {};
    for (const index of duplicates) local[index] = t("duplicate");
    const hasAdmission = Object.values(targets).includes("admission_no");
    setErrors(local);
    setFailure(undefined);
    setFormError(hasAdmission ? null : ti("admission_no_not_mapped"));
    if (duplicates.size > 0 || !hasAdmission) {
      requestAnimationFrame(() =>
        form.querySelector<HTMLElement>("[aria-invalid='true'], [data-mapping-error]")?.focus(),
      );
      return;
    }
    const columns = Object.entries(targets)
      .filter(([, target]) => target !== IGNORE)
      .map(([index, target]) => ({ index: Number(index), target }));
    setPending(true);
    (async () => {
      await unwrap(
        api.PUT("/api/v1/imports/{import_id}/mapping", {
          params: { path: { import_id: batch.id } },
          headers: { "If-Match": `W/"${batch.version}"` },
          body: { columns },
        }),
      );
      await unwrap(
        api.POST("/api/v1/imports/{import_id}/validate", {
          params: { path: { import_id: batch.id } },
          headers: { "Idempotency-Key": newIdempotencyKey() },
        }),
      );
    })()
      .then(async () => {
        await queryClient.invalidateQueries({ queryKey: importKey(batch.id) });
        onChecked();
      })
      .catch((error: unknown) => {
        const server: Record<number, string> = {};
        let unmapped = false;
        for (const { field, key } of apiFieldErrors(error)) {
          // `columns.<n>`: n is the spreadsheet column index the API refused.
          const match = /^columns\.(\d+)$/.exec(field);
          const column = match
            ? batch.columns.find((item) => item.index === Number(match[1]))
            : undefined;
          if (column) server[column.index] = translateOr(ti, key, "generic", { row: "" });
          else unmapped = true;
        }
        setErrors(server);
        if (Object.keys(server).length === 0 || unmapped) setFailure(error);
      })
      .finally(() => setPending(false));
  }

  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4">
      {formError ? (
        <div data-mapping-error tabIndex={-1} id={errorId}>
          <Alert tone="danger" live>
            {formError}
          </Alert>
        </div>
      ) : null}
      <div className="space-y-2">
        {/* Column titles for sighted users; each select is named "Field for the column …". */}
        <div aria-hidden="true" className="hidden gap-4 px-4 sm:grid sm:grid-cols-2">
          <Eyebrow as="span">{t("colHeader")}</Eyebrow>
          <Eyebrow as="span">{t("colTarget")}</Eyebrow>
        </div>
        <ul aria-label={t("tableLabel")} className="space-y-2">
          {batch.columns.map((column) => {
            const header = column.header || t("noHeader", { number: column.index + 1 });
            return (
              <li
                key={column.index}
                className={`${cardClasses({ padding: "sm", tone: "outline" })} grid gap-3 sm:grid-cols-2 sm:items-center`}
              >
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <Icon name="file" className="size-4 text-ink-muted" />
                  <span className="font-medium break-words text-ink">{header}</span>
                  {column.suggested && column.target === null ? (
                    <Badge tone="info">
                      <Icon name="sparkles" className="size-3.5" />
                      {t("suggested")}
                    </Badge>
                  ) : null}
                </div>
                <SelectField
                  name={`col-${column.index}`}
                  label={t("targetFor", { header })}
                  className="[&>label]:sr-only"
                  options={options}
                  defaultValue={initialTarget(column)}
                  error={errors[column.index]}
                  disabled={pending}
                />
              </li>
            );
          })}
        </ul>
      </div>
      <ProblemAlert error={failure} namespace="imports.errors" />
      <div className="flex flex-wrap items-center justify-end gap-3">
        <p className="text-sm text-ink-muted">{t("nothingSaved")}</p>
        <Button type="submit" disabled={pending}>
          {pending ? t("checking") : t("submit")}
        </Button>
      </div>
    </form>
  );
}

/* ------------------------------------------------------------------ rows */

const ROW_FILTERS = ["all", "error", "warning", "valid", "committed", "skipped"] as const;

const rowPill: Record<ImportRow["status"], PillVariant> = {
  valid: "positive",
  error: "negative",
  committed: "done",
  skipped: "tag",
  reverted: "tag",
};
type RowFilterChoice = (typeof ROW_FILTERS)[number];

function rowQuery(filter: RowFilterChoice): { status?: RowStatusFilter } {
  return filter === "all" ? {} : { status: filter };
}

function RowsCard({
  batch,
  label,
  initialFilter,
}: {
  batch: ImportBatch;
  label: (key: string) => string;
  initialFilter: RowFilterChoice;
}) {
  const t = useTranslations("imports.rows");
  const api = useBffClient("staff");
  const [filter, setFilter] = useState<RowFilterChoice>(initialFilter);
  const pages = useCursorStack();
  const query = {
    ...rowQuery(filter),
    limit: 50,
    ...(pages.cursor ? { cursor: pages.cursor } : {}),
  };
  const rows = useApiQuery([...importKey(batch.id), "rows", batch.version, query], () =>
    unwrap(
      api.GET("/api/v1/imports/{import_id}/rows", {
        params: { path: { import_id: batch.id }, query },
      }),
    ),
  );
  const list: Loadable<readonly ImportRow[]> =
    rows.status === "ready" ? { status: "ready", data: rows.data.data } : rows;
  const next = rows.status === "ready" ? rows.data.next_cursor : null;

  const columns: Column<ImportRow>[] = [
    {
      key: "row",
      header: t("colRow"),
      cell: (row) => <span className="font-mono text-xs text-ink-muted">{row.row_no}</span>,
    },
    {
      key: "admission",
      header: t("colAdmissionNo"),
      cell: (row) =>
        row.student_id && row.status === "committed" ? (
          <Link
            href={`/students/${row.student_id}`}
            className="text-primary underline underline-offset-4"
          >
            <Value>{row.admission_no}</Value>
          </Link>
        ) : (
          <Value>{row.admission_no}</Value>
        ),
    },
    {
      key: "name",
      header: t("colName"),
      cell: (row) => <Value>{row.values.full_name}</Value>,
    },
    { key: "class", header: t("colClass"), cell: (row) => <Value>{row.class_section}</Value> },
    {
      key: "action",
      header: t("colAction"),
      cell: (row) => (row.action ? t(`action.${row.action}`) : <Value>{null}</Value>),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Pill variant={rowPill[row.status]}>{t(`status.${row.status}`)}</Pill>,
    },
    {
      key: "issues",
      header: t("colIssues"),
      className: "min-w-72",
      cell: (row) =>
        row.errors.length === 0 && row.warnings.length === 0 ? (
          <Value>{null}</Value>
        ) : (
          <ul className="space-y-2">
            {row.errors.map((issue, index) => (
              <li key={`e${index}`} className="flex flex-wrap items-baseline gap-2">
                <Pill variant="negative">
                  <Icon name="alert" className="size-3" />
                  {t("errorTag")}
                  <span className="sr-only">:</span>
                </Pill>
                <span className="min-w-0 flex-1">
                  <IssueText issue={issue} label={label} />
                </span>
              </li>
            ))}
            {row.warnings.map((issue, index) => (
              <li key={`w${index}`} className="flex flex-wrap items-baseline gap-2">
                <Badge tone="warning">
                  {t("warningTag")}
                  <span className="sr-only">:</span>
                </Badge>
                <span className="min-w-0 flex-1">
                  <IssueText issue={issue} label={label} />
                </span>
              </li>
            ))}
            {row.sensitive.length > 0 ? (
              <li className="text-ink-muted">{t("sensitive", { count: row.sensitive.length })}</li>
            ) : null}
          </ul>
        ),
    },
  ];

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3" data-print="hide">
          <SelectField
            className="w-full max-w-xs"
            label={t("filter")}
            value={filter}
            onChange={(event) => {
              const value = ROW_FILTERS.find((item) => item === event.currentTarget.value);
              pages.reset();
              setFilter(value ?? "all");
            }}
            options={ROW_FILTERS.map((value) => ({ value, label: t(`filters.${value}`) }))}
          />
        </div>
        <DataTable
          caption={t("title")}
          captionHidden
          columns={columns}
          state={list}
          rowKey={(row) => String(row.row_no)}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
        <Pager
          label={t("pagesLabel")}
          page={pages.page}
          onPrevious={pages.hasPrevious ? pages.previous : undefined}
          onNext={next ? () => pages.next(next) : undefined}
        />
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ actions */

const commitSchema = z.object({ skip_error_rows: checkbox });
const emptySchema = z.object({});
const templateSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .max(100, { error: "tooLong" })
    .refine((value) => !containsFullAadhaar(value), { error: "invalid" }),
});

function CommitDialog({ batch }: { batch: ImportBatch }) {
  const t = useTranslations("imports.commit");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const count = (value: number | undefined) => formatCount(value ?? 0, locale) ?? "0";
  const valid = batch.stats.valid ?? 0;
  return (
    <FormDialog
      triggerLabel={t("open")}
      triggerVariant="primary"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("submit")}
      problems="imports.errors"
      schema={commitSchema}
      invalidate={[importKey(batch.id), [...IMPORTS_KEY, "list"]]}
      submit={(data, idempotencyKey) =>
        unwrap(
          api.POST("/api/v1/imports/{import_id}/commit", {
            params: { path: { import_id: batch.id } },
            headers: { "Idempotency-Key": idempotencyKey },
            body: { skip_error_rows: data.skip_error_rows },
          }),
        )
      }
    >
      {() => (
        <>
          <p className="text-sm">
            {t("summary", { valid: count(valid), total: count(batch.row_count) })}
          </p>
          <p className="text-sm">
            {t("sourceLabel")} <SourceName source={batch.source} />
          </p>
          {batch.error_count > 0 ? (
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" name="skip_error_rows" className="mt-1 size-4" />
              <span>{t("skipErrors", { errors: count(batch.error_count) })}</span>
            </label>
          ) : null}
          <Alert tone="info">{t("undoNote")}</Alert>
        </>
      )}
    </FormDialog>
  );
}

function RevertDialog({ batch }: { batch: ImportBatch }) {
  const t = useTranslations("imports.revert");
  const api = useBffClient("staff");
  return (
    <FormDialog
      triggerLabel={t("open")}
      triggerVariant="danger"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("submit")}
      confirmVariant="danger"
      problems="imports.errors"
      schema={emptySchema}
      invalidate={[importKey(batch.id), [...IMPORTS_KEY, "list"], ["staff", "students"]]}
      submit={() =>
        unwrap(
          api.POST("/api/v1/imports/{import_id}/revert", {
            params: { path: { import_id: batch.id } },
          }),
        )
      }
    >
      {() => <Alert tone="warning">{t("warning")}</Alert>}
    </FormDialog>
  );
}

function SaveTemplateDialog({ batch }: { batch: ImportBatch }) {
  const t = useTranslations("imports.template");
  const api = useBffClient("staff");
  const [saved, setSaved] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <FormDialog
        triggerLabel={t("open")}
        title={t("title")}
        description={t("description")}
        confirmLabel={t("submit")}
        problems="imports.errors"
        schema={templateSchema}
        invalidate={[[...IMPORTS_KEY, "templates"]]}
        onOpen={() => setSaved(false)}
        onSuccess={() => setSaved(true)}
        submit={(data, idempotencyKey) =>
          unwrap(
            api.POST("/api/v1/import-templates", {
              headers: { "Idempotency-Key": idempotencyKey },
              body: { name: data.name, import_id: batch.id },
            }),
          )
        }
      >
        {(errors) => (
          <GuardedTextField
            name="name"
            label={t("name")}
            hint={t("nameHint")}
            error={errors.name}
            maxLength={100}
            autoComplete="off"
          />
        )}
      </FormDialog>
      {saved ? (
        <p role="status" className="text-sm font-semibold text-success-ink">
          {t("saved")}
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ the page */

function StatusPanel({ batch }: { batch: ImportBatch }) {
  const t = useTranslations("imports.detail");
  const tf = useTranslations("imports.failure");
  const busy = IMPORT_BUSY.has(batch.status);
  return (
    <div className="space-y-3">
      <div role="status" aria-live="polite">
        {busy ? (
          <Alert tone="info" title={t(`busy.${batch.status as "uploaded"}`)}>
            {t("busyBody")}
          </Alert>
        ) : null}
      </div>
      {batch.error_code ? (
        <Alert tone="danger" title={t("failedTitle")}>
          {translateOr(tf, batch.error_code, "generic")}
        </Alert>
      ) : null}
      {batch.raw_file_deleted_at ? <Alert tone="info">{t("fileDeleted")}</Alert> : null}
    </div>
  );
}

const STAT_KEYS = [
  "rows",
  "valid",
  "errors",
  "warnings",
  "create",
  "update",
  "committed",
  "created",
  "updated",
  "skipped",
] as const;

function Stats({ batch }: { batch: ImportBatch }) {
  const t = useTranslations("imports.stats");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const shown = STAT_KEYS.filter((key) => batch.stats[key] !== undefined);
  if (shown.length === 0) return null;
  return (
    <Card title={t("title")} description={t("description")}>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {shown.map((key) => (
          <StatCard
            key={key}
            label={t(key)}
            value={formatCount(batch.stats[key], locale) ?? null}
            unavailableLabel={tc("notAvailable")}
          />
        ))}
      </dl>
    </Card>
  );
}

/** Whole hours until the undo deadline, measured on this device after the page has loaded. */
function useHoursLeft(deadline: string | null | undefined): number | null {
  // Client-only: the import is fetched in the browser, so this never runs on the server.
  const [now, setNow] = useState<number | null>(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);
  const end = deadline ? Date.parse(deadline) : Number.NaN;
  if (now === null || Number.isNaN(end) || end <= now) return null;
  return Math.floor((end - now) / 3_600_000);
}

/** US-401 AC3 / FR-IMP-005: the 24-hour undo, with how long is left. */
function UndoCard({
  batch,
  canRun,
  canCommit,
}: {
  batch: ImportBatch;
  canRun: boolean;
  canCommit: boolean;
}) {
  const t = useTranslations("imports.detail");
  const open = batch.can_revert && Boolean(batch.revert_deadline);
  const hours = useHoursLeft(open ? batch.revert_deadline : null);
  return (
    <Card
      eyebrow={t("undoEyebrow")}
      title={t("addedTitle")}
      actions={
        hours !== null ? (
          <Pill variant="date" size="md">
            <Icon name="clock" className="size-4" />
            {hours >= 1 ? t("hoursLeft", { hours }) : t("underAnHour")}
          </Pill>
        ) : null
      }
    >
      <div className="space-y-4">
        {open ? (
          <p>{t("revertUntil", { deadline: formatDateTime(batch.revert_deadline) ?? "" })}</p>
        ) : (
          <p className="text-ink-muted">{t("revertClosed")}</p>
        )}
        <div className="flex flex-wrap items-center gap-3" data-print="hide">
          {batch.can_revert && canCommit ? <RevertDialog batch={batch} /> : null}
          {canRun ? <SaveTemplateDialog batch={batch} /> : null}
        </div>
      </div>
    </Card>
  );
}

export interface ImportDetailViewProps {
  batch: Loadable<ImportBatch>;
  attributes: Loadable<readonly Attribute[]>;
  permissions: Permissions;
}

/** US-401 / FR-IMP-002..005: one import from mapping to check, add and (within 24 h) undo. */
export function ImportDetailView({ batch, attributes, permissions }: ImportDetailViewProps) {
  const t = useTranslations("imports.detail");
  const tl = useTranslations("imports.list");
  const tn = useTranslations("school.nav");
  const label = useTargetLabel(attributes);
  const [checked, setChecked] = useState(0);

  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: tl("title"), href: "/imports" },
  ];
  if (batch.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("loadingTitle")}
          breadcrumb={[...crumbs, { label: t("loadingTitle") }]}
        />
        <LoadGate state={batch} />
      </div>
    );
  }
  const data = batch.data;
  const canRun = permissions.has(PERM.importRun);
  const canCommit = permissions.has(PERM.importCommit);
  const editable = MAPPING_EDITABLE.has(data.status) && canRun;

  const title = t("title", { date: formatDateTime(data.created_at) ?? "" });

  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        breadcrumb={[...crumbs, { label: title }]}
        badge={<ImportStatusBadge status={data.status} />}
        description={t(`next.${data.status as "parsed"}`)}
      />
      <div className={cardClasses()}>
        <div className="space-y-5">
          <ImportSteps current={importStep(data.status)} failed={data.status === "failed"} />
          <dl className="flex flex-wrap gap-x-8 gap-y-3 border-t border-border pt-4 text-sm">
            <div>
              <dt className="text-ink-muted">{t("source")}</dt>
              <dd className="font-medium text-ink">
                <SourceName source={data.source} />
              </dd>
            </div>
            {data.committed_at ? (
              <div>
                <dt className="text-ink-muted">{t("committedAt")}</dt>
                <dd className="font-medium text-ink">{formatDateTime(data.committed_at)}</dd>
              </div>
            ) : null}
            {data.reverted_at ? (
              <div>
                <dt className="text-ink-muted">{t("revertedAt")}</dt>
                <dd className="font-medium text-ink">{formatDateTime(data.reverted_at)}</dd>
              </div>
            ) : null}
          </dl>
        </div>
      </div>
      <StatusPanel batch={data} />

      {data.status === "committed" ? (
        <UndoCard batch={data} canRun={canRun} canCommit={canCommit} />
      ) : null}

      <Stats batch={data} />

      {canRun && HAS_SHEET.has(data.status) && !data.raw_file_deleted_at ? (
        <SheetLink importId={data.id} editable={MAPPING_EDITABLE.has(data.status)} />
      ) : null}

      {data.status === "validated" ? (
        <Card title={t("readyTitle")} description={t("readyDescription")}>
          <div className="flex flex-wrap items-center gap-3" data-print="hide">
            {data.can_commit && canCommit ? <CommitDialog batch={data} /> : null}
            {!canCommit ? (
              <p className="text-sm text-ink-muted">{t("noCommitPermission")}</p>
            ) : null}
            {data.can_commit ? null : <p className="text-sm">{t("nothingToAdd")}</p>}
            {canRun ? <SaveTemplateDialog batch={data} /> : null}
          </div>
        </Card>
      ) : null}

      {editable && attributes.status === "ready" ? (
        <Card title={t("mappingTitle")} description={t("mappingDescription")}>
          <MappingForm
            // A new version (after checking) starts from the saved mapping again.
            key={`${data.version}-${checked}`}
            batch={data}
            attributes={attributes.data}
            label={label}
            onChecked={() => setChecked((value) => value + 1)}
          />
        </Card>
      ) : editable ? (
        <LoadGate state={attributes} />
      ) : null}

      {HAS_ROWS.has(data.status) ? (
        <RowsCard
          // A new status (e.g. committed) starts again from its own filter, so rows that were
          // hidden by "errors only" before adding show up.
          key={data.status}
          batch={data}
          label={label}
          initialFilter={data.status === "validated" && data.error_count > 0 ? "error" : "all"}
        />
      ) : null}
    </div>
  );
}

/** GET /imports/{id}, asked again every few seconds while a worker reads, checks or adds it. */
export function ImportDetailScreen({ importId }: { importId: string }) {
  const api = useBffClient("staff");
  const permissions = useStaffPermissions();
  const batch = usePolledQuery(
    importKey(importId),
    () =>
      unwrap(api.GET("/api/v1/imports/{import_id}", { params: { path: { import_id: importId } } })),
    (data) => (data && IMPORT_BUSY.has(data.status) ? POLL_MS : false),
  );
  const attributes = useAttributes();
  return <ImportDetailView batch={batch} attributes={attributes} permissions={permissions} />;
}

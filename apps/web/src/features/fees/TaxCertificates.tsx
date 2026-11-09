"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useMemo, useRef, useState } from "react";
import * as z from "@/lib/zod";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import type { FilterChip } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { SelectField } from "@/components/ui/Select";
import { useSectionOptions, type SectionOption } from "@/features/findings/data";
import { unwrap, useApiMutation, useApiQuery, useBffClient } from "@/lib/bff/query";
import { pollHeaders } from "@/lib/bff/poll";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { openFileUrl } from "@/lib/open-file-url";
import { COLLECT, FINANCE_READ, isFeesOff, printHref, useFeeStatus, type Account } from "./data";
import { Amount, FeesGate, NoAccess } from "./parts";
import {
  financialYears,
  TAX_KEYS,
  taxPollDelay,
  withTaxErrors,
  type TaxCertificate,
  type TaxCertificateBulk,
  type TaxCertificateListItem,
} from "./tax";

const NO_FIELDS = z.object({});
const PAGE_SIZE = 50;
const STATUSES = ["issued", "superseded"] as const;

type PdfStatus = TaxCertificateListItem["pdf_status"];

/** "Issued" or "Superseded" (always text, never colour alone). */
export function TaxStatusPill({ status }: { status: TaxCertificateListItem["status"] }) {
  const t = useTranslations("fees.taxCertificate.status");
  return <Pill variant={status === "issued" ? "done" : "tag"}>{t(status)}</Pill>;
}

/** The PDF state in words: being made, failed (with why), or ready. */
function PdfStateText({ status }: { status: PdfStatus }) {
  const t = useTranslations("fees.taxCertificate.pdf");
  if (status === "ready") return <span className="text-sm">{t("ready")}</span>;
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 text-sm font-semibold text-danger">
        <Icon name="alert" className="size-4 shrink-0" />
        {t("failed")}
      </span>
    );
  }
  return <span className="text-sm text-ink-muted">{t("preparing")}</span>;
}

/**
 * Reads certificates again while a PDF is made, passively (XB-06: an unattended office PC is
 * not kept signed in), stopping once every PDF is ready or failed.
 */
function usePdfPoll() {
  const polls = useRef(0);
  return (rows: readonly { pdf_status: PdfStatus }[] | undefined) => {
    if (!rows?.some((row) => row.pdf_status === "queued")) {
      polls.current = 0;
      return false as const;
    }
    polls.current += 1;
    return taxPollDelay(polls.current - 1);
  };
}

/** The stored PDF through a short-lived link (409 `pdf_not_ready` while it is made). */
function useTaxDownload() {
  const api = useBffClient("staff");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const busy = useRef(false);
  async function download(id: string) {
    if (busy.current) return;
    busy.current = true;
    setPendingId(id);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/fees/tax-certificates/{tax_certificate_id}/download-url", {
          params: { path: { tax_certificate_id: id } },
        }),
      );
      openFileUrl(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      busy.current = false;
      setPendingId(null);
    }
  }
  return { pendingId, error, download };
}

/** Queue the PDF again after it failed (`fee.collect`). One request per click. */
function useRetryRender() {
  const api = useBffClient("staff");
  const busy = useRef(false);
  const mutation = useApiMutation(
    (id: string) =>
      unwrap(
        api.POST("/api/v1/fees/tax-certificates/{tax_certificate_id}/render", {
          params: { path: { tax_certificate_id: id } },
        }),
      ),
    { invalidate: [TAX_KEYS.all] },
  );
  return {
    pending: mutation.isPending,
    error: mutation.error ?? undefined,
    retry: (id: string) => {
      if (busy.current) return;
      busy.current = true;
      mutation.mutate(id, {
        onSettled: () => {
          busy.current = false;
        },
      });
    },
  };
}

function PrintViewLink({ id, label }: { id: string; label: string }) {
  const tf = useTranslations("fees");
  return (
    <a
      href={printHref(`/api/v1/fees/tax-certificates/${id}/print`)}
      target="_blank"
      rel="noopener noreferrer"
      className={buttonClasses("secondary", "sm")}
    >
      <Icon name="printer" className="size-4" />
      {label}
      <span className="sr-only"> ({tf("newTab")})</span>
    </a>
  );
}

/** Print view, download (when the PDF is ready) and, for collectors, "make the PDF again". */
function CertificateActions({
  row,
  download,
  retry,
  canRetry,
}: {
  row: Pick<TaxCertificateListItem, "id" | "serial_number" | "pdf_status">;
  download: ReturnType<typeof useTaxDownload>;
  retry: ReturnType<typeof useRetryRender>;
  canRetry: boolean;
}) {
  const t = useTranslations("fees.taxCertificate");
  const tc = useTranslations("common");
  return (
    <span className="flex flex-wrap items-center gap-2">
      <PrintViewLink id={row.id} label={t("print")} />
      {row.pdf_status === "ready" ? (
        <Button
          variant="secondary"
          size="sm"
          disabled={download.pendingId !== null}
          onClick={() => void download.download(row.id)}
        >
          <Icon name="arrowDown" className="size-4" />
          {download.pendingId === row.id ? tc("working") : t("download")}
          <span className="sr-only"> {row.serial_number}</span>
        </Button>
      ) : null}
      {row.pdf_status === "failed" && canRetry ? (
        <Button
          variant="secondary"
          size="sm"
          disabled={retry.pending}
          onClick={() => retry.retry(row.id)}
        >
          <Icon name="refresh" className="size-4" />
          {retry.pending ? tc("working") : t("retry")}
          <span className="sr-only"> {row.serial_number}</span>
        </Button>
      ) : null}
    </span>
  );
}

function NotTaxAdvice() {
  const t = useTranslations("fees.taxCertificate");
  return (
    <p className="max-w-prose text-sm text-ink-muted">
      <span className="font-semibold text-ink">{t("notAdviceTitle")}</span> {t("notAdvice")}
    </p>
  );
}

// --- one student (the fee account's tab) ------------------------------------------------------

/**
 * The fee account's "Tax certificate" tab (FR-FEE-049, FR-FEE-052): pick a financial year
 * (started years only, 8 back), see the issued certificate and earlier superseded ones, issue
 * or issue again (`fee.collect`), open the print view and download the PDF.
 */
export function TaxCertificatePanel({ account }: { account: Account }) {
  const t = useTranslations("fees.taxCertificate");
  const can = useStaffCan();
  const canIssue = can(COLLECT);
  const status = useFeeStatus();
  return (
    <FeesGate data={status}>
      {(s) => (
        <StudentCertificates
          key={s.financial_year}
          account={account}
          years={financialYears(s.financial_year)}
          canIssue={canIssue}
          title={t("panelTitle")}
        />
      )}
    </FeesGate>
  );
}

function StudentCertificates({
  account,
  years,
  canIssue,
  title,
}: {
  account: Account;
  years: readonly string[];
  canIssue: boolean;
  title: string;
}) {
  const t = useTranslations("fees.taxCertificate");
  const api = useBffClient("staff");
  const [year, setYear] = useState(years[0] ?? "");
  const [outcome, setOutcome] = useState<TaxCertificate | null>(null);
  const poll = usePdfPoll();
  const download = useTaxDownload();
  const retry = useRetryRender();
  const studentId = account.student_id;
  const list = useApiQuery(
    TAX_KEYS.student(studentId, year),
    (context) =>
      unwrap(
        api.GET("/api/v1/fees/tax-certificates", {
          params: { query: { student_id: studentId, financial_year: year, limit: 50 } },
          headers: pollHeaders(context),
        }),
      ),
    { enabled: year !== "", refetchInterval: (page) => poll(page?.data) },
  );
  const rows = list.status === "ready" ? list.data.data : [];
  const current = rows.find((row) => row.status === "issued") ?? null;
  const name = account.display_name ?? account.admission_no ?? "";
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className="space-y-4">
      <div className="space-y-1">
        <h3 id={titleId} className="text-base font-semibold">
          {title}
        </h3>
        <p className="max-w-prose text-sm text-ink-muted">{t("panelBody")}</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <SelectField
          name="financial_year"
          label={t("year")}
          className="w-44"
          value={year}
          onChange={(e) => {
            setYear(e.target.value);
            setOutcome(null);
          }}
          options={years.map((y) => ({ value: y, label: t("yearOption", { year: y }) }))}
        />
        {canIssue && list.status === "ready" ? (
          <ActionDialog<typeof NO_FIELDS, TaxCertificate>
            triggerLabel={current ? t("issueAgain") : t("issue")}
            triggerVariant={current ? "secondary" : "primary"}
            title={t("issueTitle")}
            description={t("issueBody", { year, name })}
            confirmLabel={current ? t("issueAgain") : t("issue")}
            consequence={current ? t("issueAgainConsequence") : t("issueConsequence")}
            note={t("notAdvice")}
            schema={NO_FIELDS}
            invalidate={[TAX_KEYS.all]}
            errorNamespace="fees.taxCertificate"
            onSuccess={(result) => setOutcome(result)}
            submit={(_input, key) =>
              withTaxErrors(
                unwrap(
                  api.POST("/api/v1/fees/tax-certificates", {
                    headers: { "Idempotency-Key": key },
                    body: { student_id: studentId, financial_year: year },
                  }),
                ),
              )
            }
          />
        ) : null}
      </div>
      <NotTaxAdvice />
      {outcome && outcome.financial_year === year ? (
        <Alert tone={outcome.outcome === "unchanged" ? "info" : "success"} live>
          {t(`outcome.${outcome.outcome ?? "issued"}`, { serial: outcome.serial_number })}
        </Alert>
      ) : null}
      <ApiErrorAlert error={download.error} namespace="fees.taxCertificate" />
      <ApiErrorAlert error={retry.error} namespace="fees.taxCertificate" />
      <FeesGate data={list}>
        {() => (
          <>
            {current ? (
              <CurrentCertificate
                row={current}
                download={download}
                retry={retry}
                canRetry={canIssue}
              />
            ) : (
              <p>{t("none", { year })}</p>
            )}
            {rows.some((row) => row.status === "superseded") ? (
              <div className="space-y-2">
                <h4 className="text-sm font-semibold">{t("earlierTitle", { year })}</h4>
                <ul className="divide-y divide-border">
                  {rows
                    .filter((row) => row.status === "superseded")
                    .map((row) => (
                      <li key={row.id} className="flex flex-wrap items-center gap-3 py-2">
                        <span className="num font-semibold whitespace-nowrap">
                          {row.serial_number}
                        </span>
                        <TaxStatusPill status={row.status} />
                        <Amount value={row.amount} />
                        <span className="num text-sm whitespace-nowrap text-ink-muted">
                          {formatDate(row.issued_at)}
                        </span>
                        <PrintViewLink id={row.id} label={t("print")} />
                      </li>
                    ))}
                </ul>
              </div>
            ) : null}
          </>
        )}
      </FeesGate>
    </section>
  );
}

function CurrentCertificate({
  row,
  download,
  retry,
  canRetry,
}: {
  row: TaxCertificateListItem;
  download: ReturnType<typeof useTaxDownload>;
  retry: ReturnType<typeof useRetryRender>;
  canRetry: boolean;
}) {
  const t = useTranslations("fees.taxCertificate");
  return (
    <div className="space-y-3 rounded-lg border border-border p-4">
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-sm text-ink-muted">{t("serial")}</dt>
          <dd className="num font-semibold whitespace-nowrap">{row.serial_number}</dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("amount")}</dt>
          <dd className="font-semibold">
            <Amount value={row.amount} />
          </dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("issuedOn")}</dt>
          <dd className="num whitespace-nowrap">{formatDate(row.issued_at)}</dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("statusLabel")}</dt>
          <dd className="flex flex-wrap items-center gap-2">
            <TaxStatusPill status={row.status} />
            <span className="text-sm">{t("receipts", { count: row.receipt_count })}</span>
          </dd>
        </div>
      </dl>
      <div className="space-y-2">
        <p role="status">
          <PdfStateText status={row.pdf_status} />
        </p>
        {row.pdf_status === "queued" ? (
          <p className="text-sm text-ink-muted">{t("pdfPreparingBody")}</p>
        ) : row.pdf_status === "failed" ? (
          <p className="text-sm text-ink-muted">{t("pdfFailedBody")}</p>
        ) : null}
      </div>
      <CertificateActions row={row} download={download} retry={retry} canRetry={canRetry} />
    </div>
  );
}

// --- a class or section -------------------------------------------------------------------------

/** Class (whole) and section targets for bulk issue and the list filter, in class order. */
function targetOptions(
  sections: readonly SectionOption[],
  wholeClass: (label: string) => string,
): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [];
  const seen = new Set<string>();
  for (const section of sections) {
    if (!seen.has(section.classId)) {
      seen.add(section.classId);
      out.push({
        value: `class:${section.classId}`,
        label: wholeClass(section.classLabel || section.label),
      });
    }
    out.push({ value: `section:${section.id}`, label: section.label });
  }
  return out;
}

function parseTarget(value: string): { class_id?: string; section_id?: string } {
  const [kind, id] = value.split(":");
  if (!id) return {};
  return kind === "class" ? { class_id: id } : kind === "section" ? { section_id: id } : {};
}

const bulkSchema = z.object({
  financial_year: z.string().min(1, { error: "required" }),
  target: z.string().regex(/^(class|section):[0-9a-f-]{36}$/, { error: "required" }),
});

/** Bulk issue for one class or section of the current year (FR-FEE-050; `fee.collect`). */
export function TaxBulkIssueDialog({
  years,
  sections,
}: {
  years: readonly string[];
  sections: readonly SectionOption[];
}) {
  const t = useTranslations("fees.taxCertificate");
  const api = useBffClient("staff");
  const targets = targetOptions(sections, (label) => t("wholeClass", { class: label }));
  return (
    <ActionDialog
      triggerLabel={t("bulk")}
      triggerVariant="primary"
      title={t("bulkTitle")}
      description={t("bulkBody")}
      confirmLabel={t("bulkConfirm")}
      consequence={t("bulkConsequence")}
      note={t("notAdvice")}
      schema={bulkSchema}
      invalidate={[TAX_KEYS.all]}
      errorNamespace="fees.taxCertificate"
      submit={(input, key) =>
        withTaxErrors(
          unwrap(
            api.POST("/api/v1/fees/tax-certificates/bulk", {
              headers: { "Idempotency-Key": key },
              body: { financial_year: input.financial_year, ...parseTarget(input.target) },
            }),
          ),
        )
      }
      renderResult={(result, close) => <BulkResult result={result} close={close} />}
    >
      {(errors) => (
        <>
          <SelectField
            name="financial_year"
            label={t("year")}
            error={errors.financial_year}
            defaultValue={years[0]}
            options={years.map((y) => ({ value: y, label: t("yearOption", { year: y }) }))}
          />
          <SelectField
            name="target"
            label={t("bulkTarget")}
            hint={targets.length === 0 ? t("noTargets") : undefined}
            placeholder={t("bulkTargetPlaceholder")}
            error={errors.target}
            options={targets}
          />
        </>
      )}
    </ActionDialog>
  );
}

function BulkResult({ result, close }: { result: TaxCertificateBulk; close: () => void }) {
  const t = useTranslations("fees.taxCertificate");
  const rows: [string, number][] = [
    [t("result.issued"), result.issued],
    [t("result.reissued"), result.reissued],
    [t("result.unchanged"), result.unchanged],
    [t("result.skipped"), result.skipped_no_tuition],
    [t("result.skippedTooMany"), result.skipped_too_many_receipts],
  ];
  return (
    <>
      <Alert tone="success" live title={t("result.title", { year: result.financial_year })}>
        {t("result.students", { count: result.students })}
      </Alert>
      <dl className="divide-y divide-border">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-4 py-2">
            <dt>{label}</dt>
            <dd className="num font-semibold">{value}</dd>
          </div>
        ))}
      </dl>
      {result.skipped_no_tuition > 0 ? (
        <p className="text-sm text-ink-muted">{t("result.skippedHint")}</p>
      ) : null}
      {result.skipped_too_many_receipts > 0 ? (
        <p className="text-sm text-ink-muted">{t("result.skippedTooManyHint")}</p>
      ) : null}
      <div className="flex justify-end">
        <Button onClick={close}>{t("result.close")}</Button>
      </div>
    </>
  );
}

// --- the list -----------------------------------------------------------------------------------

interface Filters {
  year: string;
  status: string;
  target: string;
}

const NO_FILTERS: Filters = { year: "", status: "", target: "" };

/**
 * Tuition fee certificates, newest first (FR-FEE-055; `fee.collect` or `finance.read`):
 * filters are a financial year, a status and a class or section (ids and codes only in the
 * query, no names), with the PDF state, print view and download on each row.
 */
export function TaxCertificatesScreen() {
  const t = useTranslations("fees.taxCertificate");
  const tn = useTranslations("school.nav");
  const tr = useTranslations("fees.receipts");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([COLLECT, FINANCE_READ]);
  const canIssue = can(COLLECT);
  const enabled = me !== undefined && allowed;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const status = useFeeStatus(enabled);
  const { sections } = useSectionOptions();
  const years = status.status === "ready" ? financialYears(status.data.financial_year) : [];
  const targets = useMemo(
    () => targetOptions(sections, (label) => t("wholeClass", { class: label })),
    [sections, t],
  );
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const poll = usePdfPoll();
  const download = useTaxDownload();
  const retry = useRetryRender();
  const list = useApiQuery(
    TAX_KEYS.list(filters, cursor),
    (context) =>
      unwrap(
        api.GET("/api/v1/fees/tax-certificates", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(cursor ? { cursor } : {}),
              ...(filters.year ? { financial_year: filters.year } : {}),
              ...(filters.status
                ? { status: filters.status as TaxCertificateListItem["status"] }
                : {}),
              ...parseTarget(filters.target),
            },
          },
          headers: pollHeaders(context),
        }),
      ),
    { enabled, refetchInterval: (page) => poll(page?.data) },
  );
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        canIssue && years.length > 0 ? (
          <TaxBulkIssueDialog years={years} sections={sections} />
        ) : null
      }
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-5">
        {header}
        <NoAccess />
      </div>
    );
  }
  function set(key: keyof Filters, value: string) {
    setFilters((f) => ({ ...f, [key]: value }));
    setCursors([null]);
  }
  function clear() {
    setFilters(NO_FILTERS);
    setCursors([null]);
  }
  const chips: FilterChip[] = [
    ...(filters.year
      ? [{ id: "year", label: t("year"), value: t("yearOption", { year: filters.year }) }]
      : []),
    ...(filters.status
      ? [
          {
            id: "status",
            label: t("statusLabel"),
            value: t(`status.${filters.status as "issued"}`),
          },
        ]
      : []),
    ...(filters.target
      ? [
          {
            id: "target",
            label: t("filterTarget"),
            value: targets.find((x) => x.value === filters.target)?.label ?? "—",
          },
        ]
      : []),
  ];
  const rows: Loadable<readonly TaxCertificateListItem[]> =
    list.status === "ready" ? { status: "ready", data: list.data.data } : list;
  const pageIndex = cursors.length - 1;
  const shown = list.status === "ready" ? list.data.data.length : 0;
  const nextCursor = list.status === "ready" ? list.data.next_cursor : null;
  const columns: ColumnV2<TaxCertificateListItem>[] = [
    {
      key: "serial",
      header: t("columns.serial"),
      className: "whitespace-nowrap",
      cell: (r) => (
        <span
          className={`num font-semibold ${r.status === "superseded" ? "text-ink-muted line-through" : ""}`}
        >
          {r.serial_number}
        </span>
      ),
    },
    {
      key: "student",
      header: t("columns.student"),
      cell: (r) => (
        <>
          {r.student_name}
          <span className="block text-xs text-ink-muted">{r.class_section}</span>
        </>
      ),
    },
    {
      key: "year",
      header: t("columns.year"),
      className: "whitespace-nowrap",
      cell: (r) => <span className="num">{r.financial_year}</span>,
    },
    {
      key: "status",
      header: t("columns.status"),
      cell: (r) => (
        <span className="flex flex-col items-start gap-1">
          <TaxStatusPill status={r.status} />
          <PdfStateText status={r.pdf_status} />
        </span>
      ),
    },
    {
      key: "receipts",
      header: t("columns.receipts"),
      numeric: true,
      cell: (r) => <span className="num">{r.receipt_count}</span>,
    },
    {
      key: "amount",
      header: t("columns.amount"),
      numeric: true,
      cell: (r) => <Amount value={r.amount} />,
    },
    {
      key: "actions",
      header: <span className="sr-only">{t("columns.actions")}</span>,
      cell: (r) => (
        <CertificateActions row={r} download={download} retry={retry} canRetry={canIssue} />
      ),
    },
  ];
  return (
    <div className="space-y-5">
      {header}
      <NotTaxAdvice />
      <ApiErrorAlert error={download.error} namespace="fees.taxCertificate" />
      <ApiErrorAlert error={retry.error} namespace="fees.taxCertificate" />
      {isFeesOff(list) ? (
        <FeesGate data={list}>{() => null}</FeesGate>
      ) : (
        <DataTableV2
          id="fees-tax-certificates"
          stacked
          caption={t("listTitle")}
          captionHidden
          density="compact"
          columns={columns}
          state={rows}
          rowKey={(r) => r.id}
          toolbar={
            <form
              role="search"
              aria-label={t("listTitle")}
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => e.preventDefault()}
            >
              <SelectField
                name="year"
                label={t("year")}
                placeholder={tr("any")}
                className="w-40"
                value={filters.year}
                onChange={(e) => set("year", e.target.value)}
                options={years.map((y) => ({ value: y, label: t("yearOption", { year: y }) }))}
              />
              <SelectField
                name="status"
                label={t("statusLabel")}
                placeholder={tr("any")}
                className="w-40"
                value={filters.status}
                onChange={(e) => set("status", e.target.value)}
                options={STATUSES.map((s) => ({ value: s, label: t(`status.${s}`) }))}
              />
              <SelectField
                name="target"
                label={t("filterTarget")}
                placeholder={tr("any")}
                className="w-56"
                value={filters.target}
                onChange={(e) => set("target", e.target.value)}
                options={targets}
              />
            </form>
          }
          chips={chips}
          onRemoveChip={(id) => set(id as keyof Filters, "")}
          onClearFilters={clear}
          emptyTitle={t("emptyTitle")}
          emptyBody={canIssue ? t("emptyBody") : t("emptyBodyReader")}
          noResultsTitle={t("noResults")}
          onRetry={() =>
            void queryClient.invalidateQueries({ queryKey: TAX_KEYS.list(filters, cursor) })
          }
          footer={
            list.status === "ready" && (shown > 0 || pageIndex > 0) ? (
              <Pagination
                from={pageIndex * PAGE_SIZE + 1}
                to={pageIndex * PAGE_SIZE + shown}
                hasPrevious={pageIndex > 0}
                hasNext={nextCursor !== null && nextCursor !== undefined}
                onPrevious={() => setCursors((c) => c.slice(0, -1))}
                onNext={() => setCursors((c) => [...c, nextCursor ?? null])}
              />
            ) : undefined
          }
        />
      )}
    </div>
  );
}

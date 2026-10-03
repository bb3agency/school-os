"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import type { Locale } from "@/i18n/routing";
import { ApiError, newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatBytes, formatCount, formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  ADMIN_KEYS,
  EXPORT_ALL,
  READ_SENSITIVE,
  isExportBusy,
  startDownload,
  useTenantExports,
  type TenantExport,
  type TenantExportStatus,
} from "./data";

/** Being made = in progress, ready = done, failed = negative; a deleted archive is a plain tag. */
const statusPill: Record<TenantExportStatus, PillVariant> = {
  queued: "progress",
  running: "progress",
  ready: "done",
  failed: "negative",
  expired: "tag",
};

/** Codes whose answer means the export itself changed: reload the list. */
const STALE = new Set(["export_not_ready", "export_failed", "export_expired"]);

function StatusPill({ status }: { status: TenantExportStatus }) {
  const t = useTranslations("admin.dataExport.status");
  return <Pill variant={statusPill[status]}>{t(status)}</Pill>;
}

function Requester({ row }: { row: TenantExport }) {
  const t = useTranslations("admin.dataExport");
  if (row.own) return <>{t("you")}</>;
  return <>{row.requested_by.display_name ?? t("formerStaff")}</>;
}

/**
 * The download button of one ready archive. The presigned link (≤ 5 minutes) is fetched
 * through the BFF when pressed and opened at once; it is never stored or logged. A 428 is
 * handled by the global step-up prompt, which retries the request.
 */
function DownloadButton({ row, onError }: { row: TenantExport; onError: (e: unknown) => void }) {
  const t = useTranslations("admin.dataExport");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const size = formatBytes(row.size_bytes, locale);

  async function download() {
    setPending(true);
    onError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/admin/tenant-export/{tenant_export_id}/download-url", {
          params: { path: { tenant_export_id: row.id } },
        }),
      );
      startDownload(link.url);
    } catch (failure) {
      onError(failure);
      if (failure instanceof ApiError && failure.code && STALE.has(failure.code)) {
        void queryClient.invalidateQueries({ queryKey: ADMIN_KEYS.exports });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <Button
      variant="primary"
      onClick={() => void download()}
      disabled={pending}
      aria-disabled={pending || undefined}
    >
      {pending ? tc("working") : size ? t("downloadSized", { size }) : t("download")}
    </Button>
  );
}

/** The newest export: what is happening now and, while the archive exists, its download. */
function Latest({ row }: { row: TenantExport }) {
  const t = useTranslations("admin.dataExport");
  const tfail = useTranslations("admin.dataExport.failure");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const [error, setError] = useState<unknown>(undefined);
  const count = (value: number) => formatCount(value, locale) ?? String(value);

  let state: ReactNode;
  if (isExportBusy(row.status)) {
    state = (
      <Alert tone="info" title={t(row.status === "queued" ? "queuedTitle" : "runningTitle")}>
        {t("busyBody")}
      </Alert>
    );
  } else if (row.status === "failed") {
    state = (
      <Alert tone="danger" title={t("failedTitle")}>
        {translateOr(tfail, row.error_code ?? "other", "other")}
      </Alert>
    );
  } else if (row.status === "expired" || !row.can_download) {
    state = (
      <Alert tone="warning" title={t("expiredTitle")}>
        {t("expiredBody")}
      </Alert>
    );
  } else {
    state = (
      <div className="space-y-3">
        <Alert tone="success" title={t("readyTitle")}>
          <p>
            {t("readyBody", {
              expires: formatDateTime(row.expires_at) ?? "",
            })}
          </p>
        </Alert>
        <p className="text-sm">{tc("stepUpNote")}</p>
        <div className="flex flex-wrap items-center gap-3">
          <DownloadButton row={row} onError={setError} />
        </div>
        <p className="text-xs text-ink-muted">{t("linkNote")}</p>
        <ApiErrorAlert error={error} namespace="admin" />
      </div>
    );
  }

  return (
    <Card title={t("latestTitle")} actions={<StatusPill status={row.status} />}>
      <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {t("statusNow", { status: t(`status.${row.status}`) })}
      </p>
      {state}
      {row.counts ? (
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label={t("counts.students")}>{count(row.counts.tables?.students ?? 0)}</Fact>
          <Fact label={t("counts.tables")}>
            {count(Object.keys(row.counts.tables ?? {}).length)}
          </Fact>
          <Fact label={t("counts.documents")}>{count(row.counts.documents)}</Fact>
          <Fact label={t("counts.auditEvents")}>{count(row.counts.audit_events)}</Fact>
        </dl>
      ) : null}
    </Card>
  );
}

function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1 rounded-lg border border-border bg-surface-muted px-4 py-3">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="font-semibold break-words text-ink">{children}</dd>
    </div>
  );
}

/** "Make a full export": what it holds, the restricted-details choice and the request. */
function RequestCard({ busy }: { busy: boolean }) {
  const t = useTranslations("admin.dataExport");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const sensitiveId = useId();
  const [includeSensitive, setIncludeSensitive] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [requested, setRequested] = useState(false);
  const mayIncludeSensitive = can(READ_SENSITIVE);

  async function request() {
    setPending(true);
    setError(undefined);
    setRequested(false);
    try {
      await unwrap(
        api.POST("/api/v1/admin/tenant-export", {
          headers: { "Idempotency-Key": newIdempotencyKey() },
          body: { include_sensitive: includeSensitive && mayIncludeSensitive },
        }),
      );
      setRequested(true);
      await queryClient.invalidateQueries({ queryKey: ADMIN_KEYS.exports });
    } catch (failure) {
      setError(failure);
      if (failure instanceof ApiError && failure.code === "tenant_export_in_progress") {
        void queryClient.invalidateQueries({ queryKey: ADMIN_KEYS.exports });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title={t("requestTitle")} description={t("requestDescription")}>
      <div className="space-y-4">
        <ul className="list-disc space-y-1 ps-5 text-sm text-ink">
          <li>{t("includes.records")}</li>
          <li>{t("includes.documents")}</li>
          <li>{t("includes.audit")}</li>
          <li>{t("includes.never")}</li>
        </ul>
        {mayIncludeSensitive ? (
          <div className="max-w-2xl space-y-1 rounded-lg border border-border bg-surface-muted p-4">
            <label className="flex min-h-8 cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={includeSensitive}
                onChange={(event) => setIncludeSensitive(event.target.checked)}
                aria-describedby={`${sensitiveId}-hint`}
                className="mt-1 size-4 shrink-0 accent-primary"
              />
              <span className="font-semibold text-ink">{t("sensitiveLabel")}</span>
            </label>
            <p id={`${sensitiveId}-hint`} className="ps-7 text-sm text-ink-muted">
              {t("sensitiveHint")}
            </p>
          </div>
        ) : (
          <p className="text-sm text-ink-muted">{t("sensitiveNotAllowed")}</p>
        )}
        <Alert tone="warning" title={t("careTitle")}>
          {t("careBody")}
        </Alert>
        <p className="text-sm">{tc("stepUpNote")}</p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => void request()}
            disabled={pending || busy}
            aria-disabled={pending || busy || undefined}
          >
            {pending ? tc("working") : t("requestButton")}
          </Button>
          {busy ? <span className="text-sm text-ink-muted">{t("busyNote")}</span> : null}
        </div>
        {requested ? (
          <Alert tone="success" live>
            {t("requested")}
          </Alert>
        ) : null}
        <ApiErrorAlert error={error} namespace="admin" />
      </div>
    </Card>
  );
}

function History({ rows }: { rows: readonly TenantExport[] }) {
  const t = useTranslations("admin.dataExport");
  const locale = useLocale() as Locale;
  const [error, setError] = useState<unknown>(undefined);
  if (rows.length === 0) return null;
  return (
    <Card title={t("historyTitle")} description={t("historyDescription")}>
      <TableScroll label={t("historyTable")}>
        <Table>
          <THead>
            <Tr>
              <Th>{t("columns.requested")}</Th>
              <Th>{t("columns.by")}</Th>
              <Th>{t("columns.status")}</Th>
              <Th>{t("columns.restricted")}</Th>
              <Th>{t("columns.size")}</Th>
              <Th>{t("columns.until")}</Th>
              <Th>
                <span className="sr-only">{t("columns.actions")}</span>
              </Th>
            </Tr>
          </THead>
          <TBody>
            {rows.map((row) => (
              <Tr key={row.id}>
                <Td>
                  <Value>{formatDateTime(row.created_at)}</Value>
                </Td>
                <Td>
                  <Requester row={row} />
                </Td>
                <Td>
                  <StatusPill status={row.status} />
                </Td>
                <Td>{row.include_sensitive ? t("restrictedYes") : t("restrictedNo")}</Td>
                <Td>
                  <Value>{formatBytes(row.size_bytes, locale)}</Value>
                </Td>
                <Td>
                  <Value>{formatDateTime(row.expires_at)}</Value>
                </Td>
                <Td>{row.can_download ? <DownloadButton row={row} onError={setError} /> : null}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
      <ApiErrorAlert error={error} namespace="admin" className="mt-4" />
    </Card>
  );
}

/**
 * The school's full data export (US-1201 AC1, FR-ADM-001): the owner asks for it (a fresh MFA
 * sign-in is asked for), follows it while it is made, and downloads it for 24 hours. History
 * shows who exported everything and when. Restricted details only when explicitly included.
 */
export function DataExportScreen() {
  const t = useTranslations("admin.dataExport");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(EXPORT_ALL);
  const list = useTenantExports(undefined, allowed);

  let body: ReactNode;
  if (!meLoaded) body = <LoadingState label={tc("loading")} />;
  else if (!allowed) {
    body = (
      <Alert tone="info" title={t("ownerOnlyTitle")}>
        {t("ownerOnlyBody")}
      </Alert>
    );
  } else if (list.status === "loading") body = <LoadingState label={tc("loading")} />;
  else if (list.status === "unavailable") {
    body = (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  } else if (list.status === "error") {
    body = (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {list.reason ? te(`load.${list.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  } else {
    const rows = list.data.data;
    const latest = rows[0];
    body = (
      <>
        <RequestCard busy={latest !== undefined && isExportBusy(latest.status)} />
        {latest ? <Latest row={latest} /> : null}
        <History rows={rows} />
      </>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {body}
    </div>
  );
}

"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card, cardClasses } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { useRetentionDays } from "@/features/admin/data";
import { attributeLabel } from "@/features/findings/data";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatBytes, formatCount, formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  EXPORT_KEYS,
  startDownload,
  useExport,
  useExportAttributes,
  useExportProfiles,
} from "./data";
import { ExportStatusBadge, exportTitle, RequesterName, ScopeSummary } from "./parts";
import {
  EXPORT_PERM,
  exportKeptDays,
  isStructureColumn,
  PROFILE_PERMISSION,
  type Export,
  type FileFormat,
} from "./types";

/** Download needs a fresh MFA sign-in (ADR-0021): someone else's, student lists, restricted. */
export function downloadNeedsStepUp(row: Pick<Export, "own" | "kind" | "include_sensitive">) {
  return !row.own || row.kind === "student_list" || row.include_sensitive;
}

/** Codes whose answer means the export itself changed: reload it. */
const STALE = new Set(["export_not_ready", "export_failed", "export_expired"]);

function Item({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="font-semibold text-ink">{children}</dd>
    </div>
  );
}

/**
 * Download buttons, one per file. The presigned link (≤ 5 minutes) is fetched through the BFF
 * when the button is pressed and opened at once; it is never stored or logged. A 428 is
 * handled by the global step-up prompt, which retries the request.
 */
function Downloads({ row }: { row: Export }) {
  const t = useTranslations("exports.detail");
  const tf = useTranslations("exports.format");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<FileFormat | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  // Guard against a body without the arrays: a missing list means "no files yet", not a crash.
  const listed = row.files ?? [];
  const files =
    listed.length > 0
      ? listed
      : (row.formats ?? []).map((format) => ({ format, content_type: "", size_bytes: -1 }));

  async function download(format: FileFormat) {
    setPending(format);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/exports/{export_id}/download-url", {
          params: { path: { export_id: row.id }, query: { format } },
        }),
      );
      startDownload(link.url);
    } catch (failure) {
      setError(failure);
      if (failure instanceof ApiError && failure.code && STALE.has(failure.code)) {
        void queryClient.invalidateQueries({ queryKey: EXPORT_KEYS.all });
      }
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="space-y-3">
      {downloadNeedsStepUp(row) ? <p className="text-sm">{tc("stepUpNote")}</p> : null}
      <ul className="grid gap-3 sm:grid-cols-2">
        {files.map((file) => {
          const size = formatBytes(file.size_bytes, locale);
          return (
            <li
              key={file.format}
              className={`${cardClasses({ padding: "sm", tone: "outline" })} flex flex-wrap items-center gap-3`}
            >
              <span
                aria-hidden="true"
                className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary"
              >
                <Icon name="file" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block font-semibold text-ink">{tf(file.format)}</span>
                {size ? (
                  <span className="block font-mono text-xs text-ink-muted">{size}</span>
                ) : null}
              </span>
              <Button
                variant="primary"
                onClick={() => void download(file.format)}
                disabled={pending !== null}
                aria-disabled={pending !== null || undefined}
              >
                {pending === file.format
                  ? tc("working")
                  : `${t("download", { format: tf(file.format) })}${size ? ` (${size})` : ""}`}
              </Button>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-ink-muted">{t("linkNote")}</p>
      <ApiErrorAlert error={error} namespace="exports" />
    </div>
  );
}

function MakeNew({ row }: { row: Export }) {
  const t = useTranslations("exports.detail");
  const can = useStaffCan();
  if (row.kind === "student_list") {
    return can(EXPORT_PERM.studentList) ? (
      <Link href="/exports/new/student-list" className="font-semibold underline">
        {t("makeNew")}
      </Link>
    ) : null;
  }
  const permission = PROFILE_PERMISSION[row.kind === "board_precheck" ? "board" : "portal"];
  if (!can(permission)) return null;
  const href = row.profile_key
    ? `/exports/new/precheck?profile=${encodeURIComponent(row.profile_key)}`
    : "/exports/new/precheck";
  return (
    <Link href={href} className="font-semibold underline">
      {t("makeNew")}
    </Link>
  );
}

function FilesSection({ row }: { row: Export }) {
  const t = useTranslations("exports.detail");
  const tfail = useTranslations("exports.failure");
  if (row.status === "queued" || row.status === "running") {
    return (
      <Alert tone="info" title={t(row.status === "queued" ? "queuedTitle" : "runningTitle")}>
        {t("busyBody")}
      </Alert>
    );
  }
  if (row.status === "failed") {
    return (
      <Alert tone="danger" title={t("failedTitle")}>
        <p>{translateOr(tfail, row.error_code ?? "other", "other")}</p>
        <p className="mt-2">
          <MakeNew row={row} />
        </p>
      </Alert>
    );
  }
  if (row.status === "expired") {
    return (
      <Alert tone="warning" title={t("expiredTitle")}>
        <p>{t("expiredBody")}</p>
        <p className="mt-2">
          <MakeNew row={row} />
        </p>
      </Alert>
    );
  }
  if (!row.can_download) {
    return row.own ? (
      <Alert tone="warning" title={t("noLongerTitle")}>
        {t("noLongerBody")}
      </Alert>
    ) : (
      <Alert tone="info" title={t("notOwnTitle")}>
        <p>{t("notOwnBody")}</p>
        <p className="mt-2">
          <MakeNew row={row} />
        </p>
      </Alert>
    );
  }
  return <Downloads row={row} />;
}

/** One export: status, who asked for it, what it covers and its files (ADR-0021). */
export function ExportDetailScreen({ exportId }: { exportId: string }) {
  const t = useTranslations("exports");
  const td = useTranslations("exports.detail");
  const tk = useTranslations("exports.kind");
  const tf = useTranslations("exports.format");
  const tl = useTranslations("exports.language");
  const telugu = useTeluguEnabled();
  const tstatus = useTranslations("exports.status");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const state = useExport(exportId);
  const profiles = useExportProfiles(can([EXPORT_PERM.board, EXPORT_PERM.portal]));
  const attributes = useExportAttributes(
    state.status === "ready" && state.data.kind === "student_list" && can(EXPORT_PERM.readBasic),
  );
  // FR-ADM-002: the period this export is kept for comes from its own dates once it is ready;
  // before that, from the school's setting (only readable with tenant.settings.manage).
  const ownDays = state.status === "ready" ? exportKeptDays(state.data) : null;
  const schoolDays = useRetentionDays("exports", state.status === "ready" && ownDays === null);
  const keptDays = ownDays ?? schoolDays;

  const back = (
    <Link href="/exports" className="text-primary underline">
      {td("back")}
    </Link>
  );

  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: t("title"), href: "/exports" },
  ];

  if (state.status === "loading") return <LoadingState label={tc("loading")} />;
  if (state.status !== "ready") {
    const missing = state.status === "error" && state.reason === "not_found";
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} breadcrumb={[...crumbs, { label: td("title") }]} />
        <Alert
          tone={missing ? "warning" : "danger"}
          title={td(missing ? "notFoundTitle" : "loadErrorTitle")}
        >
          <p>{td(missing ? "notFoundBody" : "loadErrorBody")}</p>
        </Alert>
        {back}
      </div>
    );
  }

  const row = state.data;
  const title = exportTitle(row, profiles.data, locale, (kind) => tk(kind));
  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const columnName = (key: string) =>
    isStructureColumn(key)
      ? t(`columns.${key}`)
      : (attributeLabel(attributes.data, key, locale) ?? key);

  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        breadcrumb={[...crumbs, { label: title }]}
        badge={<ExportStatusBadge status={row.status} />}
      />
      <Card title={td("filesTitle")}>
        {/* Announces status changes while polling; the buttons stay outside the live region. */}
        <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          {td("statusNow", { status: tstatus(row.status) })}
        </p>
        <FilesSection row={row} />
      </Card>
      <Card title={td("aboutTitle")}>
        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Item label={td("requestedBy")}>
            <RequesterName row={row} />
          </Item>
          <Item label={td("created")}>
            <Value>{formatDateTime(row.created_at)}</Value>
          </Item>
          <Item label={td("finished")}>
            <Value>{formatDateTime(row.finished_at)}</Value>
          </Item>
          <Item label={row.status === "expired" ? td("deletedOn") : td("expires")}>
            <Value>{formatDateTime(row.expires_at)}</Value>
          </Item>
          <Item label={td("students")}>{count(row.student_count)}</Item>
          <Item label={td("scope")}>
            <ScopeSummary scope={row.scope} />
          </Item>
          <Item label={td("formats")}>
            {(row.formats ?? []).map((format) => tf(format)).join(", ")}
          </Item>
          {telugu ? <Item label={td("language")}>{tl(row.language)}</Item> : null}
          <Item label={td("restricted")}>
            {row.include_sensitive ? td("restrictedYes") : td("restrictedNo")}
          </Item>
        </dl>
        {row.columns && row.columns.length > 0 ? (
          <div className="mt-4 space-y-1">
            <h3 className="text-sm text-ink-muted">{td("columns")}</h3>
            <ul className="flex flex-wrap gap-1.5">
              {row.columns.map((key) => (
                <li key={key}>
                  <Pill variant="tag">{columnName(key)}</Pill>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="mt-4 text-sm text-ink-muted">
          {keptDays === null
            ? td("retentionNoteSchoolPeriod")
            : td("retentionNote", { days: keptDays })}
        </p>
      </Card>
    </div>
  );
}

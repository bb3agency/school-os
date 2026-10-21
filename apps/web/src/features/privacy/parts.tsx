"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { exportRefetchInterval, startDownload } from "@/features/exports/data";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatDate } from "@/lib/format";
import {
  PRIVACY_KEYS,
  type ErasurePlanRow,
  type PrivacyFile,
  type RequestStatus,
  type TemplateKey,
} from "./types";

const STATUS_VARIANTS: Record<RequestStatus, PillVariant> = {
  received: "progress",
  in_progress: "progress",
  waiting_for_requester: "review",
  decided: "done",
  closed: "tag",
  withdrawn: "tag",
};

/** A request's state in words (colour only repeats the text, WCAG 1.4.1). */
export function RequestStatusPill({ status }: { status: RequestStatus }) {
  const t = useTranslations("privacy.status");
  return <Pill variant={STATUS_VARIANTS[status]}>{t(status)}</Pill>;
}

/**
 * Due date with the days left as a figure ("2 days left"), or "Overdue by 1 day" in the danger
 * pill. Closed requests show only the date.
 */
export function DueCell({
  dueOn,
  daysLeft,
  overdue,
  open,
}: {
  dueOn: string;
  daysLeft: number;
  overdue: boolean;
  open: boolean;
}) {
  const t = useTranslations("privacy.due");
  const date = <span className="num whitespace-nowrap">{formatDate(dueOn)}</span>;
  if (!open) return date;
  return (
    <span className="flex flex-col items-start gap-1">
      {date}
      {overdue ? (
        <Pill variant="negative" size="sm">
          <span className="num">{t("overdue", { count: Math.abs(daysLeft) })}</span>
        </Pill>
      ) : (
        <span className="num text-sm text-ink-muted">
          {daysLeft === 0 ? t("today") : t("left", { count: daysLeft })}
        </span>
      )}
    </span>
  );
}

const OUTCOME_VARIANTS: Record<ErasurePlanRow["outcome"], PillVariant> = {
  erasable: "positive",
  refused_official_record: "tag",
  refused_legal_obligation: "tag",
  retention_running: "tag",
};

/** "Can be erased" / "Kept: school record" / "Kept: required by law" / "Kept until 31/03/2035". */
export function OutcomePill({ row }: { row: ErasurePlanRow }) {
  const t = useTranslations("privacy.erasure.outcome");
  const label =
    row.outcome === "retention_running"
      ? row.until
        ? t("until", { date: formatDate(row.until) ?? row.until })
        : t("retention_running")
      : t(row.outcome);
  return (
    <Pill variant={OUTCOME_VARIANTS[row.outcome]} size="sm">
      <span className="num">{label}</span>
    </Pill>
  );
}

/** GET /privacy/files/{id}; polls while the worker makes the PDF (not in a hidden tab). */
function usePrivacyFile(exportId: string) {
  const api = useBffClient("staff");
  return useQuery({
    queryKey: PRIVACY_KEYS.file(exportId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/privacy/files/{export_id}", {
          params: { path: { export_id: exportId } },
        }),
      ),
    refetchInterval: (query) =>
      exportRefetchInterval(
        query.state.data ? [query.state.data] : undefined,
        query.state.dataUpdateCount,
        query.state.status === "error",
      ),
    refetchIntervalInBackground: false,
    retry: false,
  });
}

/**
 * "Download PDF" of a notice template: POST makes the PDF (202), the file is polled until it is
 * ready, then the presigned link is fetched when the button is pressed and opened at once
 * (never kept).
 */
export function NoticePdfButton({
  templateKey,
  label,
}: {
  templateKey: TemplateKey;
  label: string;
}) {
  const t = useTranslations("privacy.pdf");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [file, setFile] = useState<PrivacyFile | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  async function make() {
    setPending(true);
    setError(undefined);
    try {
      const made = await unwrap(
        api.POST("/api/v1/privacy/templates/{key}/pdf", {
          params: { path: { key: templateKey } },
        }),
      );
      setFile(made);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <span className="flex flex-col items-start gap-1">
      {file ? (
        <FileStatus exportId={file.export_id} label={label} onRestart={() => void make()} />
      ) : (
        <Button variant="secondary" size="sm" disabled={pending} onClick={() => void make()}>
          {pending ? tc("working") : t("make")}
          <span className="sr-only">: {label}</span>
        </Button>
      )}
      <ApiErrorAlert error={error} namespace="privacy" />
    </span>
  );
}

function FileStatus({
  exportId,
  label,
  onRestart,
}: {
  exportId: string;
  label: string;
  onRestart: () => void;
}) {
  const t = useTranslations("privacy.pdf");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const file = usePrivacyFile(exportId);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  async function download() {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/privacy/files/{export_id}/download-url", {
          params: { path: { export_id: exportId } },
        }),
      );
      startDownload(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const status = file.data?.status;
  if (file.isError || status === "failed" || status === "expired") {
    return (
      <span className="flex flex-wrap items-center gap-2 text-sm">
        <span>{status === "expired" ? t("expired") : t("failed")}</span>
        <Button variant="secondary" size="sm" onClick={onRestart}>
          {t("again")}
          <span className="sr-only">: {label}</span>
        </Button>
      </span>
    );
  }
  if (status !== "ready") {
    return (
      <span role="status" className="text-sm text-ink-muted">
        {t("making")}
      </span>
    );
  }
  return (
    <span className="flex flex-col items-start gap-1">
      <Button variant="secondary" size="sm" disabled={pending} onClick={() => void download()}>
        {pending ? tc("working") : t("download")}
        <span className="sr-only">: {label}</span>
      </Button>
      <ApiErrorAlert error={error} namespace="privacy" />
    </span>
  );
}

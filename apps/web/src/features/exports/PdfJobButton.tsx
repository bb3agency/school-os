"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { EXPORT_KEYS, startDownload, useExport } from "./data";
import type { ConsentFormsCreate, Export, ReadinessSlipsCreate } from "./types";

/** What to print: parent verification slips (FR-DQ-047) or APAAR consent forms (FR-APC-007). */
export type PdfJobRequest =
  | { kind: "readiness_slips"; body: ReadinessSlipsCreate }
  | { kind: "apaar_consent_forms"; body: ConsentFormsCreate };

/**
 * The PDF of a print job: polls the export while the worker makes it, then offers the
 * download. The presigned link is fetched when the button is pressed and opened at once (never
 * kept). A 428 is handled by the global step-up prompt, which retries the request.
 */
function PdfJobStatus({ exportId, onRestart }: { exportId: string; onRestart: () => void }) {
  const t = useTranslations("exports.pdfJob");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const job = useExport(exportId);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  async function download(row: Export) {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/exports/{export_id}/download-url", {
          params: { path: { export_id: row.id }, query: { format: "pdf" } },
        }),
      );
      startDownload(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  if (job.status === "error" || job.status === "unavailable") {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span>{t("lost")}</span>
        <Button variant="secondary" size="sm" onClick={onRestart}>
          {t("again")}
        </Button>
      </span>
    );
  }
  const row = job.status === "ready" ? job.data : null;
  if (!row || row.status === "queued" || row.status === "running") {
    return (
      <span role="status" className="text-sm text-ink-muted">
        {t("making")}
      </span>
    );
  }
  if (row.status !== "ready") {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span role="status">{row.status === "expired" ? t("expired") : t("failed")}</span>
        <Button variant="secondary" size="sm" onClick={onRestart}>
          {t("again")}
        </Button>
      </span>
    );
  }
  return (
    <span className="flex flex-col gap-2">
      <span className="flex flex-wrap items-center gap-2">
        <span role="status" className="sr-only">
          {t("ready")}
        </span>
        <Button
          variant="primary"
          size="sm"
          onClick={() => void download(row)}
          disabled={pending}
          aria-disabled={pending || undefined}
          loading={pending}
        >
          {pending ? tc("working") : t("download")}
        </Button>
        {row.include_sensitive ? <span className="text-xs">{tc("stepUpNote")}</span> : null}
      </span>
      <ApiErrorAlert error={error} namespace="exports" />
    </span>
  );
}

/**
 * "Make PDF" for a print job (R1 leftovers; FR-DQ-047, FR-APC-007): asks the server to print
 * the same pages as the print view to a PDF in the background, then shows its progress and the
 * download. Every export needs a recent MFA sign-in; the global step-up prompt asks for it.
 */
export function PdfJobButton({
  request,
  label,
  describedBy,
}: {
  request: PdfJobRequest;
  /** Visible label, e.g. "Make PDF of slips". */
  label: string;
  /** Id of the text naming what is printed (screen readers: which section or student). */
  describedBy?: string;
}) {
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const tc = useTranslations("common");
  const [exportId, setExportId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  async function start() {
    setPending(true);
    setError(undefined);
    const headers = { "Idempotency-Key": newIdempotencyKey() };
    try {
      const created =
        request.kind === "readiness_slips"
          ? await unwrap(
              api.POST("/api/v1/exports/readiness-slips", { headers, body: request.body }),
            )
          : await unwrap(
              api.POST("/api/v1/exports/apaar-consent-forms", { headers, body: request.body }),
            );
      setExportId(created.id);
      void queryClient.invalidateQueries({ queryKey: EXPORT_KEYS.all });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  if (exportId) {
    return <PdfJobStatus exportId={exportId} onRestart={() => setExportId(null)} />;
  }
  return (
    <span className="flex flex-col gap-2">
      <Button
        variant="secondary"
        size="sm"
        onClick={() => void start()}
        disabled={pending}
        aria-disabled={pending || undefined}
        aria-describedby={describedBy}
        loading={pending}
      >
        {pending ? tc("working") : label}
      </Button>
      <ApiErrorAlert error={error} namespace="exports" />
    </span>
  );
}

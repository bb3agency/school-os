"use client";

import type { Invoice } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { startDownload } from "@/features/exports/data";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";

/** Conflicts the invoice API explains with its own codes (docs/16 §5.8). */
const KNOWN = new Set(["invoice_pdf_pending", "invoice_draft"]);

/**
 * "Download PDF" for a numbered invoice: GET /platform/invoices/{id}/download-url
 * (platform.invoices.read; audited as invoice.pdf_downloaded) and open the short-lived link
 * once. The link is never kept in state or logs.
 */
export function InvoiceDownload({ invoice, label }: { invoice: Invoice; label: string }) {
  const t = useTranslations("platform.invoices");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  if (!invoice.invoice_number) return null;

  async function download() {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/platform/invoices/{invoice_id}/download-url", {
          params: { path: { invoice_id: invoice.id } },
        }),
      );
      startDownload(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const known =
    error instanceof ApiError && error.code && KNOWN.has(error.code)
      ? (error.code as "invoice_pdf_pending" | "invoice_draft")
      : null;

  return (
    <span className="relative flex flex-col items-start gap-1">
      <Button
        variant="secondary"
        size="sm"
        onClick={() => void download()}
        disabled={pending}
        aria-disabled={pending || undefined}
        aria-label={t("downloadFor", { number: label })}
      >
        <Icon name="arrowDown" className="size-4" />
        {pending ? tc("working") : t("download")}
      </Button>
      {known ? (
        <Alert tone="info" live>
          {t(`downloadErrors.${known}`)}
        </Alert>
      ) : (
        <ApiErrorAlert error={error} />
      )}
    </span>
  );
}

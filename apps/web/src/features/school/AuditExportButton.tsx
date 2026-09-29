"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { downloadSheet } from "@/features/sheets/download";
import { auditQuery, type AuditFilters } from "./audit-filters";

/**
 * FR-AUD-005 / US-1001: "Download CSV" for the school audit log. Sends the filters shown on
 * the page to `GET /audit/export` through the BFF (the file holds exactly the events they
 * select, oldest first) and saves the file the API streams. The API wants a recent MFA
 * sign-in for every export (docs/07 §5.2): a 428 goes through the page's "Confirm it's you"
 * prompt and the request is sent once more. The API records every download (`audit.exported`).
 * Nothing is kept in browser storage.
 */
export function AuditExportButton({ filters }: { filters: AuditFilters }) {
  const t = useTranslations("school.audit");
  const locale = useLocale();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(undefined);
    setSaved(null);
    try {
      const search = new URLSearchParams(Object.entries(auditQuery(filters))).toString();
      const query = search ? `?${search}` : "";
      const name = await downloadSheet({
        path: `/api/v1/audit/export${query}`,
        method: "GET",
        locale,
        fallbackName: "audit-log.csv",
      });
      setSaved(name);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex flex-col items-start gap-2">
      <Button
        variant="secondary"
        onClick={() => void download()}
        disabled={busy}
        aria-describedby="audit-export-note"
      >
        <Icon name="arrowDown" className="size-4" />
        {busy ? t("exporting") : t("exportCsv")}
      </Button>
      <span id="audit-export-note" className="sr-only">
        {t("exportNote")}
      </span>
      <span role="status" aria-live="polite" className="text-xs text-ink-muted">
        {saved ? t("exported", { name: saved }) : ""}
      </span>
      <ApiErrorAlert error={error} namespace="school.audit" className="max-w-sm" />
    </span>
  );
}

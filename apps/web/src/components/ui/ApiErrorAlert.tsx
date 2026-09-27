"use client";

import { useTranslations } from "next-intl";
import { describeApiError } from "@/lib/api-errors";
import { Alert } from "./Alert";

/**
 * Plain-language explanation of a failed BFF call (en/te), with the request id so support
 * can find it. Shows nothing when there is no error. While the page is leaving for sign-in
 * or step-up MFA (401/428) it says so instead of showing an error.
 */
export function ApiErrorAlert({ error, className }: { error: unknown; className?: string }) {
  const t = useTranslations("errors.api");
  if (error === undefined || error === null) return null;
  const described = describeApiError(error);
  if (described.kind === "redirecting") {
    return (
      <Alert tone="info" live className={className}>
        {t("redirecting")}
      </Alert>
    );
  }
  if (described.kind === "unavailable") {
    return (
      <Alert tone="info" live className={className}>
        {t("unavailable")}
      </Alert>
    );
  }
  return (
    <Alert tone="danger" live title={t(`${described.key}.title`)} className={className}>
      <p>{t(`${described.key}.body`)}</p>
      {described.requestId ? (
        <p className="mt-1 text-xs">{t("reference", { id: described.requestId })}</p>
      ) : null}
    </Alert>
  );
}

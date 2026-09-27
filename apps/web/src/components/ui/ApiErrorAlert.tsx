"use client";

import { useTranslations } from "next-intl";
import { describeApiError } from "@/lib/api-errors";
import { ApiError } from "@/lib/bff/query";
import { Alert } from "./Alert";

/**
 * Message namespaces that carry their own `<namespace>.errors.<code>.{title,body}` for the
 * problem codes of one feature (e.g. `changeRequests.errors.self_approval_forbidden`). Codes
 * without such a message fall back to `errors.api.*`.
 */
export type ErrorNamespace = "findings" | "changeRequests" | "breakGlass" | "notifications";

type LooseTranslator = ((key: string) => string) & { has: (key: string) => boolean };

/**
 * Plain-language explanation of a failed BFF call (en/te), with the request id so support
 * can find it. Shows nothing when there is no error. While the page is leaving for sign-in
 * or step-up MFA (401/428) it says so instead of showing an error.
 */
export function ApiErrorAlert({
  error,
  className,
  namespace,
}: {
  error: unknown;
  className?: string;
  /** Look the problem code up in this feature's messages first. */
  namespace?: ErrorNamespace | undefined;
}) {
  const t = useTranslations("errors.api");
  const root = useTranslations() as unknown as LooseTranslator;
  if (error === undefined || error === null) return null;
  const own =
    namespace && error instanceof ApiError && error.code
      ? `${namespace}.errors.${error.code}`
      : null;
  if (own && error instanceof ApiError && root.has(`${own}.title`) && root.has(`${own}.body`)) {
    const requestId = error.problem.request_id;
    return (
      <Alert tone="danger" live title={root(`${own}.title`)} className={className}>
        <p>{root(`${own}.body`)}</p>
        {typeof requestId === "string" ? (
          <p className="mt-1 text-xs">{t("reference", { id: requestId })}</p>
        ) : null}
      </Alert>
    );
  }
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

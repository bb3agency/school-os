"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { ApiError } from "@/lib/bff/query";

/** Message namespaces that hold `<code>.title` / `<code>.body` for feature-specific codes. */
export type ProblemNamespace =
  "students.errors" | "imports.errors" | "extraction.errors" | "sheets.errors";

type Loose = ((key: string, values?: Record<string, string | number>) => string) & {
  has: (key: string) => boolean;
};

/** An error with a feature code that is not an API problem (e.g. an upload that timed out). */
export interface CodedError {
  code: string;
}

function codeOf(error: unknown): string | undefined {
  if (error instanceof ApiError) return error.code;
  if (error instanceof Error && "code" in error && typeof error.code === "string") {
    return error.code;
  }
  return undefined;
}

/**
 * A failed call explained in plain language (en/te). Codes these features know (for example
 * `identity_change_required`, `revert_window_closed`, `file_too_large`) get their own message
 * that says how to fix it; everything else falls back to the shared <ApiErrorAlert>. Never
 * shows raw API text. With several namespaces the first that knows the code wins.
 */
export function ProblemAlert({
  error,
  namespace,
  action,
  className,
}: {
  error: unknown;
  namespace: ProblemNamespace | readonly ProblemNamespace[];
  /** Extra content under the message, e.g. a link to the correct workflow. */
  action?: ReactNode;
  className?: string;
}) {
  const ts = useTranslations("students.errors") as unknown as Loose;
  const ti = useTranslations("imports.errors") as unknown as Loose;
  const tx = useTranslations("extraction.errors") as unknown as Loose;
  const tsh = useTranslations("sheets.errors") as unknown as Loose;
  const tapi = useTranslations("errors.api");
  if (error === undefined || error === null) return null;
  const code = codeOf(error);
  const tables: Record<ProblemNamespace, Loose> = {
    "students.errors": ts,
    "imports.errors": ti,
    "extraction.errors": tx,
    "sheets.errors": tsh,
  };
  const namespaces: readonly ProblemNamespace[] =
    typeof namespace === "string" ? [namespace] : namespace;
  const t = code
    ? namespaces.map((ns) => tables[ns]).find((table) => table.has(`${code}.title`))
    : undefined;
  if (t && code) {
    const requestId =
      error instanceof ApiError && typeof error.problem.request_id === "string"
        ? error.problem.request_id
        : "";
    return (
      <Alert tone="danger" live title={t(`${code}.title`)} className={className}>
        <p>{t(`${code}.body`)}</p>
        {action ? <div className="mt-2">{action}</div> : null}
        {requestId ? <p className="mt-1 text-xs">{tapi("reference", { id: requestId })}</p> : null}
      </Alert>
    );
  }
  return <ApiErrorAlert error={error} {...(className ? { className } : {})} />;
}

/** The code of a failed call, if any (to decide on an extra action link). */
export function problemCode(error: unknown): string | undefined {
  return codeOf(error);
}

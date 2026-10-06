"use client";

import type { AuditVerify } from "@schoolos/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { describeApiError } from "@/lib/api-errors";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";

export type AuditVerifyResult = AuditVerify;

export const AUDIT_READ = "audit.read";
export const AUDIT_VERIFY_KEY = ["staff", "audit-verify"] as const;
/** How often the page looks for the result while a check is queued. */
const PENDING_POLL_MS = 5_000;

/**
 * The school's audit chain (US-1001 AC2, FR-AUD-003, FR-AUD-005; audit 2026-10-06 R-19).
 * GET /audit/verify returns the latest STORED check (the nightly job, or a check someone asked
 * for): it no longer re-reads the whole log. "Check again" queues a new check (POST, at most
 * once per school every 10 minutes); the page shows it as queued and polls until it is done.
 */
export function AuditVerifyScreen() {
  const t = useTranslations("school.audit.integrity");
  const ta = useTranslations("school.audit");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const api = useBffClient("staff");
  const client = useQueryClient();
  const allowed = can(AUDIT_READ);
  const [requestError, setRequestError] = useState<unknown>(null);
  const [requesting, setRequesting] = useState(false);
  const query = useQuery({
    queryKey: AUDIT_VERIFY_KEY,
    queryFn: () => unwrap(api.GET("/api/v1/audit/verify")),
    enabled: allowed,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (state) => (state.state.data?.pending ? PENDING_POLL_MS : false),
  });

  const back = (
    <Link href="/audit" className="text-primary underline">
      {t("back")}
    </Link>
  );

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="warning" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }

  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const result = query.data;

  async function checkAgain() {
    setRequesting(true);
    setRequestError(null);
    try {
      const queued = await unwrap(api.POST("/api/v1/audit/verify", { body: { full: false } }));
      client.setQueryData(AUDIT_VERIFY_KEY, queued);
    } catch (error) {
      setRequestError(error);
    } finally {
      setRequesting(false);
    }
  }

  const cooling = requestError ? describeApiError(requestError) : null;
  const coolDown =
    cooling?.kind === "api" && cooling.key === "rate_limited"
      ? Math.max(1, Math.ceil((cooling.retryAfter ?? 600) / 60))
      : null;

  const state: "loading" | "idle" | "pending" | "ok" | "broken" = query.isPending
    ? "loading"
    : !result || result.ok === null
      ? result?.pending
        ? "pending"
        : "idle"
      : result.ok
        ? "ok"
        : "broken";
  const marks = {
    loading: "bg-surface-sunken text-ink-muted",
    idle: "bg-surface-sunken text-ink-muted",
    pending: "bg-primary-soft text-primary",
    ok: "bg-success-soft text-success-ink",
    broken: "bg-danger-soft text-danger",
  } as const;
  const verifiedAt = result?.verified_at ? formatDateTime(result.verified_at) : null;
  const intactUpTo = result ? Math.max(0, (result.first_bad_seq ?? 1) - 1) : 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: ta("home"), href: "/" },
          { label: ta("title"), href: "/audit" },
          { label: t("title") },
        ]}
        actions={back}
      />
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card title={t("statusLabel")}>
          <div aria-live="polite" aria-atomic="true" className="space-y-4">
            <div className="flex items-start gap-4">
              <span
                aria-hidden="true"
                className={`flex size-12 shrink-0 items-center justify-center rounded-full ${marks[state]}`}
              >
                <Icon
                  name={
                    state === "ok"
                      ? "checkCircle"
                      : state === "broken"
                        ? "alert"
                        : state === "pending"
                          ? "clock"
                          : "shieldCheck"
                  }
                  className="size-6"
                />
              </span>
              <div className="min-w-0 flex-1 space-y-1">
                {state === "loading" ? <LoadingState label={tc("loading")} /> : null}
                {state === "idle" ? (
                  <>
                    <p className="text-lg font-semibold text-ink">{t("notRunTitle")}</p>
                    <p className="text-sm text-ink-muted">{t("notRunBody")}</p>
                  </>
                ) : null}
                {state === "pending" ? (
                  <>
                    <Pill variant="progress">{t("queued")}</Pill>
                    <p className="text-sm text-ink-muted">{t("queuedBody")}</p>
                  </>
                ) : null}
                {state === "ok" && result ? (
                  <>
                    <p className="text-lg font-semibold text-ink">{t("okTitle")}</p>
                    <p className="text-sm text-ink-muted">
                      {result.checkpoint_seq === 0
                        ? t("okEmpty")
                        : result.mode === "incremental"
                          ? t("okIncremental", {
                              count: result.checked,
                              last: count(result.checkpoint_seq),
                            })
                          : t("okBody", {
                              count: result.checked,
                              last: count(result.checkpoint_seq),
                            })}
                    </p>
                  </>
                ) : null}
                {state === "broken" && result ? (
                  <>
                    <p className="text-lg font-semibold text-danger">
                      {t("brokenTitle", { seq: count(result.first_bad_seq ?? 0) })}
                    </p>
                    <p className="text-sm text-ink">
                      {intactUpTo > 0
                        ? t("brokenIntact", { count: intactUpTo, last: count(intactUpTo) })
                        : t("brokenFromStart")}
                    </p>
                    <p className="text-sm text-ink">
                      {translateOr(t, `reason.${result.reason ?? "other"}`, "reason.other")}
                    </p>
                  </>
                ) : null}
                {result?.pending && (state === "ok" || state === "broken") ? (
                  <Pill variant="progress">{t("queued")}</Pill>
                ) : null}
              </div>
            </div>
            {state === "ok" || state === "broken" ? (
              <div className="flex flex-wrap items-end justify-between gap-3 border-t border-border pt-4">
                <dl>
                  <dt className="text-sm text-ink-muted">{t("eventsChecked")}</dt>
                  <dd className="font-display text-4xl text-ink tabular-nums">
                    {result ? count(result.checked) : null}
                  </dd>
                </dl>
                {verifiedAt ? (
                  <p className="font-mono text-xs text-ink-subtle">
                    {t("lastVerifiedAt", { time: verifiedAt })}
                  </p>
                ) : null}
              </div>
            ) : null}
            {state === "broken" ? <Alert tone="danger" title={t("brokenAction")} /> : null}
          </div>
          {query.isError ? (
            <div className="mt-4">
              <ApiErrorAlert error={query.error} />
            </div>
          ) : null}
        </Card>
        <Card title={t("howTitle")}>
          <p className="text-sm text-ink-muted">{t("howBody")}</p>
          <div className="mt-5 space-y-3">
            <Button
              onClick={() => void checkAgain()}
              disabled={requesting || Boolean(result?.pending)}
              aria-disabled={requesting || Boolean(result?.pending) || undefined}
            >
              <Icon name="shieldCheck" className="size-4" />
              {result?.verified_at ? t("checkAgain") : ta("verify")}
            </Button>
            {coolDown !== null ? (
              <Alert tone="info" title={t("coolDownTitle")}>
                {t("coolDownBody", { minutes: coolDown })}
              </Alert>
            ) : requestError ? (
              <ApiErrorAlert error={requestError} />
            ) : null}
          </div>
        </Card>
      </div>
    </div>
  );
}

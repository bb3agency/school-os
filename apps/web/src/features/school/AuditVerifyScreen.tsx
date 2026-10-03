"use client";

import type { components } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
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
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";

export type AuditVerifyResult = components["schemas"]["app__audit__viewer__AuditVerifyOut"];

export const AUDIT_READ = "audit.read";
export const AUDIT_VERIFY_KEY = ["staff", "audit-verify"] as const;

/**
 * Check the school's audit chain (US-1001 AC2, FR-AUD-003, FR-AUD-005): GET /audit/verify
 * re-computes every event's hash link. The check reads the whole log, so it runs only when the
 * member asks for it, and never again by itself.
 */
export function AuditVerifyScreen() {
  const t = useTranslations("school.audit.integrity");
  const ta = useTranslations("school.audit");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const api = useBffClient("staff");
  const [asked, setAsked] = useState(false);
  const allowed = can(AUDIT_READ);
  const query = useQuery({
    queryKey: AUDIT_VERIFY_KEY,
    queryFn: () => unwrap(api.GET("/api/v1/audit/verify")),
    enabled: allowed && asked,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
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
  const running = asked && query.isFetching;
  const result = query.data;
  const checkedAt = result ? new Date(query.dataUpdatedAt).toISOString() : null;

  function check() {
    if (asked) void query.refetch();
    else setAsked(true);
  }

  const state: "idle" | "running" | "ok" | "broken" = running
    ? "running"
    : result?.ok
      ? "ok"
      : result
        ? "broken"
        : "idle";
  const marks = {
    idle: "bg-surface-sunken text-ink-muted",
    running: "bg-primary-soft text-primary",
    ok: "bg-success-soft text-success-ink",
    broken: "bg-danger-soft text-danger",
  } as const;

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
                        : state === "running"
                          ? "clock"
                          : "shieldCheck"
                  }
                  className="size-6"
                />
              </span>
              <div className="min-w-0 flex-1 space-y-1">
                {state === "idle" ? (
                  <>
                    <p className="text-lg font-semibold text-ink">{t("notRunTitle")}</p>
                    <p className="text-sm text-ink-muted">{t("notRunBody")}</p>
                  </>
                ) : null}
                {state === "running" ? (
                  <>
                    <Pill variant="progress">{t("checking")}</Pill>
                    <p className="text-sm text-ink-muted">{t("checkingBody")}</p>
                  </>
                ) : null}
                {state === "ok" && result ? (
                  <>
                    <p className="text-lg font-semibold text-ink">{t("okTitle")}</p>
                    <p className="text-sm text-ink-muted">
                      {result.checked === 0
                        ? t("okEmpty")
                        : t("okBody", { count: result.checked, last: count(result.checked) })}
                    </p>
                  </>
                ) : null}
                {state === "broken" && result ? (
                  <>
                    <p className="text-lg font-semibold text-danger">
                      {t("brokenTitle", { seq: count(result.first_bad_seq ?? 0) })}
                    </p>
                    <p className="text-sm text-ink">
                      {result.checked > 0
                        ? t("brokenIntact", { count: result.checked, last: count(result.checked) })
                        : t("brokenFromStart")}
                    </p>
                    <p className="text-sm text-ink">
                      {translateOr(t, `reason.${result.reason ?? "other"}`, "reason.other")}
                    </p>
                  </>
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
                {checkedAt ? (
                  <p className="font-mono text-xs text-ink-subtle">
                    {t("checkedAt", { time: formatDateTime(checkedAt) ?? "" })}
                  </p>
                ) : null}
              </div>
            ) : null}
            {state === "broken" ? <Alert tone="danger" title={t("brokenAction")} /> : null}
          </div>
          {!running && query.isError ? (
            <div className="mt-4">
              <ApiErrorAlert error={query.error} />
            </div>
          ) : null}
        </Card>
        <Card title={t("howTitle")}>
          <p className="text-sm text-ink-muted">{t("howBody")}</p>
          <div className="mt-5">
            <Button onClick={check} disabled={running} aria-disabled={running || undefined}>
              <Icon name="shieldCheck" className="size-4" />
              {running ? t("checking") : result ? t("checkAgain") : ta("verify")}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

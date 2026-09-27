"use client";

import type { components } from "@schoolos/api-client";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
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

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} actions={back} />
      <Card title={t("howTitle")}>
        <p className="text-sm">{t("howBody")}</p>
        <div className="mt-4">
          <Button onClick={check} disabled={running} aria-disabled={running || undefined}>
            {running ? t("checking") : result ? t("checkAgain") : ta("verify")}
          </Button>
        </div>
      </Card>
      <div aria-live="polite" aria-atomic="true">
        {running ? <p className="text-sm">{t("checkingBody")}</p> : null}
        {!running && result?.ok ? (
          <Alert tone="success" title={t("okTitle")}>
            <p>
              {result.checked === 0
                ? t("okEmpty")
                : t("okBody", { count: result.checked, last: count(result.checked) })}
            </p>
            {checkedAt ? (
              <p className="mt-1 text-xs">
                {t("checkedAt", { time: formatDateTime(checkedAt) ?? "" })}
              </p>
            ) : null}
          </Alert>
        ) : null}
        {!running && result && !result.ok ? (
          <Alert tone="danger" title={t("brokenTitle", { seq: count(result.first_bad_seq ?? 0) })}>
            <p>
              {result.checked > 0
                ? t("brokenIntact", { count: result.checked, last: count(result.checked) })
                : t("brokenFromStart")}
            </p>
            <p className="mt-2">
              {translateOr(t, `reason.${result.reason ?? "other"}`, "reason.other")}
            </p>
            <p className="mt-2 font-semibold">{t("brokenAction")}</p>
            {checkedAt ? (
              <p className="mt-1 text-xs">
                {t("checkedAt", { time: formatDateTime(checkedAt) ?? "" })}
              </p>
            ) : null}
          </Alert>
        ) : null}
      </div>
      {!running && query.isError ? <ApiErrorAlert error={query.error} /> : null}
    </div>
  );
}

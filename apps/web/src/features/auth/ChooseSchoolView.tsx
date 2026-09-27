"use client";

import type { SchoolChoice } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { known, schoolTone } from "@/features/status";
import { chooseSchool, defaultNavigate, type Navigate } from "@/lib/bff/session-client";

/**
 * FR-IAM-013 school picker: one button per school the user belongs to. Only active schools
 * can be opened; suspended or offboarding ones are shown disabled with the reason, so the
 * user knows whom to ask. Choosing POSTs /bff/auth/active-tenant (CSRF), then continues to
 * `next` (already validated on the server).
 */
export function ChooseSchoolView({
  schools,
  next,
  currentTenantId = null,
  navigate = defaultNavigate,
}: {
  schools: readonly SchoolChoice[];
  next: string;
  currentTenantId?: string | null;
  navigate?: Navigate;
}) {
  const t = useTranslations("chooseSchool");
  const tstatus = useTranslations("status.school");
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  async function open(school: SchoolChoice) {
    setBusy(school.tenant_id);
    setFailed(null);
    try {
      const code = await chooseSchool(school.tenant_id);
      if (code === null) {
        navigate(next);
        return;
      }
      setFailed(code);
    } catch {
      setFailed("network");
    }
    setBusy(null);
  }

  const failure =
    failed === null
      ? null
      : failed === "tenant_not_available"
        ? t("errorNotAvailable")
        : failed === "network" || failed === "service_unavailable"
          ? t("errorNetwork")
          : t("errorGeneric");

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      {failure ? (
        <Alert tone="danger" live>
          {failure}
        </Alert>
      ) : null}
      <ul className="space-y-3" aria-label={t("listLabel")}>
        {schools.map((school) => {
          const status = known(schoolTone, school.status);
          const usable = school.status === "active";
          const current = currentTenantId === school.tenant_id;
          const hintId = `school-${school.tenant_id}-hint`;
          return (
            <li
              key={school.tenant_id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface p-4"
            >
              <div className="space-y-1">
                <p className="font-semibold">
                  {school.name}{" "}
                  <span className="font-mono text-xs text-ink-muted">({school.code})</span>
                </p>
                <p className="flex flex-wrap items-center gap-2 text-sm">
                  {status ? (
                    <Badge tone={schoolTone[status]}>{tstatus(status)}</Badge>
                  ) : (
                    <Badge>{school.status}</Badge>
                  )}
                  {current ? <Badge tone="info">{t("current")}</Badge> : null}
                </p>
                {!usable ? (
                  <p id={hintId} className="text-sm text-ink-muted">
                    {school.status === "suspended" || school.status === "offboarding"
                      ? t("suspendedHint")
                      : t("notReadyHint")}
                  </p>
                ) : null}
              </div>
              <Button
                onClick={() => open(school)}
                disabled={!usable || busy !== null}
                aria-describedby={!usable ? hintId : undefined}
              >
                {busy === school.tenant_id ? t("opening") : t("open")}
                <span className="sr-only">: {school.name}</span>
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

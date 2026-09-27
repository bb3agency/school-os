"use client";

import type { TenantDetail } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useId } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert, type AlertTone } from "@/components/ui/Alert";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";
import { PK } from "./data";

type Provisioning = NonNullable<TenantDetail["provisioning"]>;

const TONE: Record<Provisioning["state"], AlertTone> = {
  registered: "info",
  initialised: "info",
  completed: "success",
  failed: "warning",
};

/**
 * Where a school's setup stands (FR-PLT-002, docs/16 §5.4), in plain language: the state,
 * the step that stopped, the error code for support (codes only, never data), the number of
 * attempts and whether it can be resumed. Shown while the school is still `provisioning`.
 */
export function ProvisioningStatus({ school }: { school: TenantDetail }) {
  const t = useTranslations("platform.schoolDetail.provisioning");
  const headingId = useId();
  const run = school.provisioning;
  if (!run || school.tenant_status !== "provisioning") return null;
  const updated = formatDateTime(run.updated_at);
  return (
    <section aria-labelledby={headingId}>
      <Alert tone={TONE[run.state]} title={<span id={headingId}>{t(`state.${run.state}`)}</span>}>
        <div className="space-y-1">
          {run.failed_step ? <p>{t("failedAt", { step: t(`steps.${run.failed_step}`) })}</p> : null}
          {run.last_error ? <p>{t("errorCode", { code: run.last_error })}</p> : null}
          {run.state !== "completed" ? <p>{t("attempts", { count: run.attempts })}</p> : null}
          {updated ? <p>{t("updatedAt", { date: updated })}</p> : null}
          {run.in_progress ? <p>{t("inProgress")}</p> : null}
          {run.resumable ? <p>{t("resumeHint")}</p> : null}
        </div>
      </Alert>
    </section>
  );
}

/** Short label for the overview's "Setup" row. */
export function ProvisioningLabel({ school }: { school: TenantDetail }) {
  const t = useTranslations("platform.schoolDetail.provisioning");
  if (!school.provisioning) return null;
  return <>{t(`label.${school.provisioning.state}`)}</>;
}

/**
 * "Resume provisioning" (POST /platform/tenants/{id}/provisioning:resume,
 * platform.tenants.provision). Confirmed first; the API may ask for step-up (428), which the
 * BFF client turns into a fresh sign-in. Codes such as `provisioning_in_progress` or
 * `resume_needs_request` are explained in the dialog (errors.api.*, en/te).
 */
export function ResumeProvisioningAction({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.schoolDetail.provisioning");
  const api = useBffClient("operator");
  return (
    <ActionDialog
      triggerLabel={t("resume")}
      triggerVariant="primary"
      title={t("resumeTitle")}
      description={t("resumeBody")}
      confirmLabel={t("resume")}
      stepUp
      schema={z.object({})}
      invalidate={[PK.tenants, PK.dashboard, PK.deployments]}
      submit={() =>
        unwrap(
          api.POST("/api/v1/platform/tenants/{tenant_id}/provisioning:resume", {
            params: { path: { tenant_id: schoolId } },
          }),
        )
      }
    />
  );
}

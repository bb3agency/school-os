"use client";

import type { TenantDetail } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Timeline, type TimelineItem, type TimelineStatus } from "@/components/ui/Timeline";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";
import { PK } from "./data";

type Provisioning = NonNullable<TenantDetail["provisioning"]>;

/** The provisioning saga in order (docs/16 §5.4): tenant row, keys and roles, owner invite. */
const STEPS = ["register", "initialise", "owner_invite"] as const;
type Step = (typeof STEPS)[number];

/** How many steps are finished, from the run's state (codes only, as the API reports them). */
function doneCount(run: Provisioning): number {
  switch (run.state) {
    case "registered":
      return 1;
    case "initialised":
      return 2;
    case "completed":
      return STEPS.length;
    case "failed":
      return run.failed_step ? STEPS.indexOf(run.failed_step) : 1;
  }
}

/**
 * Where a school's setup stands (FR-PLT-002, docs/16 §5.4), in plain language: the state as
 * the card title, the steps as a timeline, the step that stopped, the error code for support
 * (codes only, never data), the number of attempts and whether it can be resumed. Shown
 * while the school is still `provisioning`. `action` is the "Resume provisioning" button.
 */
export function ProvisioningStatus({
  school,
  action,
}: {
  school: TenantDetail;
  action?: ReactNode;
}) {
  const t = useTranslations("platform.schoolDetail.provisioning");
  const run = school.provisioning;
  if (!run || school.tenant_status !== "provisioning") return null;
  const updated = formatDateTime(run.updated_at);
  const done = doneCount(run);
  const failed = run.state === "failed";

  const items: TimelineItem[] = STEPS.map((step: Step, index) => {
    const status: TimelineStatus = index < done ? "done" : index === done ? "current" : "pending";
    const stopped = failed && index === done;
    return {
      id: step,
      title: t(`timeline.${step}`),
      status,
      statusLabel: stopped
        ? t("stepStatus.stopped")
        : status === "current" && run.in_progress
          ? t("stepStatus.running")
          : t(`stepStatus.${status}`),
      ...(stopped
        ? { chips: <Pill variant="negative">{t("stoppedHere")}</Pill> }
        : status === "current" && run.in_progress
          ? { chips: <Pill variant="progress">{t("runningNow")}</Pill> }
          : {}),
    };
  });

  return (
    <Card eyebrow={t("eyebrow")} title={t(`state.${run.state}`)} actions={action}>
      <div className="grid gap-6 md:grid-cols-2">
        <Timeline items={items} label={t("timelineLabel")} />
        <div className="space-y-3 text-sm">
          {run.failed_step || run.last_error ? (
            <Alert tone="warning">
              <div className="space-y-1">
                {run.failed_step ? (
                  <p>{t("failedAt", { step: t(`steps.${run.failed_step}`) })}</p>
                ) : null}
                {run.last_error ? <p>{t("errorCode", { code: run.last_error })}</p> : null}
              </div>
            </Alert>
          ) : null}
          {run.in_progress ? <p>{t("inProgress")}</p> : null}
          {run.state !== "completed" ? (
            <p className="text-ink-muted">{t("attempts", { count: run.attempts })}</p>
          ) : null}
          {updated ? <p className="text-ink-muted">{t("updatedAt", { date: updated })}</p> : null}
          {run.resumable ? <p>{t("resumeHint")}</p> : null}
        </div>
      </div>
    </Card>
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

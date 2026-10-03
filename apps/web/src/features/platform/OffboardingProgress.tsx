"use client";

import type { Offboarding, TenantDetail } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { Timeline, type TimelineItem, type TimelineStatus } from "@/components/ui/Timeline";
import { startDownload } from "@/features/exports/data";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { PK } from "./data";

/** Same pattern as the API's `OffboardingReference` (docs/16 §5.5.1): a letter or ticket
 * number, never free text or personal data. */
export const OFFBOARDING_REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 ._/#:-]{0,79}$/;

const reference = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .regex(OFFBOARDING_REFERENCE_PATTERN, { error: "invalidReference" });

const exportSchema = z.object({
  basis: z.enum(["school_confirmed", "delivered_by_us"], { error: "chooseOption" }),
  reference,
});
const teardownSchema = z.object({
  kms_deletion_reference: reference,
  host_teardown_reference: reference,
});

/** Categories in the order the certificate lists them (docs/16 §5.5.1). */
const CATEGORIES = [
  "students",
  "changes",
  "dq",
  "extraction",
  "imports",
  "documents",
  "knowledge",
  "exports",
  "admin",
  "notifications",
  "breakglass",
  "ops",
  "identity",
  "tenancy",
] as const;
type Category = (typeof CATEGORIES)[number];

type Step = "approved" | "export" | "deleted" | "teardown" | "keys" | "certificate";

function steps(run: Offboarding): readonly Step[] {
  return run.tier === "dedicated"
    ? ["approved", "export", "teardown", "certificate"]
    : ["approved", "export", "deleted", "keys", "certificate"];
}

function stepTime(run: Offboarding, step: Step): string | null {
  switch (step) {
    case "approved":
      return run.approved_at;
    case "export":
      return run.export_confirmed_at;
    case "deleted":
      return run.data_deleted_at;
    case "teardown":
      return run.teardown_confirmed_at;
    case "keys":
      return run.keys_destroyed_at;
    case "certificate":
      return run.certificate?.issued_at ?? null;
  }
}

const INVALIDATE = [PK.tenants, PK.dashboard, PK.deployments] as const;

/**
 * Offboarding progress after the two-person approval (FR-PLT-005, docs/16 §5.5.1): the state
 * in plain language, the 30-day deadline (due soon / overdue), the steps as a timeline, rows per
 * category found before deletion, what verification still finds, the failed step and its error
 * code, and the actions: confirm the export (the gate before any deletion), confirm the teardown
 * of a dedicated host, download the certificate. Counts and codes only; no school data.
 */
export function OffboardingProgress({
  school,
  canOffboard,
}: {
  school: TenantDetail;
  canOffboard: boolean;
}) {
  const t = useTranslations("platform.schoolDetail.offboarding");
  const locale = useLocale();
  const count = (value: number | null | undefined) => formatCount(value ?? 0, locale) ?? "0";
  const run = school.offboarding;
  if (!run) return null;
  const order = steps(run);
  const done = order.filter((step) => stepTime(run, step) !== null).length;
  const items: TimelineItem[] = order.map((step, index) => {
    const at = stepTime(run, step);
    const status: TimelineStatus = at ? "done" : index === done ? "current" : "pending";
    return {
      id: step,
      title: t(`steps.${step}`),
      status,
      statusLabel: t(`stepStatus.${status}`),
      ...(at ? { time: formatDateTime(at) ?? "" } : {}),
    };
  });
  const inventory = run.inventory ?? null;
  const remaining = Object.entries(run.remaining ?? {}).filter(([, n]) => n > 0);

  const actions = (
    <div className="flex flex-wrap gap-2">
      {canOffboard && run.state === "awaiting_export" ? (
        <ConfirmExportAction schoolId={school.tenant_id} />
      ) : null}
      {canOffboard && run.tier === "dedicated" && run.state === "scheduled" ? (
        <ConfirmTeardownAction schoolId={school.tenant_id} />
      ) : null}
      {run.certificate ? <CertificateDownload schoolId={school.tenant_id} /> : null}
    </div>
  );

  return (
    <Card eyebrow={t("eyebrow")} title={t(`state.${run.state}`)} actions={actions}>
      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-3">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            {t("deadline", { date: formatDate(run.deadline_at) ?? "" })}
            {run.overdue ? <Pill variant="negative">{t("overdue")}</Pill> : null}
            {!run.overdue && run.due_soon && run.state !== "completed" ? (
              <Pill variant="review">{t("dueSoon")}</Pill>
            ) : null}
          </p>
          <Timeline items={items} label={t("timelineLabel")} />
        </div>
        <div className="space-y-3 text-sm">
          {run.state === "awaiting_export" ? <p>{t("exportGateHint")}</p> : null}
          {run.tier === "dedicated" && run.state === "scheduled" ? (
            <p>{t("teardownHint")}</p>
          ) : null}
          {run.failed_step || run.last_error ? (
            <Alert tone="warning">
              <div className="space-y-1">
                {run.failed_step ? (
                  <p>{t("failedAt", { step: t(`failedSteps.${run.failed_step}`) })}</p>
                ) : null}
                {run.last_error ? <p>{t("errorCode", { code: run.last_error })}</p> : null}
                <p>{t("retryHint")}</p>
              </div>
            </Alert>
          ) : null}
          {run.in_progress ? <p>{t("inProgress")}</p> : null}
          {inventory ? (
            <table className="w-full text-left">
              <caption className="mb-1 text-left font-semibold">{t("inventoryTitle")}</caption>
              <thead className="sr-only">
                <tr>
                  <th scope="col">{t("category")}</th>
                  <th scope="col">{t("rows")}</th>
                </tr>
              </thead>
              <tbody>
                {CATEGORIES.map((category: Category) => (
                  <tr key={category} className="border-b border-border-soft">
                    <th scope="row" className="py-1 font-normal">
                      {t(`categories.${category}`)}
                    </th>
                    <td className="py-1 text-right tabular-nums">{count(inventory[category])}</td>
                  </tr>
                ))}
                <tr>
                  <th scope="row" className="py-1 font-normal">
                    {t("categories.files")}
                  </th>
                  <td className="py-1 text-right tabular-nums">{count(run.objects_before)}</td>
                </tr>
              </tbody>
            </table>
          ) : null}
          {remaining.length > 0 ? (
            <Alert tone="warning" title={t("remainingTitle")}>
              <ul className="list-inside list-disc">
                {remaining.map(([table, rows]) => (
                  <li key={table}>
                    <code className="font-mono">{table}</code>: {count(rows)}
                  </li>
                ))}
              </ul>
            </Alert>
          ) : null}
          {run.keys_destroyed_at ? (
            <p>{t("keysDestroyed", { count: run.keys_destroyed ?? 0 })}</p>
          ) : null}
          {run.certificate ? (
            <p className="text-ink-muted">
              {t("certificateIssued", { date: formatDateTime(run.certificate.issued_at) ?? "" })}
            </p>
          ) : null}
          {run.audit_delete_after ? (
            <p className="text-ink-muted">
              {run.audit_deleted_at
                ? t("auditDeleted", { date: formatDate(run.audit_deleted_at) ?? "" })
                : t("auditRetained", { date: formatDate(run.audit_delete_after) ?? "" })}
            </p>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

/** Export gate (POST …/offboarding:confirm-export, platform.tenants.offboard, step-up). */
export function ConfirmExportAction({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.schoolDetail.offboarding");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  return (
    <ActionDialog
      triggerLabel={t("confirmExport")}
      triggerVariant="primary"
      title={t("confirmExportTitle")}
      description={t("confirmExportBody")}
      confirmLabel={t("confirmExport")}
      stepUp
      schema={exportSchema}
      invalidate={INVALIDATE}
      submit={(data) =>
        unwrap(
          api.POST("/api/v1/platform/tenants/{tenant_id}/offboarding:confirm-export", {
            params: { path: { tenant_id: schoolId } },
            body: { basis: data.basis, reference: data.reference },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="basis"
            label={t("basis")}
            placeholder={tc("chooseOne")}
            error={errors.basis}
            defaultValue=""
            options={[
              { value: "school_confirmed", label: t("bases.school_confirmed") },
              { value: "delivered_by_us", label: t("bases.delivered_by_us") },
            ]}
          />
          <TextField
            name="reference"
            label={t("reference")}
            hint={t("referenceHint")}
            error={errors.reference}
            maxLength={80}
            required
          />
        </>
      )}
    </ActionDialog>
  );
}

/** Dedicated host teardown (POST …/offboarding:confirm-teardown, step-up). */
export function ConfirmTeardownAction({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.schoolDetail.offboarding");
  const api = useBffClient("operator");
  return (
    <ActionDialog
      triggerLabel={t("confirmTeardown")}
      triggerVariant="primary"
      title={t("confirmTeardownTitle")}
      description={t("confirmTeardownBody")}
      confirmLabel={t("confirmTeardown")}
      stepUp
      schema={teardownSchema}
      invalidate={INVALIDATE}
      submit={(data) =>
        unwrap(
          api.POST("/api/v1/platform/tenants/{tenant_id}/offboarding:confirm-teardown", {
            params: { path: { tenant_id: schoolId } },
            body: data,
          }),
        )
      }
    >
      {(errors) => (
        <>
          <TextField
            name="kms_deletion_reference"
            label={t("kmsReference")}
            hint={t("referenceHint")}
            error={errors.kms_deletion_reference}
            maxLength={80}
            required
          />
          <TextField
            name="host_teardown_reference"
            label={t("hostReference")}
            hint={t("referenceHint")}
            error={errors.host_teardown_reference}
            maxLength={80}
            required
          />
        </>
      )}
    </ActionDialog>
  );
}

/**
 * "Download certificate": GET …/deletion-certificate/download-url (platform.tenants.read;
 * audited) and open the short-lived link once. The link is never kept in state.
 */
export function CertificateDownload({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.schoolDetail.offboarding");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  async function download() {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/platform/tenants/{tenant_id}/deletion-certificate/download-url", {
          params: { path: { tenant_id: schoolId } },
        }),
      );
      startDownload(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const pendingCode = error instanceof ApiError && error.code === "certificate_pending";
  return (
    <span className="relative flex flex-col items-start gap-1">
      <Button
        variant="secondary"
        onClick={() => void download()}
        disabled={pending}
        aria-disabled={pending || undefined}
      >
        <Icon name="arrowDown" className="size-4" />
        {pending ? tc("working") : t("downloadCertificate")}
      </Button>
      {pendingCode ? (
        <Alert tone="info" live>
          {t("certificatePending")}
        </Alert>
      ) : (
        <ApiErrorAlert error={error} />
      )}
    </span>
  );
}

"use client";

import { useTranslations } from "next-intl";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { Value } from "@/components/ui/Value";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";
import {
  CERT_KEYS,
  ifMatch,
  REASON_MIN,
  VERIFICATION_REASON_MAX,
  type Verification,
} from "./types";

const reasonSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .min(REASON_MIN, { error: "reasonTooShort" })
    .max(VERIFICATION_REASON_MAX, { error: "tooLong" }),
});

function ReasonField({ error }: { error: string | undefined }) {
  const t = useTranslations("certificates.verification");
  return (
    <TextAreaField
      name="reason"
      label={t("reason")}
      hint={t("reasonHint")}
      error={error}
      maxLength={VERIFICATION_REASON_MAX}
      rows={3}
    />
  );
}

/**
 * The certificate's online check (ADR-0045; US-2002, US-2004): the code printed on the paper,
 * how often it was checked and when last (nothing about who checked), and for the principal
 * (`certificate.approve`, step-up) adding a code to a certificate issued before R2 or
 * withdrawing a misused one.
 */
export function VerificationCard({ certificateId }: { certificateId: string }) {
  const t = useTranslations("certificates.verification");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const path = { certificate_id: certificateId };
  const invalidate = [CERT_KEYS.verification(certificateId)] as const;
  const query = useApiQuery(CERT_KEYS.verification(certificateId), () =>
    unwrap(api.GET("/api/v1/certificates/{certificate_id}/verification", { params: { path } })),
  );
  if (query.status === "loading") return <LoadingState label={tc("loading")} />;
  if (query.status !== "ready") return null;
  const v: Verification = query.data;
  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        {v.status === "none" ? (
          <Alert tone="info" title={t("noneTitle")}>
            {t("noneBody")}
          </Alert>
        ) : (
          <dl className="divide-y divide-border">
            <div className="grid gap-1 py-2 sm:grid-cols-[12rem_1fr] sm:gap-4">
              <dt className="text-sm text-ink-muted">{t("code")}</dt>
              <dd className="font-mono text-lg tracking-wider">
                <Value>{v.code}</Value>
              </dd>
            </div>
            <div className="grid gap-1 py-2 sm:grid-cols-[12rem_1fr] sm:gap-4">
              <dt className="text-sm text-ink-muted">{t("checks")}</dt>
              <dd>{t("checkCount", { count: v.check_count })}</dd>
            </div>
            {v.last_checked_at ? (
              <div className="grid gap-1 py-2 sm:grid-cols-[12rem_1fr] sm:gap-4">
                <dt className="text-sm text-ink-muted">{t("lastChecked")}</dt>
                <dd>
                  <Value>{formatDateTime(v.last_checked_at)}</Value>
                </dd>
              </div>
            ) : null}
            {v.url ? (
              <div className="grid gap-1 py-2 sm:grid-cols-[12rem_1fr] sm:gap-4">
                <dt className="text-sm text-ink-muted">{t("link")}</dt>
                <dd className="break-all text-sm">{v.url}</dd>
              </div>
            ) : null}
          </dl>
        )}
        {v.created_reason ? (
          <p className="text-sm text-ink-muted">{t(`reasons.${v.created_reason}`)}</p>
        ) : null}
        {v.status === "active" && !v.url ? (
          <p className="text-sm text-ink-muted">{t("off")}</p>
        ) : null}
        {v.status === "withdrawn" ? (
          <Alert tone="warning" title={t("withdrawnTitle")}>
            <span className="block">{t("withdrawnBody")}</span>
            {v.withdraw_reason ? (
              <span className="mt-1 block whitespace-pre-line">{v.withdraw_reason}</span>
            ) : null}
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {v.can_add ? (
            <ActionDialog
              triggerLabel={t("add")}
              triggerVariant="secondary"
              title={t("addTitle")}
              description={t("addBody")}
              confirmLabel={t("add")}
              stepUp
              schema={reasonSchema}
              invalidate={invalidate}
              errorNamespace="certificates"
              submit={(input, key) =>
                unwrap(
                  api.POST("/api/v1/certificates/{certificate_id}/verification", {
                    params: { path },
                    headers: { "Idempotency-Key": key },
                    body: input,
                  }),
                )
              }
            >
              {(errors) => <ReasonField error={errors.reason} />}
            </ActionDialog>
          ) : null}
          {v.can_withdraw ? (
            <ActionDialog
              triggerLabel={t("withdraw")}
              triggerVariant="danger"
              title={t("withdrawTitle")}
              description={t("withdrawBody")}
              confirmLabel={t("withdraw")}
              confirmVariant="danger"
              stepUp
              schema={reasonSchema}
              invalidate={invalidate}
              errorNamespace="certificates"
              submit={(input) =>
                unwrap(
                  api.POST("/api/v1/certificates/{certificate_id}/verification/withdraw", {
                    params: { path },
                    headers: { "If-Match": ifMatch(v.version) },
                    body: input,
                  }),
                )
              }
            >
              {(errors) => <ReasonField error={errors.reason} />}
            </ActionDialog>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

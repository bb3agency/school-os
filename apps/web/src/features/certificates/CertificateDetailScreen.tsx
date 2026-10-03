"use client";

import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Timeline, type TimelineItem } from "@/components/ui/Timeline";
import { Value } from "@/components/ui/Value";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useApiMutation, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import {
  CertificateStatusBadge,
  DownloadPdfButton,
  inputText,
  PrintViewLink,
  useCertificateTitle,
  useCertificateTypes,
} from "./parts";
import {
  CERT_KEYS,
  ifMatch,
  REASON_MAX,
  REASON_MIN,
  type Certificate,
  type ContentLine,
} from "./types";

const reasonText = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(REASON_MIN, { error: "reasonTooShort" })
  .max(REASON_MAX, { error: "tooLong" });
const reasonSchema = z.object({ reason: reasonText });
const approveSchema = z.object({
  note: z
    .string()
    .trim()
    .max(REASON_MAX, { error: "tooLong" })
    .transform((value) => (value === "" ? null : value)),
});

function ReasonField({
  label,
  hint,
  error,
}: {
  label: string;
  hint: string;
  error: string | undefined;
}) {
  return (
    <TextAreaField
      name="reason"
      label={label}
      hint={hint}
      error={error}
      maxLength={REASON_MAX}
      rows={3}
    />
  );
}

/** Approve / reject (checkers, step-up) and withdraw (the requester) of a pending request. */
function Decisions({ certificate }: { certificate: Certificate }) {
  const t = useTranslations("certificates.detail");
  const api = useBffClient("staff");
  const path = { certificate_id: certificate.id };
  const headers = { "If-Match": ifMatch(certificate.version) };
  const invalidate = [CERT_KEYS.all] as const;
  return (
    <div className="flex flex-wrap gap-2">
      {certificate.can_approve ? (
        <>
          <ActionDialog
            triggerLabel={t("approve")}
            triggerVariant="primary"
            title={t("approveTitle")}
            description={t("approveBody")}
            confirmLabel={t("approve")}
            stepUp
            schema={approveSchema}
            invalidate={invalidate}
            errorNamespace="certificates"
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/certificates/{certificate_id}/approve", {
                  params: { path },
                  headers,
                  body: { note: input.note },
                }),
              )
            }
          >
            {(errors) => (
              <TextAreaField
                name="note"
                label={t("approveNote")}
                hint={t("approveNoteHint")}
                error={errors.note}
                maxLength={REASON_MAX}
                rows={3}
              />
            )}
          </ActionDialog>
          <ActionDialog
            triggerLabel={t("reject")}
            triggerVariant="danger"
            title={t("rejectTitle")}
            description={t("rejectBody")}
            confirmLabel={t("reject")}
            confirmVariant="danger"
            stepUp
            schema={reasonSchema}
            invalidate={invalidate}
            errorNamespace="certificates"
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/certificates/{certificate_id}/reject", {
                  params: { path },
                  headers,
                  body: input,
                }),
              )
            }
          >
            {(errors) => (
              <ReasonField label={t("rejectReason")} hint={t("reasonHint")} error={errors.reason} />
            )}
          </ActionDialog>
        </>
      ) : null}
      {certificate.can_withdraw ? (
        <ActionDialog
          triggerLabel={t("withdraw")}
          triggerVariant="secondary"
          title={t("withdrawTitle")}
          description={t("withdrawBody")}
          confirmLabel={t("withdraw")}
          confirmVariant="danger"
          schema={z.object({})}
          invalidate={invalidate}
          errorNamespace="certificates"
          submit={() =>
            unwrap(
              api.POST("/api/v1/certificates/{certificate_id}/withdraw", {
                params: { path },
                headers,
              }),
            )
          }
        />
      ) : null}
    </div>
  );
}

/** Duplicate (reason) and cancel (step-up, reason) of an issued certificate. */
function IssuedActions({ certificate }: { certificate: Certificate }) {
  const t = useTranslations("certificates.detail");
  const api = useBffClient("staff");
  const router = useRouter();
  const path = { certificate_id: certificate.id };
  const invalidate = [CERT_KEYS.all] as const;
  return (
    <div className="flex flex-wrap gap-2">
      {certificate.can_duplicate ? (
        <ActionDialog
          triggerLabel={t("duplicate")}
          triggerVariant="secondary"
          title={t("duplicateTitle")}
          description={
            certificate.requires_approval ? t("duplicateBodyApproval") : t("duplicateBody")
          }
          confirmLabel={t("duplicate")}
          schema={reasonSchema}
          invalidate={invalidate}
          errorNamespace="certificates"
          submit={(input, key) =>
            unwrap(
              api.POST("/api/v1/certificates/{certificate_id}/duplicates", {
                params: { path },
                headers: { "Idempotency-Key": key },
                body: input,
              }),
            )
          }
          onSuccess={(created) => router.push(`/certificates/${created.id}`)}
        >
          {(errors) => (
            <ReasonField
              label={t("duplicateReason")}
              hint={t("reasonHint")}
              error={errors.reason}
            />
          )}
        </ActionDialog>
      ) : null}
      {certificate.can_cancel ? (
        <ActionDialog
          triggerLabel={t("cancel")}
          triggerVariant="danger"
          title={t("cancelTitle")}
          description={
            certificate.certificate_type === "transfer" ? t("cancelBodyTc") : t("cancelBody")
          }
          confirmLabel={t("cancel")}
          confirmVariant="danger"
          stepUp
          schema={reasonSchema}
          invalidate={invalidate}
          errorNamespace="certificates"
          submit={(input) =>
            unwrap(
              api.POST("/api/v1/certificates/{certificate_id}/cancel", {
                params: { path },
                headers: { "If-Match": ifMatch(certificate.version) },
                body: input,
              }),
            )
          }
        >
          {(errors) => (
            <ReasonField label={t("cancelReason")} hint={t("reasonHint")} error={errors.reason} />
          )}
        </ActionDialog>
      ) : null}
    </div>
  );
}

function Lines({ lines, locale }: { lines: readonly ContentLine[]; locale: string }) {
  const shown = lines.filter((line) => line.label_en !== "");
  return (
    <dl className="divide-y divide-border">
      {shown.map((line) => (
        <div key={line.key} className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr] sm:gap-4">
          <dt className="text-sm text-ink-muted">
            {locale === "te" && line.label_te ? line.label_te : line.label_en}
          </dt>
          <dd className="break-words text-ink">
            <Value>{line.value}</Value>
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * One certificate (US-1101..US-1107): what it prints (frozen once issued), where it is in the
 * workflow, and what the viewer may do: approve or reject (not the requester; step-up),
 * withdraw, print, download the PDF, issue a duplicate or cancel (step-up).
 */
export function CertificateDetailScreen({ certificateId }: { certificateId: string }) {
  const t = useTranslations("certificates");
  const td = useTranslations("certificates.detail");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const locale = useLocale();
  const api = useBffClient("staff");
  const me = useStaffMe();
  const types = useCertificateTypes();
  const title = useCertificateTitle();
  const query = useApiQuery(CERT_KEYS.one(certificateId), () =>
    unwrap(
      api.GET("/api/v1/certificates/{certificate_id}", {
        params: { path: { certificate_id: certificateId } },
      }),
    ),
  );
  const retry = useApiMutation(
    () =>
      unwrap(
        api.POST("/api/v1/certificates/{certificate_id}/render", {
          params: { path: { certificate_id: certificateId } },
        }),
      ),
    { invalidate: [CERT_KEYS.all] },
  );
  const crumbs = (last: string) => [{ label: t("title"), href: "/certificates" }, { label: last }];

  if (query.status === "loading") return <LoadingState label={tc("loading")} />;
  if (query.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader breadcrumb={crumbs(t("nav"))} title={t("nav")} />
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {query.status === "error" && query.reason
            ? te(`load.${query.reason}`)
            : tc("loadErrorBody")}
        </Alert>
      </div>
    );
  }
  const data = query.data;
  const catalog = types.status === "ready" ? types.data : null;
  const mine = me?.membership_id === data.requested_by;
  const name = title(data);
  const issued = data.status === "issued" || data.status === "cancelled";

  const steps: TimelineItem[] = [
    {
      id: "asked",
      title: data.requires_approval ? td("prepared") : td("requested"),
      time: <Value>{formatDateTime(data.requested_at)}</Value>,
      body: mine ? td("byYou") : td("bySomeoneElse"),
      status: "done",
    },
  ];
  if (data.status === "pending") {
    steps.push({ id: "waiting", title: td("waitingApproval"), status: "current" });
  }
  if (data.status === "rejected" || data.status === "withdrawn") {
    steps.push({
      id: "closed",
      title: t(`status.${data.status}`),
      ...(data.decided_at ? { time: <Value>{formatDateTime(data.decided_at)}</Value> } : {}),
      body: data.decision_note ? (
        <p className="whitespace-pre-line">{data.decision_note}</p>
      ) : undefined,
      status: "done",
    });
  }
  if (issued && data.issued_at) {
    steps.push({
      id: "issued",
      title: data.requires_approval ? td("approvedIssued") : td("issued"),
      time: <Value>{formatDateTime(data.issued_at)}</Value>,
      body: data.decision_note ? (
        <p className="whitespace-pre-line">{data.decision_note}</p>
      ) : undefined,
      status: "done",
    });
  }
  if (data.status === "cancelled") {
    steps.push({
      id: "cancelled",
      title: t("status.cancelled"),
      ...(data.cancelled_at ? { time: <Value>{formatDateTime(data.cancelled_at)}</Value> } : {}),
      body: <p className="whitespace-pre-line">{data.cancel_reason}</p>,
      status: "done",
    });
  }

  const inputs = Object.entries(data.inputs);

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumb={crumbs(name)}
        title={name}
        description={
          <span className="mt-1 flex flex-wrap items-center gap-2">
            <Value>{data.student_name}</Value>
            {data.admission_no ? (
              <Pill variant="tag">
                <span className="font-mono">
                  {td("admissionNo", { number: data.admission_no })}
                </span>
              </Pill>
            ) : null}
          </span>
        }
        badge={<CertificateStatusBadge status={data.status} size="md" />}
        actions={data.status === "pending" ? <Decisions certificate={data} /> : null}
      />

      {data.status === "pending" && data.can_approve ? (
        <Alert tone="info" title={td("waitingForYouTitle")}>
          {td("waitingForYouBody")}
        </Alert>
      ) : null}
      {data.status === "pending" && mine ? (
        <Alert tone="info" title={td("ownRequestTitle")}>
          {td("ownRequestBody")}
        </Alert>
      ) : null}
      {data.original_certificate_id ? (
        <Alert tone="info" title={td("duplicateOfTitle", { copy: data.duplicate_no ?? "—" })}>
          <span className="space-y-1">
            <span className="block whitespace-pre-line">{data.duplicate_reason}</span>
            <Link
              href={`/certificates/${data.original_certificate_id}`}
              className="text-primary underline"
            >
              {td("openOriginal")}
            </Link>
          </span>
        </Alert>
      ) : null}
      {data.status === "cancelled" ? (
        <Alert tone="warning" title={td("cancelledTitle")}>
          {data.certificate_type === "transfer" ? td("cancelledBodyTc") : td("cancelledBody")}
        </Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <div className="space-y-6">
          {data.content ? (
            <Card title={td("printedTitle")} description={td("printedBody")}>
              <Lines lines={data.content.fields} locale={locale} />
              <Lines lines={data.content.details} locale={locale} />
            </Card>
          ) : (
            <Card title={td("requestedTitle")} description={td("requestedBody")}>
              {inputs.length === 0 ? (
                <p className="text-sm text-ink-muted">{td("noInputs")}</p>
              ) : (
                <dl className="divide-y divide-border">
                  {inputs.map(([key, value]) => {
                    const input = catalog
                      ?.find((type) => type.key === data.certificate_type)
                      ?.inputs.find((one) => one.key === key);
                    return (
                      <div key={key} className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr] sm:gap-4">
                        <dt className="text-sm text-ink-muted">
                          {input ? t(`inputs.${input.key}` as "inputs.remarks") : key}
                        </dt>
                        <dd className="break-words text-ink">
                          {inputText(catalog, data, key, value, locale)}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
              )}
            </Card>
          )}
        </div>
        <div className="space-y-6">
          <Card title={td("timelineTitle")}>
            <Timeline label={td("timelineLabel")} items={steps} />
          </Card>
          {data.status !== "rejected" && data.status !== "withdrawn" ? (
            <Card title={td("printTitle")} description={issued ? td("printBody") : td("draftBody")}>
              <div className="flex flex-wrap items-start gap-3">
                <PrintViewLink certificateId={data.id} />
                {issued ? <DownloadPdfButton certificate={data} /> : null}
              </div>
              {issued && data.pdf_status === "failed" ? (
                <div className="mt-4">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => retry.mutate(undefined)}
                    disabled={retry.isPending}
                  >
                    {retry.isPending ? tc("working") : td("retryPdf")}
                  </Button>
                </div>
              ) : null}
            </Card>
          ) : null}
          {data.status === "issued" && (data.can_duplicate || data.can_cancel) ? (
            <Card title={td("issuedActionsTitle")} description={td("issuedActionsBody")}>
              <IssuedActions certificate={data} />
            </Card>
          ) : null}
        </div>
      </div>

      <nav aria-label={td("relatedLabel")} className="flex flex-wrap gap-2" data-print="hide">
        <ButtonLink href={`/students/${data.student_id}`} variant="secondary" size="sm">
          {td("openStudent")}
        </ButtonLink>
        <ButtonLink
          href={`/certificates?student_id=${data.student_id}`}
          variant="secondary"
          size="sm"
        >
          {td("allForStudent")}
        </ButtonLink>
        <ButtonLink href="/certificates" variant="ghost" size="sm">
          {td("backToList")}
        </ButtonLink>
      </nav>
    </div>
  );
}

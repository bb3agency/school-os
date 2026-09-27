"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { DQ_KEYS } from "@/features/findings/data";
import { SourceChip } from "@/features/findings/parts";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { CR_KEYS } from "./ChangeRequestsScreen";
import { ChangeRequestStatusBadge, DisplayValue, ExpiryText, fieldLabel } from "./parts";
import { CR_APPROVE, CR_REQUEST, ifMatch, TEXT_MAX, TEXT_MIN, type ChangeRequest } from "./types";

/** Decision note or rejection reason: 10–1000 characters (FR-CR-004). */
const decisionText = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(TEXT_MIN, { error: "reasonTooShort" })
  .max(TEXT_MAX, { error: "tooLong" });

const approveSchema = z.object({
  note: z
    .string()
    .trim()
    .refine((value) => value === "" || value.length >= TEXT_MIN, { error: "reasonTooShort" })
    .refine((value) => value.length <= TEXT_MAX, { error: "tooLong" })
    .transform((value) => (value === "" ? null : value)),
});
const rejectSchema = z.object({ reason: decisionText });

/** Student name and admission number for the header (needs `student.read_basic`). */
function useStudentLabel(studentId: string | null, enabled: boolean) {
  const api = useBffClient("staff");
  const query = useQuery({
    queryKey: ["staff", "students", "one", studentId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}", {
          params: { path: { student_id: studentId as string } },
        }),
      ),
    enabled: enabled && studentId !== null,
    staleTime: 60_000,
    retry: false,
  });
  const student = query.data;
  if (!student) return null;
  return {
    name: student.canonical.full_name?.value ?? null,
    admissionNo: student.admission_no,
  };
}

/** Evidence: a short-lived presigned download link (≤ 5 min), opened as a download. */
function EvidenceButton({ documentId }: { documentId: string }) {
  const t = useTranslations("changeRequests.detail");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  async function open() {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/documents/{document_id}/download-url", {
          params: { path: { document_id: documentId } },
        }),
      );
      window.location.assign(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="space-y-2">
      <Button variant="secondary" size="sm" onClick={() => void open()} disabled={pending}>
        {pending ? tc("working") : t("downloadEvidence")}
      </Button>
      <ApiErrorAlert error={error} namespace="changeRequests" />
    </div>
  );
}

function Decisions({ request, mine }: { request: ChangeRequest; mine: boolean }) {
  const t = useTranslations("changeRequests.detail");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const invalidate = [CR_KEYS.all, DQ_KEYS.findings, DQ_KEYS.summary] as const;
  const path = { change_request_id: request.id };
  const headers = { "If-Match": ifMatch(request.version) };
  // SEC-014 / FR-CR-002: the requester is never offered approve or reject (the API refuses too).
  const decide =
    request.status === "pending" && request.can_decide && !mine && can(CR_APPROVE);
  const cancel = request.status === "pending" && request.can_cancel && mine && can(CR_REQUEST);
  if (!decide && !cancel) return null;
  return (
    <>
      {decide ? (
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
            errorNamespace="changeRequests"
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/change-requests/{change_request_id}/approve", {
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
                maxLength={TEXT_MAX}
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
            schema={rejectSchema}
            invalidate={invalidate}
            errorNamespace="changeRequests"
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/change-requests/{change_request_id}/reject", {
                  params: { path },
                  headers,
                  body: input,
                }),
              )
            }
          >
            {(errors) => (
              <TextAreaField
                name="reason"
                label={t("rejectReason")}
                hint={t("rejectReasonHint")}
                error={errors.reason}
                maxLength={TEXT_MAX}
                rows={3}
              />
            )}
          </ActionDialog>
        </>
      ) : null}
      {cancel ? (
        <ActionDialog
          triggerLabel={t("cancel")}
          triggerVariant="secondary"
          title={t("cancelTitle")}
          description={t("cancelBody")}
          confirmLabel={t("cancel")}
          confirmVariant="danger"
          schema={z.object({})}
          invalidate={invalidate}
          errorNamespace="changeRequests"
          submit={() =>
            unwrap(
              api.POST("/api/v1/change-requests/{change_request_id}/cancel", {
                params: { path },
                headers,
              }),
            )
          }
        />
      ) : null}
    </>
  );
}

/**
 * One change request (US-601 AC1–AC4): old and new value (masked when the API says so),
 * source, reason, evidence, expiry and decision; approve/reject (step-up MFA, never by the
 * requester), cancel (requester only) and the printable correction memo.
 */
export function ChangeRequestDetailScreen({ changeRequestId }: { changeRequestId: string }) {
  const t = useTranslations("changeRequests");
  const td = useTranslations("changeRequests.detail");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const locale = useLocale();
  const api = useBffClient("staff");
  const me = useStaffMe();
  const can = useStaffCan();
  const request = useApiQuery(CR_KEYS.one(changeRequestId), () =>
    unwrap(
      api.GET("/api/v1/change-requests/{change_request_id}", {
        params: { path: { change_request_id: changeRequestId } },
      }),
    ),
  );
  const data = request.status === "ready" ? request.data : null;
  const student = useStudentLabel(data?.student_id ?? null, can("student.read_basic"));

  if (request.status === "loading") return <LoadingState label={tc("loading")} />;
  if (!data) {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {request.status === "error" && request.reason
          ? te(`load.${request.reason}`)
          : tc("loadErrorBody")}
      </Alert>
    );
  }
  const mine = me?.membership_id === data.requested_by;
  const memoHref = `/bff/api/v1/change-requests/${data.id}/memo`;

  return (
    <div className="space-y-6">
      <PageHeader
        title={td("title", { field: fieldLabel(data, locale) })}
        description={student?.name ?? undefined}
        badge={<ChangeRequestStatusBadge status={data.status} />}
        actions={<Decisions request={data} mine={mine} />}
      />

      {data.status === "pending" && mine ? (
        <Alert tone="info" title={td("ownRequestTitle")}>
          {td("ownRequestBody")}
        </Alert>
      ) : null}
      {data.status === "pending" && !mine && !data.can_decide && can(CR_APPROVE) ? (
        <Alert tone="info">{td("cannotDecide")}</Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <Card title={td("changeTitle")}>
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[max-content_1fr]">
            <dt className="font-semibold">{td("student")}</dt>
            <dd>
              <Value>{student?.name}</Value>
              {student?.admissionNo ? (
                <span className="ml-2 text-sm text-ink-muted">
                  {td("admissionNo", { number: student.admissionNo })}
                </span>
              ) : null}
            </dd>
            <dt className="font-semibold">{t("colField")}</dt>
            <dd>{fieldLabel(data, locale)}</dd>
            <dt className="font-semibold">{td("source")}</dt>
            <dd>
              <SourceChip source={data.target_source} />
            </dd>
            <dt className="font-semibold">{t("oldValue")}</dt>
            <dd>
              <DisplayValue
                value={data.old_value}
                masked={data.masked}
                attributeKey={data.attribute_key}
              />
            </dd>
            <dt className="font-semibold">{t("newValue")}</dt>
            <dd className="font-semibold">
              <DisplayValue
                value={data.new_value}
                masked={data.masked}
                attributeKey={data.attribute_key}
              />
            </dd>
            <dt className="font-semibold">{td("reason")}</dt>
            <dd className="whitespace-pre-line">{data.reason}</dd>
            <dt className="font-semibold">{td("evidence")}</dt>
            <dd>
              <EvidenceButton documentId={data.evidence_document_id} />
            </dd>
          </dl>
          {data.masked ? <p className="mt-4 text-sm text-ink-muted">{td("maskedNote")}</p> : null}
        </Card>

        <Card title={td("timelineTitle")}>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
            <dt className="font-semibold">{td("requestedAt")}</dt>
            <dd>
              <Value>{formatDateTime(data.requested_at)}</Value>
              <span className="block text-ink-muted">
                {mine ? t("byYou") : t("bySomeoneElse")}
              </span>
            </dd>
            {data.status === "pending" ? (
              <>
                <dt className="font-semibold">{td("expires")}</dt>
                <dd>
                  <ExpiryText request={data} />
                </dd>
              </>
            ) : null}
            {data.decided_at ? (
              <>
                <dt className="font-semibold">{td("decidedAt")}</dt>
                <dd>
                  <Value>{formatDateTime(data.decided_at)}</Value>
                </dd>
              </>
            ) : null}
            {data.decision_note ? (
              <>
                <dt className="font-semibold">
                  {data.status === "rejected" ? td("rejectReason") : td("decisionNote")}
                </dt>
                <dd className="whitespace-pre-line">{data.decision_note}</dd>
              </>
            ) : null}
          </dl>
          {data.status === "approved" ? (
            <p className="mt-4 text-sm">{td("approvedNote")}</p>
          ) : null}
          {data.status === "expired" ? (
            <p className="mt-4 text-sm">{td("expiredNote")}</p>
          ) : null}
        </Card>
      </div>

      <Card title={td("memoTitle")} description={td("memoBody")}>
        {/* The memo is an API page with its own strict CSP, served through the BFF. */}
        <a
          href={memoHref}
          target="_blank"
          rel="noopener noreferrer"
          className="font-semibold text-primary underline"
        >
          {td("openMemo")}
          <span className="sr-only"> ({td("newTab")})</span>
        </a>
      </Card>

      <p>
        <Link
          href={`/change-requests?student_id=${data.student_id}`}
          className="text-primary underline"
        >
          {td("allForStudent")}
        </Link>
        <span aria-hidden="true"> · </span>
        <Link href="/change-requests" className="text-primary underline">
          {td("backToList")}
        </Link>
      </p>
    </div>
  );
}

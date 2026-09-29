"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Avatar } from "@/components/ui/Avatar";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink, buttonClasses } from "@/components/ui/Button";
import { Card, cardClasses } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Icon } from "@/components/ui/Icon";
import { TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Timeline, type TimelineItem } from "@/components/ui/Timeline";
import { Value } from "@/components/ui/Value";
import { DQ_KEYS } from "@/features/findings/data";
import { SourceChip } from "@/features/findings/parts";
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
    .max(TEXT_MAX, { error: "tooLong" })
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

/**
 * Evidence (docs/07 §6.3: the approver sees an evidence preview). A short-lived presigned link
 * (≤ 5 min, `attachment`) is fetched on demand: images are shown on the page (img-src allows
 * the files origin), other files (PDF) are downloaded. The link is never stored.
 */
function EvidenceViewer({ documentId }: { documentId: string }) {
  const t = useTranslations("changeRequests.detail");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [image, setImage] = useState<string | null>(null);
  async function open() {
    setPending(true);
    setError(undefined);
    try {
      const link = await unwrap(
        api.GET("/api/v1/documents/{document_id}/download-url", {
          params: { path: { document_id: documentId } },
        }),
      );
      if (link.mime_type.startsWith("image/")) setImage(link.url);
      else window.location.assign(link.url);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="space-y-2">
      <Pill variant="tag">
        <Icon name="file" className="size-3.5" />
        {t("evidenceChip")}
      </Pill>
      {image ? (
        <figure className="space-y-2">
          {/* A short-lived presigned URL on the files origin (CSP img-src): next/image would
              proxy and cache it, which must not happen for evidence documents. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={image}
            alt={t("evidenceAlt")}
            className="max-h-96 max-w-full rounded-lg border border-border"
          />
          <figcaption className="text-xs text-ink-muted">{t("evidenceLinkNote")}</figcaption>
        </figure>
      ) : null}
      <Button variant="secondary" size="sm" onClick={() => void open()} disabled={pending}>
        {pending ? tc("working") : image ? t("reloadEvidence") : t("openEvidence")}
      </Button>
      <ApiErrorAlert error={error} namespace="changeRequests" />
    </div>
  );
}

/**
 * Approve and reject (`mode="decide"`, checkers only) or withdraw (`mode="cancel"`, the
 * requester only). The caller decides which one applies (SEC-014 / FR-CR-002).
 */
function Decisions({ request, mode }: { request: ChangeRequest; mode: "decide" | "cancel" }) {
  const t = useTranslations("changeRequests.detail");
  const api = useBffClient("staff");
  const invalidate = [CR_KEYS.all, DQ_KEYS.findings, DQ_KEYS.summary] as const;
  const path = { change_request_id: request.id };
  const headers = { "If-Match": ifMatch(request.version) };
  const decide = mode === "decide";
  const cancel = mode === "cancel";
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
  const tstatus = useTranslations("changeRequests.status");
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
  const crumbs = (last: string) => [
    { label: t("title"), href: "/change-requests" },
    { label: last },
  ];

  if (request.status === "loading") return <LoadingState label={tc("loading")} />;
  if (!data) {
    return (
      <div className="space-y-6">
        <PageHeader breadcrumb={crumbs(t("nav"))} title={t("nav")} />
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {request.status === "error" && request.reason
            ? te(`load.${request.reason}`)
            : tc("loadErrorBody")}
        </Alert>
      </div>
    );
  }
  const mine = me?.membership_id === data.requested_by;
  const memoHref = `/bff/api/v1/change-requests/${data.id}/memo`;
  // SEC-014 / FR-CR-002: the requester is never offered approve or reject (the API refuses too).
  const decide = data.status === "pending" && data.can_decide && !mine && can(CR_APPROVE);
  const cancel = data.status === "pending" && data.can_cancel && mine && can(CR_REQUEST);
  const field = fieldLabel(data, locale);

  const steps: TimelineItem[] = [
    {
      id: "asked",
      title: td("requestedAt"),
      time: <Value>{formatDateTime(data.requested_at)}</Value>,
      body: mine ? t("byYou") : t("bySomeoneElse"),
      status: "done",
    },
    data.status === "pending"
      ? {
          id: "waiting",
          title: td("waitingDecision"),
          body: <ExpiryText request={data} />,
          status: "current",
        }
      : {
          id: "decided",
          title: tstatus(data.status),
          ...(data.decided_at ? { time: <Value>{formatDateTime(data.decided_at)}</Value> } : {}),
          body: data.decision_note ? (
            <dl>
              <dt className="font-medium text-ink">
                {data.status === "rejected" ? td("rejectReason") : td("decisionNote")}
              </dt>
              <dd className="whitespace-pre-line">{data.decision_note}</dd>
            </dl>
          ) : undefined,
          status: "done",
        },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumb={crumbs(field)}
        title={td("title", { field })}
        description={student?.name ?? undefined}
        badge={<ChangeRequestStatusBadge status={data.status} size="md" />}
        actions={cancel ? <Decisions request={data} mode="cancel" /> : null}
      />

      {decide ? (
        <Card
          title={td("waitingDecision")}
          description={td("decideHint")}
          actions={<Decisions request={data} mode="decide" />}
        />
      ) : null}
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
          <div className="space-y-5">
            <div className="grid items-stretch gap-3 sm:grid-cols-[1fr_auto_1fr]">
              <div className={cardClasses({ padding: "sm", tone: "muted" })}>
                <Eyebrow as="p">{t("oldValue")}</Eyebrow>
                <p className="mt-2 font-mono text-lg break-words text-ink-muted">
                  <DisplayValue
                    value={data.old_value}
                    masked={data.masked}
                    attributeKey={data.attribute_key}
                  />
                </p>
              </div>
              <span
                aria-hidden="true"
                className="flex items-center justify-center text-ink-muted max-sm:rotate-90"
              >
                <Icon name="arrowRight" />
              </span>
              <div className={cardClasses({ padding: "sm", tone: "outline" })}>
                <Eyebrow as="p" tone="brand">
                  {t("newValue")}
                </Eyebrow>
                <p className="mt-2 font-mono text-lg font-medium break-words text-ink">
                  <DisplayValue
                    value={data.new_value}
                    masked={data.masked}
                    attributeKey={data.attribute_key}
                  />
                </p>
              </div>
            </div>
            <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-[max-content_1fr]">
              <dt className="text-sm text-ink-muted">{td("student")}</dt>
              <dd className="flex flex-wrap items-center gap-2">
                {student?.name ? <Avatar name={student.name} size="sm" decorative /> : null}
                <span className="font-medium">
                  <Value>{student?.name}</Value>
                </span>
                {student?.admissionNo ? (
                  <Pill variant="tag">
                    <span className="font-mono">
                      {td("admissionNo", { number: student.admissionNo })}
                    </span>
                  </Pill>
                ) : null}
              </dd>
              <dt className="text-sm text-ink-muted">{t("colField")}</dt>
              <dd>{field}</dd>
              <dt className="text-sm text-ink-muted">{td("source")}</dt>
              <dd>
                <SourceChip source={data.target_source} />
              </dd>
              <dt className="text-sm text-ink-muted">{td("reason")}</dt>
              <dd className="whitespace-pre-line">{data.reason}</dd>
              <dt className="text-sm text-ink-muted">{td("evidence")}</dt>
              <dd>
                <EvidenceViewer documentId={data.evidence_document_id} />
              </dd>
            </dl>
            {data.masked ? <p className="text-sm text-ink-muted">{td("maskedNote")}</p> : null}
          </div>
        </Card>

        <div className="space-y-6">
          <Card title={td("timelineTitle")}>
            <Timeline label={td("timelineLabel")} items={steps} />
            {data.status === "approved" ? (
              <p className="mt-4 text-sm">{td("approvedNote")}</p>
            ) : null}
            {data.status === "expired" ? <p className="mt-4 text-sm">{td("expiredNote")}</p> : null}
          </Card>

          <Card title={td("memoTitle")} description={td("memoBody")}>
            {/* The memo is an API page with its own strict CSP, served through the BFF. */}
            <a
              href={memoHref}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonClasses("secondary", "md")}
            >
              <Icon name="file" className="size-4" />
              {td("openMemo")}
              <span className="sr-only"> ({td("newTab")})</span>
            </a>
          </Card>
        </div>
      </div>

      <nav aria-label={td("relatedLabel")} className="flex flex-wrap gap-2" data-print="hide">
        <ButtonLink
          href={`/change-requests?student_id=${data.student_id}`}
          variant="secondary"
          size="sm"
        >
          {td("allForStudent")}
        </ButtonLink>
        <ButtonLink href="/change-requests" variant="ghost" size="sm">
          {td("backToList")}
        </ButtonLink>
      </nav>
    </div>
  );
}

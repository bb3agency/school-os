"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { todayInIndia } from "@/features/change-requests/dates";
import { StudentCombobox, type PickedStudent } from "@/features/privacy/SubjectPicker";
import { Link } from "@/i18n/navigation";
import { toLoadable, useApiMutation, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDate, formatDateTime } from "@/lib/format";
import * as z from "@/lib/zod";
import { CERT_ISSUE, REASON_MAX, REASON_MIN } from "../types";
import {
  LEAVING_REASONS,
  TC_REQUEST_CHANNELS,
  TC_REQUEST_FILTERS,
  TC_REQUEST_KEYS,
  fetchRequests,
  logRequest,
  logRequestBody,
  logRequestSchema,
  moveRequest,
  prepareHref,
  type TcRequest,
  type TcRequestFilter,
  type TcRequestStatus,
} from "./data";

const PAGE_SIZE = 50;

const statusPill: Record<TcRequestStatus, PillVariant> = {
  received: "progress",
  in_preparation: "review",
  ready: "positive",
  issued: "done",
  cancelled: "negative",
};

const cancelSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .min(REASON_MIN, { error: "reasonTooShort" })
    .max(REASON_MAX, { error: "tooLong" }),
});
const confirmSchema = z.object({});

/**
 * TC requests (FO-09; FR-CERT-026..FR-CERT-044): requests that reached the office on paper,
 * by phone or in person, newest first, with overdue requests flagged. The clerk logs a request,
 * prepares the TC from it (the issue screen suggests the application date, leaving date and
 * reason), records the hand-over and cancels with a reason. The request moves to "in
 * preparation" and "ready" by itself when its TC is prepared and issued. Dues never block a TC
 * (owner decision D16).
 */
export function TcRequestsScreen() {
  const t = useTranslations("certificates.tcRequests");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const mayIssue = can(CERT_ISSUE);
  const [filter, setFilter] = useState<TcRequestFilter>("");
  const list = useInfiniteQuery({
    queryKey: TC_REQUEST_KEYS.list(filter),
    queryFn: ({ pageParam }) => fetchRequests(api, filter, pageParam, PAGE_SIZE),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });
  const first = list.data?.pages[0];
  const rows = list.data?.pages.flatMap((page) => page.data) ?? [];
  const invalidate = [TC_REQUEST_KEYS.all];
  const prepare = useApiMutation(
    (row: TcRequest) => moveRequest(api, row, { status: "in_preparation" }),
    { invalidate },
  );

  const columns: ColumnV2<TcRequest>[] = [
    {
      key: "received",
      stack: "field",
      header: t("colReceived"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="num whitespace-nowrap">
            <Value>{formatDate(row.received_on)}</Value>
          </span>
          <span className="text-xs text-ink-muted">{t(`channels.${row.channel}`)}</span>
        </span>
      ),
    },
    {
      key: "student",
      stack: "title",
      header: t("colStudent"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-anywhere">
            <Value>{row.student_name}</Value>
          </span>
          <span className="text-xs text-ink-muted">
            {[row.admission_no, row.class_label].filter(Boolean).join(" · ")}
          </span>
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <Pill variant={statusPill[row.status]}>{t(`status.${row.status}`)}</Pill>
          {row.overdue ? <Pill variant="negative">{t("overdue")}</Pill> : null}
          <span className="text-xs text-ink-muted">{stepLine(row, t)}</span>
        </span>
      ),
    },
    {
      key: "days",
      header: t("colDays"),
      cell: (row) => (
        <span className="num whitespace-nowrap">{t("days", { count: row.days_open })}</span>
      ),
    },
    {
      key: "actions",
      stack: "actions",
      header: tc("actions"),
      cell: (row) => <RowActions row={row} mayIssue={mayIssue} onPrepare={prepare.mutate} />,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: t("crumbHome"), href: "/" },
          { label: t("crumbCertificates"), href: "/certificates" },
          { label: t("title") },
        ]}
        actions={mayIssue ? <LogRequestDialog /> : null}
      />
      {first && first.overdue_count > 0 ? (
        <Alert tone="warning" title={t("overdueTitle", { count: first.overdue_count })}>
          <p>
            {t("overdueBody", { days: first.overdue_after_days })}
            {first.overdue_verified ? null : ` ${t("overdueUnverified")}`}
          </p>
          {filter !== "overdue" ? (
            <Button variant="secondary" size="sm" onClick={() => setFilter("overdue")}>
              {t("showOverdue")}
            </Button>
          ) : null}
        </Alert>
      ) : null}
      <p className="text-sm text-ink-muted">{t("duesNote")}</p>
      <ApiErrorAlert error={prepare.error} namespace="certificates" />
      <DataTableV2
        id="tc-requests"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({
          ...list,
          isError: list.isError && !list.isFetchNextPageError,
          data: rows,
        })}
        rowKey={(row) => row.id}
        toolbar={
          <SegmentedControl
            legend={t("filter")}
            legendVisible
            size="sm"
            value={filter}
            onValueChange={(value) => {
              const next = TC_REQUEST_FILTERS.find((item) => item === value);
              if (next !== undefined) setFilter(next);
            }}
            options={TC_REQUEST_FILTERS.map((value) => ({
              value,
              label: value === "" ? tc("all") : t(`filters.${value}`),
            }))}
          />
        }
        emptyTitle={t("emptyTitle")}
        emptyBody={mayIssue ? t("emptyBodyIssuer") : t("emptyBody")}
        noResultsTitle={t("emptyTitle")}
        onRetry={() => void list.refetch()}
        footer={
          list.hasNextPage ? (
            <div className="flex flex-col items-center gap-2">
              {list.isFetchNextPageError ? <ApiErrorAlert error={list.error} /> : null}
              <Button
                variant="secondary"
                onClick={() => void list.fetchNextPage()}
                disabled={list.isFetchingNextPage}
              >
                {list.isFetchingNextPage ? tc("loading") : t("showMore")}
              </Button>
            </div>
          ) : undefined
        }
      />
    </div>
  );
}

type Translate = ReturnType<typeof useTranslations<"certificates.tcRequests">>;

type StepKey = "stepIssued" | "stepCancelled" | "stepReady" | "stepPreparing" | "stepReceived";

/** The latest step in words: "Issued on 12/11/2026 10:30 by Principal Suresh". */
function stepLine(row: TcRequest, t: Translate): string {
  const step: [StepKey, string | null, string | null] =
    row.status === "issued"
      ? ["stepIssued", row.issued_at, row.issued_by_name]
      : row.status === "cancelled"
        ? ["stepCancelled", row.cancelled_at, row.cancelled_by_name]
        : row.status === "ready"
          ? ["stepReady", row.ready_at, row.ready_by_name]
          : row.status === "in_preparation"
            ? ["stepPreparing", row.preparing_at, row.preparing_by_name]
            : ["stepReceived", row.received_at, row.received_by_name];
  const [key, at, who] = step;
  return t(key, { date: formatDateTime(at) ?? "—", who: who ?? t("someone") });
}

function RowActions({
  row,
  mayIssue,
  onPrepare,
}: {
  row: TcRequest;
  mayIssue: boolean;
  onPrepare: (row: TcRequest) => void;
}) {
  const t = useTranslations("certificates.tcRequests");
  const api = useBffClient("staff");
  const invalidate = [TC_REQUEST_KEYS.all];
  const who = row.student_name ?? row.admission_no ?? "";
  const open = row.status === "received" || row.status === "in_preparation";
  return (
    <span className="flex flex-wrap items-center gap-2">
      {row.certificate_id ? (
        <Link
          href={`/certificates/${row.certificate_id}`}
          className="font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
        >
          {row.certificate_serial ?? t("openTc")}
          <span className="sr-only">: {who}</span>
        </Link>
      ) : null}
      {mayIssue && open && !row.certificate_id ? (
        <ButtonLink href={prepareHref(row)} size="sm" variant="secondary">
          {t("prepareTc")}
          <span className="sr-only">: {who}</span>
        </ButtonLink>
      ) : null}
      {row.can_prepare ? (
        <Button variant="ghost" size="sm" onClick={() => onPrepare(row)}>
          {t("markPreparing")}
          <span className="sr-only">: {who}</span>
        </Button>
      ) : null}
      {row.can_hand_over ? (
        <ActionDialog
          triggerLabel={t("handOver")}
          triggerSize="sm"
          triggerVariant="primary"
          title={t("handOverTitle")}
          description={t("handOverBody", { name: who })}
          confirmLabel={t("handOver")}
          schema={confirmSchema}
          invalidate={invalidate}
          errorNamespace="certificates"
          submit={() => moveRequest(api, row, { status: "issued" })}
        />
      ) : null}
      {row.can_cancel ? (
        <ActionDialog
          triggerLabel={t("cancel")}
          triggerSize="sm"
          triggerVariant="ghost"
          title={t("cancelTitle")}
          description={t("cancelBody", { name: who })}
          confirmLabel={t("cancel")}
          confirmVariant="danger"
          consequence={t("cancelConsequence")}
          schema={cancelSchema}
          invalidate={invalidate}
          errorNamespace="certificates"
          submit={(input) => moveRequest(api, row, { status: "cancelled", reason: input.reason })}
        >
          {(errors) => (
            <TextAreaField
              name="reason"
              label={t("cancelReason")}
              hint={t("cancelReasonHint", { min: REASON_MIN })}
              error={errors.reason}
              maxLength={REASON_MAX}
              required
            />
          )}
        </ActionDialog>
      ) : null}
    </span>
  );
}

function LogRequestDialog() {
  const t = useTranslations("certificates.tcRequests");
  const tcm = useTranslations("common");
  const api = useBffClient("staff");
  const [student, setStudent] = useState<PickedStudent | null>(null);
  const today = todayInIndia();
  return (
    <ActionDialog
      triggerLabel={t("log")}
      triggerVariant="primary"
      title={t("logTitle")}
      description={t("logBody")}
      confirmLabel={t("log")}
      schema={logRequestSchema}
      invalidate={[TC_REQUEST_KEYS.all]}
      errorNamespace="certificates"
      errorSummary
      onSuccess={() => setStudent(null)}
      submit={(input, key) => logRequest(api, logRequestBody(input), key)}
    >
      {(errors) => (
        <div className="space-y-4">
          {student ? (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-muted px-3 py-2">
              <span className="min-w-0 font-semibold break-words text-ink">{student.label}</span>
              <input type="hidden" name="student_id" value={student.id} />
              <Button variant="ghost" size="sm" onClick={() => setStudent(null)}>
                {t("changeStudent")}
              </Button>
            </div>
          ) : (
            <StudentCombobox
              fieldId="tc-request-student"
              error={errors.student_id}
              onPick={setStudent}
            />
          )}
          <TextField
            name="received_on"
            type="date"
            label={t("receivedOn")}
            hint={t("receivedOnHint")}
            defaultValue={today}
            max={today}
            error={errors.received_on}
            required
          />
          <SelectField
            name="channel"
            label={t("channel")}
            defaultValue="paper"
            error={errors.channel}
            options={TC_REQUEST_CHANNELS.map((value) => ({
              value,
              label: t(`channels.${value}`),
            }))}
          />
          <TextField
            name="leaving_date"
            type="date"
            label={t("leavingDate")}
            hint={t("optional")}
            error={errors.leaving_date}
          />
          <SelectField
            name="leaving_reason"
            label={t("leavingReason")}
            hint={t("optional")}
            defaultValue=""
            placeholder={tcm("chooseOne")}
            error={errors.leaving_reason}
            options={LEAVING_REASONS.map((value) => ({ value, label: t(`reasons.${value}`) }))}
          />
        </div>
      )}
    </ActionDialog>
  );
}

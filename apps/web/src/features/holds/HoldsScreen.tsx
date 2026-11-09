"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { StudentCombobox, type PickedStudent } from "@/features/privacy/SubjectPicker";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import {
  HOLD_KEYS,
  HOLD_MANAGE,
  actOnHold,
  actionsFor,
  fetchHolds,
  placeHold,
  placeSchema,
  type Hold,
  type HoldAction,
  type HoldList,
  type HoldStatus,
} from "./data";
import * as z from "@/lib/zod";

const STATUS_VARIANTS: Record<HoldStatus, PillVariant> = {
  pending: "review",
  active: "negative",
  lift_pending: "progress",
  lifted: "done",
  rejected: "tag",
};

function StatusPill({ status }: { status: HoldStatus }) {
  const t = useTranslations("holds.status");
  return <Pill variant={STATUS_VARIANTS[status]}>{t(status)}</Pill>;
}

/** The place dialog (FR-DPDP-039..042): scope, student or record class, reason, review date. */
function PlaceDialog({ options }: { options: HoldList }) {
  const t = useTranslations("holds.place");
  const api = useBffClient("staff");
  const [scope, setScope] = useState<"student" | "record_class">("student");
  const [student, setStudent] = useState<PickedStudent | null>(null);
  const studentField = useId();
  const today = new Date().toISOString().slice(0, 10);
  return (
    <ActionDialog
      triggerLabel={t("trigger")}
      triggerVariant="primary"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("confirm")}
      stepUp
      note={t("makerChecker")}
      consequence={t("consequence")}
      schema={placeSchema}
      invalidate={[HOLD_KEYS.list]}
      errorNamespace="holds"
      errorSummary
      submit={(data) => placeHold(api, data)}
      onSuccess={() => setStudent(null)}
    >
      {(errors) => (
        <div className="space-y-4">
          <SegmentedControl
            legend={t("scopeLegend")}
            legendVisible
            name="scope"
            value={scope}
            onValueChange={(value) =>
              setScope(value === "record_class" ? "record_class" : "student")
            }
            options={[
              { value: "student", label: t("scopes.student") },
              { value: "record_class", label: t("scopes.record_class") },
            ]}
          />
          {scope === "student" ? (
            student ? (
              <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-muted px-3 py-2">
                <span className="min-w-0 font-semibold break-words text-ink">{student.label}</span>
                <input type="hidden" name="student_id" value={student.id} />
                <Button variant="ghost" size="sm" onClick={() => setStudent(null)}>
                  {t("changeStudent")}
                </Button>
              </div>
            ) : (
              <StudentCombobox
                fieldId={studentField}
                error={errors.student_id}
                onPick={setStudent}
              />
            )
          ) : (
            <SelectField
              label={t("recordClass")}
              hint={t("recordClassHint")}
              name="record_class"
              placeholder={t("choose")}
              options={options.record_classes.map((o) => ({ value: o.code, label: o.label }))}
              error={errors.record_class}
              required
            />
          )}
          <SelectField
            label={t("reason")}
            name="reason_code"
            placeholder={t("choose")}
            options={options.reason_codes.map((o) => ({ value: o.code, label: o.label }))}
            error={errors.reason_code}
            required
          />
          <TextField
            label={t("reference")}
            hint={t("referenceHint")}
            name="reference"
            maxLength={200}
            autoComplete="off"
            error={errors.reference}
          />
          <TextField
            label={t("reviewOn")}
            hint={t("reviewOnHint", { date: formatDate(options.max_review_on) ?? "" })}
            name="review_on"
            type="date"
            min={today}
            max={options.max_review_on}
            error={errors.review_on}
            required
          />
        </div>
      )}
    </ActionDialog>
  );
}

/**
 * Legal holds (FC-12, ADR-0059, FR-DPDP-039..052): what the school must keep for a legal
 * reason. Only owners and principals (`privacy.hold.manage`) see this page. Placing and lifting
 * each take two people: one asks, another approves (both with MFA). While a hold applies,
 * retention and erasure skip what it covers; access reports only say "Held: yes".
 */
export function HoldsScreen() {
  const t = useTranslations("holds");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(HOLD_MANAGE);
  const [pending, setPending] = useState<{ hold: Hold; action: HoldAction } | null>(null);
  const list = useQuery({
    queryKey: HOLD_KEYS.list,
    queryFn: () => fetchHolds(api),
    enabled: allowed,
    retry: false,
  });

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={allowed && list.data ? <PlaceDialog options={list.data} /> : undefined}
    />
  );
  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const rows = list.data?.items ?? [];
  const columns: ColumnV2<Hold>[] = [
    {
      key: "subject",
      stack: "title",
      header: t("colSubject"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-words text-ink">{row.subject_label}</span>
          <span className="text-xs text-ink-muted">{t(`scopes.${row.scope}`)}</span>
        </span>
      ),
    },
    {
      key: "reason",
      header: t("colReason"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5 text-sm">
          <span>{row.reason_label}</span>
          {row.reference ? (
            <span className="break-words text-ink-muted">{row.reference}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "review",
      header: t("colReview"),
      cell: (row) =>
        row.review_overdue ? (
          <Pill variant="negative" icon="alert">
            {t("overdue", { date: formatDate(row.review_on) ?? row.review_on })}
          </Pill>
        ) : (
          <span className="num text-sm whitespace-nowrap">{formatDate(row.review_on)}</span>
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <StatusPill status={row.status} />
          {row.status === "pending" && row.placed_by_name ? (
            <span className="text-xs text-ink-muted">
              {t("askedBy", { name: row.placed_by_name })}
            </span>
          ) : null}
          {row.status === "lift_pending" && row.lift_requested_by_name ? (
            <span className="text-xs text-ink-muted">
              {t("liftAskedBy", { name: row.lift_requested_by_name })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "actions",
      header: t("colActions"),
      cell: (row) => (
        <span className="flex flex-wrap gap-2">
          {actionsFor(row).map((action) => (
            <Button
              key={action}
              size="sm"
              variant={action === "approve" || action === "approve-lift" ? "primary" : "secondary"}
              onClick={() => setPending({ hold: row, action })}
              aria-label={t(`actions.${action}.aria`, { subject: row.subject_label })}
            >
              {t(`actions.${action}.label`)}
            </Button>
          ))}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      {header}
      <Alert tone="info" title={t("aboutTitle")}>
        {t("aboutBody")}
      </Alert>
      <DataTableV2
        id="privacy-holds"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void list.refetch()}
      />
      <ActionDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        title={pending ? t(`actions.${pending.action}.title`) : ""}
        description={pending?.hold.subject_label}
        confirmLabel={pending ? t(`actions.${pending.action}.label`) : ""}
        confirmVariant={pending?.action === "approve-lift" ? "danger" : "primary"}
        stepUp
        consequence={pending ? t(`actions.${pending.action}.consequence`) : undefined}
        schema={z.object({})}
        invalidate={[HOLD_KEYS.list]}
        errorNamespace="holds"
        submit={() => {
          if (!pending) return Promise.reject(new Error("no hold"));
          return actOnHold(api, pending.hold, pending.action);
        }}
      />
    </div>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { todayInIndia } from "@/features/change-requests/dates";
import { Link } from "@/i18n/navigation";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  ADMISSION_CHANNELS,
  MARK_ITEMS,
  READINESS_KEYS,
  readinessApi,
  type AdmissionPending,
  type ItemKey,
  type MarkItem,
  type ReadinessItem,
} from "./readiness-api";
import { PRIVACY_MANAGE, PRIVACY_READ } from "./types";

const TONE: Record<ReadinessItem["status"], BadgeTone> = {
  done: "success",
  todo: "warning",
  not_applicable: "neutral",
};

/** Where the school does each item that SchoolOS computes (the marks are recorded here). */
const ITEM_HREF: Partial<Record<ItemKey, string>> = {
  privacy_contact: "/settings/privacy",
  notices: "/privacy/notices",
  test_request: "/privacy/requests",
  consent_forms: "/consents",
};

function isMark(key: ItemKey): key is MarkItem {
  return (MARK_ITEMS as readonly string[]).includes(key);
}

/** A typed date (DD/MM/YYYY), not in the future. */
export function buildDateSchema(field: string, today = todayInIndia()) {
  return z
    .object({ [field]: z.string().trim().min(1, { error: "required" }) })
    .superRefine((data, context) => {
      const iso = typedDateToIso(String(data[field] ?? ""));
      if (!iso || iso > today) {
        context.addIssue({ code: "custom", path: [field], message: "invalidDate" });
      }
    });
}

export function buildAdmissionSchema(today = todayInIndia()) {
  return z
    .object({
      given_on: z.string().trim().min(1, { error: "required" }),
      channel: z.enum(ADMISSION_CHANNELS, { error: "chooseOption" }),
    })
    .superRefine((data, context) => {
      const iso = typedDateToIso(data.given_on);
      if (!iso || iso > today) {
        context.addIssue({ code: "custom", path: ["given_on"], message: "invalidDate" });
      }
    });
}

/**
 * DPDP readiness (FC-03; FR-DPDP-053..060): the checklist computed from the school's records,
 * the four steps the school records here, and the families admitted after the school-wide
 * notice who still need theirs. Not legal advice (D30).
 */
export function ReadinessScreen() {
  const t = useTranslations("privacy.readiness");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const mePending = useStaffMeQuery().isPending;
  const readable = can(PRIVACY_READ);
  const manage = can(PRIVACY_MANAGE);
  const [marking, setMarking] = useState<MarkItem | null>(null);
  const [recording, setRecording] = useState<string[] | null>(null);

  const readiness = useQuery({
    queryKey: READINESS_KEYS.readiness,
    queryFn: () => readinessApi.readiness(api),
    enabled: readable,
    retry: false,
  });
  const pending = useQuery({
    queryKey: READINESS_KEYS.pending,
    queryFn: () => readinessApi.pending(api),
    enabled: readable,
    retry: false,
  });

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  if (!mePending && !readable) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const data = readiness.data;
  return (
    <div className="space-y-5">
      {header}
      <Alert tone="info" title={t("notLegalAdviceTitle")}>
        {t("notLegalAdvice")}
      </Alert>
      <section aria-labelledby="dpdp-checklist" className="space-y-3">
        <h2 id="dpdp-checklist" className="text-lg font-semibold text-ink">
          {data
            ? t("progress", { done: data.done, applicable: data.applicable })
            : t("checklistTitle")}
        </h2>
        {readiness.isPending ? <LoadingState label={t("loading")} rows={4} /> : null}
        {readiness.isError ? (
          <Alert tone="danger" title={t("loadErrorTitle")}>
            <Button size="sm" variant="secondary" onClick={() => void readiness.refetch()}>
              {t("retry")}
            </Button>
          </Alert>
        ) : null}
        {data ? (
          <ul className="grid gap-3">
            {data.items.map((item) => (
              <li key={item.key}>
                <ChecklistItem
                  item={item}
                  onRecord={
                    manage && isMark(item.key) ? () => setMarking(item.key as MarkItem) : null
                  }
                />
              </li>
            ))}
          </ul>
        ) : null}
      </section>
      <AdmissionSection
        pending={pending.data}
        loadState={toLoadable({ ...pending, data: pending.data?.items ?? [] })}
        onRetry={() => void pending.refetch()}
        onRecord={manage ? setRecording : null}
      />
      {marking ? (
        <MarkDialog item={marking} open onOpenChange={(open) => !open && setMarking(null)} />
      ) : null}
      {recording ? (
        <AdmissionDialog
          studentIds={recording}
          open
          onOpenChange={(open) => !open && setRecording(null)}
        />
      ) : null}
    </div>
  );
}

function ChecklistItem({ item, onRecord }: { item: ReadinessItem; onRecord: (() => void) | null }) {
  const t = useTranslations("privacy.readiness");
  const href = ITEM_HREF[item.key];
  const doneOn = typeof item.detail.done_on === "string" ? item.detail.done_on : null;
  return (
    <Card className="flex flex-wrap items-start justify-between gap-3 p-4">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-semibold text-ink">{t(`items.${item.key}.title`)}</h3>
          <Badge tone={TONE[item.status]}>{t(`status.${item.status}`)}</Badge>
        </div>
        <p className="text-sm text-ink-muted">{t(`items.${item.key}.help`)}</p>
        {doneOn ? (
          <p className="text-sm">
            {t("recordedOn", { date: formatDate(doneOn) ?? doneOn })}
            {item.mark?.recorded_by_name ? ` · ${item.mark.recorded_by_name}` : ""}
          </p>
        ) : null}
        {item.key === "test_request" ? (
          <p className="text-sm">
            {t("closedRequests", { count: Number(item.detail.closed_requests ?? 0) })}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        {href ? (
          <Link href={href} className="text-sm font-semibold text-primary underline">
            {t(`items.${item.key}.link`)}
          </Link>
        ) : null}
        {onRecord ? (
          <Button size="sm" variant="secondary" onClick={onRecord}>
            {t("record")}
            <span className="sr-only">: {t(`items.${item.key}.title`)}</span>
          </Button>
        ) : null}
      </div>
    </Card>
  );
}

type PendingRow = AdmissionPending["items"][number];

function AdmissionSection({
  pending,
  loadState,
  onRetry,
  onRecord,
}: {
  pending: AdmissionPending | undefined;
  loadState: ReturnType<typeof toLoadable<PendingRow[]>>;
  onRetry: () => void;
  onRecord: ((ids: string[]) => void) | null;
}) {
  const t = useTranslations("privacy.readiness.admission");
  const columns: ColumnV2<PendingRow>[] = [
    {
      key: "name",
      stack: "title",
      header: t("colStudent"),
      cell: (row) => (
        <span className="flex flex-col">
          <Value>{row.display_name}</Value>
          <span className="num text-xs text-ink-muted">
            <Value>{row.admission_no}</Value>
          </span>
        </span>
      ),
    },
    { key: "class", header: t("colClass"), cell: (row) => <Value>{row.class_section}</Value> },
    {
      key: "added",
      header: t("colAdded"),
      cell: (row) => <span className="num">{formatDate(row.added_on)}</span>,
    },
  ];
  if (onRecord) {
    columns.push({
      key: "actions",
      stack: "actions",
      header: t("colActions"),
      cell: (row) => (
        <Button size="sm" variant="secondary" onClick={() => onRecord([row.student_id])}>
          {t("recordOne")}
          <span className="sr-only">: {row.display_name ?? row.admission_no ?? ""}</span>
        </Button>
      ),
    });
  }
  const rows = pending?.items ?? [];
  return (
    <section aria-labelledby="dpdp-admission" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="dpdp-admission" className="text-lg font-semibold text-ink">
            {t("title")}
          </h2>
          <p className="text-sm text-ink-muted">
            {pending?.since
              ? t("summary", {
                  count: pending.without_notice,
                  since: formatDate(pending.since) ?? pending.since,
                })
              : t("noNotice")}
          </p>
          {pending && pending.hidden > 0 ? (
            <p className="text-sm text-ink-muted">{t("hidden", { count: pending.hidden })}</p>
          ) : null}
        </div>
        {onRecord && rows.length > 1 ? (
          <Button onClick={() => onRecord(rows.map((row) => row.student_id))}>
            {t("recordAll", { count: rows.length })}
          </Button>
        ) : null}
      </div>
      <DataTableV2
        id="dpdp-admission-table"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={loadState}
        rowKey={(row) => row.student_id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={onRetry}
      />
    </section>
  );
}

function MarkDialog({
  item,
  open,
  onOpenChange,
}: {
  item: MarkItem;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("privacy.readiness");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const dates = useDateInput();
  const base = useId();
  return (
    <ActionDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("markTitle", { item: t(`items.${item}.title`) })}
      description={t("markBody")}
      confirmLabel={t("markConfirm")}
      schema={buildDateSchema("done_on")}
      invalidate={[READINESS_KEYS.readiness]}
      errorNamespace="privacy"
      submit={(input, key) =>
        readinessApi.recordMark(
          api,
          { item, done_on: typedDateToIso(String(input.done_on)) ?? String(input.done_on) },
          key,
        )
      }
      onSuccess={() => onOpenChange(false)}
    >
      {(errors) => (
        <TextField
          id={`${base}-done`}
          name="done_on"
          label={t("fieldDoneOn")}
          hint={tc("dateHintFormat", dates.hint("2026-10-21"))}
          error={errors.done_on}
          defaultValue={dates.fromIso(todayInIndia())}
          inputMode="numeric"
          placeholder={dates.placeholder}
          autoComplete="off"
          className="num"
        />
      )}
    </ActionDialog>
  );
}

function AdmissionDialog({
  studentIds,
  open,
  onOpenChange,
}: {
  studentIds: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("privacy.readiness.admission");
  const tch = useTranslations("privacy.readiness.channels");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const dates = useDateInput();
  const base = useId();
  return (
    <ActionDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("dialogTitle", { count: studentIds.length })}
      description={t("dialogBody")}
      confirmLabel={t("dialogConfirm")}
      schema={buildAdmissionSchema()}
      invalidate={[READINESS_KEYS.readiness, READINESS_KEYS.pending]}
      errorNamespace="privacy"
      submit={(input, key) =>
        readinessApi.recordAdmission(
          api,
          {
            student_ids: studentIds,
            given_on: typedDateToIso(input.given_on) ?? input.given_on,
            channel: input.channel,
          },
          key,
        )
      }
      onSuccess={() => onOpenChange(false)}
    >
      {(errors) => (
        <div className="space-y-4">
          <TextField
            id={`${base}-given`}
            name="given_on"
            label={t("fieldGivenOn")}
            hint={tc("dateHintFormat", dates.hint("2026-10-21"))}
            error={errors.given_on}
            defaultValue={dates.fromIso(todayInIndia())}
            inputMode="numeric"
            placeholder={dates.placeholder}
            autoComplete="off"
            className="num"
          />
          <SelectField
            id={`${base}-channel`}
            name="channel"
            label={t("fieldChannel")}
            error={errors.channel}
            defaultValue=""
            placeholder={tc("chooseOne")}
            options={ADMISSION_CHANNELS.map((value) => ({ value, label: tch(value) }))}
          />
        </div>
      )}
    </ActionDialog>
  );
}

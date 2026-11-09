"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { KpiCard } from "@/components/ui/StatCard";
import { Value } from "@/components/ui/Value";
import { OwnerBadge } from "@/features/readiness/parts";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatDate } from "@/lib/format";
import {
  CHANNELS_BY_EVENT,
  EVENTS_BY_OWNER,
  FOLLOWUP_KEYS,
  FOLLOWUP_OWNERS,
  READINESS_MANAGE,
  fetchFollowups,
  ownerCounts,
  recordStep,
  stepFormSchema,
  type FollowupEvent,
  type FollowupItem,
  type FollowupState,
} from "./data";

const STATE_VARIANTS: Record<FollowupState, PillVariant> = {
  overdue: "negative",
  not_started: "review",
  update_reported: "progress",
  waiting: "tag",
};

export function FollowupStatePill({ item }: { item: FollowupItem }) {
  const t = useTranslations("udiseFollowup.state");
  return (
    <Pill
      variant={STATE_VARIANTS[item.state]}
      {...(item.state === "overdue" ? { icon: "alert" as const } : {})}
    >
      {item.state === "overdue" && item.overdue_reason
        ? t(`overdue_${item.overdue_reason}`)
        : t(item.state)}
    </Pill>
  );
}

type Filter = "all" | "overdue" | "not_started";

/** The record-a-step dialog (FR-UDI-025..027): the step, how (channel), when, a promised date. */
function RecordStepDialog({
  profileKey,
  item,
  onClose,
}: {
  profileKey: string;
  item: FollowupItem | null;
  onClose: () => void;
}) {
  const t = useTranslations("udiseFollowup.record");
  const te = useTranslations("udiseFollowup.event");
  const tch = useTranslations("udiseFollowup.channel");
  const api = useBffClient("staff");
  const owner = item?.fix_owner ?? "parent_aadhaar";
  const events = EVENTS_BY_OWNER[owner];
  const [event, setEvent] = useState<FollowupEvent>(events[0]!);
  const today = new Date().toISOString().slice(0, 10);
  const channels = CHANNELS_BY_EVENT[event];
  return (
    <ActionDialog
      open={item !== null}
      onOpenChange={(open) => {
        if (!open) {
          setEvent(events[0]!);
          onClose();
        }
      }}
      title={t("title")}
      description={item?.student.display_name ?? undefined}
      confirmLabel={t("confirm")}
      note={t("note")}
      schema={stepFormSchema}
      invalidate={[FOLLOWUP_KEYS.all]}
      errorNamespace="udiseFollowup"
      errorSummary
      submit={(data, key) => {
        if (!item) return Promise.reject(new Error("no follow-up"));
        return recordStep(
          api,
          profileKey,
          {
            studentIds: [item.student.id],
            owner,
            event: data.event,
            channel: data.channel,
            occurredOn: data.occurred_on,
            promisedOn: data.promised_on,
          },
          key,
        );
      }}
    >
      {(errors) => (
        <div className="space-y-4">
          <SelectField
            label={t("event")}
            name="event"
            value={event}
            onChange={(e) => setEvent(e.target.value as FollowupEvent)}
            options={events.map((value) => ({ value, label: te(value) }))}
            error={errors.event}
            required
          />
          {channels.length > 0 ? (
            <SelectField
              label={t("channel")}
              hint={t("channelHint")}
              name="channel"
              placeholder={t("choose")}
              options={channels.map((value) => ({ value, label: tch(value) }))}
              error={errors.channel}
              required
            />
          ) : null}
          <TextField
            label={t("occurredOn")}
            name="occurred_on"
            type="date"
            defaultValue={today}
            max={today}
            error={errors.occurred_on}
            required
          />
          {event === "promised_by" ? (
            <TextField
              label={t("promisedOn")}
              hint={t("promisedOnHint")}
              name="promised_on"
              type="date"
              min={today}
              error={errors.promised_on}
              required
            />
          ) : null}
        </div>
      )}
    </ActionDialog>
  );
}

/**
 * Follow-ups on fixes outside the school (FO-01; FR-UDI-021..FR-UDI-030). One line per student
 * and fix owner while the readiness check still finds the difference: the parent (Aadhaar
 * update) or the MEO / MIS coordinator (UDISE+). Overdue first. The office records each step;
 * nothing is ever corrected automatically, and the line goes away by itself once the new value
 * matches.
 */
export function FollowupsScreen({
  profileKey,
  sectionId,
}: {
  profileKey: string;
  sectionId: string | null;
}) {
  const t = useTranslations("udiseFollowup");
  const tr = useTranslations("readiness");
  const tf = useTranslations("findings");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const [filter, setFilter] = useState<Filter>("all");
  const [pending, setPending] = useState<FollowupItem | null>(null);
  const list = useQuery({
    queryKey: FOLLOWUP_KEYS.list(profileKey, sectionId),
    queryFn: () => fetchFollowups(api, profileKey, sectionId),
    retry: false,
  });
  const count = (value: number | undefined) => formatCount(value, locale) ?? "0";
  const manage = can(READINESS_MANAGE);
  const rows = (list.data?.items ?? []).filter((row) =>
    filter === "all" ? true : row.state === filter,
  );

  const columns: ColumnV2<FollowupItem>[] = [
    {
      key: "student",
      stack: "title",
      header: tf("colStudent"),
      cell: (row) => (
        <span className="block min-w-36">
          <span className="block font-semibold">
            <Value>{row.student.display_name}</Value>
          </span>
          {row.student.admission_no ? (
            <span className="block font-mono text-xs text-ink-muted">
              {tf("admissionNo", { number: row.student.admission_no })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "owner",
      header: t("colOwner"),
      cell: (row) => <OwnerBadge owner={row.fix_owner} />,
    },
    {
      key: "state",
      header: t("colState"),
      cell: (row) => <FollowupStatePill item={row} />,
    },
    {
      key: "last",
      header: t("colLast"),
      cell: (row) =>
        row.last_event ? (
          <span className="flex flex-col gap-0.5 text-sm">
            <span>{t(`event.${row.last_event}`)}</span>
            <span className="num text-ink-muted">{formatDate(row.last_on)}</span>
          </span>
        ) : (
          <span className="text-ink-muted">{t("nothingYet")}</span>
        ),
    },
    {
      key: "promised",
      header: t("colPromised"),
      cell: (row) =>
        row.promised_on ? (
          <span className="num text-sm whitespace-nowrap">{formatDate(row.promised_on)}</span>
        ) : (
          <span className="text-ink-muted">—</span>
        ),
    },
    {
      key: "actions",
      stack: "actions",
      header: tc("actions"),
      className: "print:hidden",
      cell: (row) => (
        <span className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {manage ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setPending(row)}
              aria-label={t("recordAria", { student: row.student.display_name ?? "" })}
            >
              {t("recordStep")}
            </Button>
          ) : null}
          <Link
            href={`/findings/readiness/${row.student.id}?profile=${profileKey}`}
            className="font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
          >
            {tr("openStudent")}
            <span className="sr-only">: {row.student.display_name ?? ""}</span>
          </Link>
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description", { days: list.data?.overdue_after_days ?? 14 })}
        breadcrumb={[
          { label: tf("crumbHome"), href: "/" },
          { label: tr("title"), href: `/findings/readiness?profile=${profileKey}` },
          { label: t("title") },
        ]}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        {FOLLOWUP_OWNERS.map((owner) => {
          const counts = ownerCounts(list.data, owner);
          return (
            <KpiCard
              key={owner}
              label={t(`waiting.${owner}`)}
              value={counts ? count(counts.open) : null}
              unavailableLabel={tc("notAvailable")}
              comparison={
                counts
                  ? t("waitingNote", {
                      overdue: count(counts.overdue),
                      notStarted: count(counts.not_started),
                    })
                  : undefined
              }
              aside={<OwnerBadge owner={owner} />}
            />
          );
        })}
      </div>
      <SegmentedControl
        legend={t("filter")}
        name="followup-filter"
        value={filter}
        onValueChange={(value) => setFilter(value as Filter)}
        options={[
          { value: "all", label: t("filterAll") },
          { value: "overdue", label: t("filterOverdue") },
          { value: "not_started", label: t("filterNotStarted") },
        ]}
      />
      {list.data && list.data.items.length === 0 ? (
        <Alert tone="success" title={t("emptyTitle")}>
          {t("emptyBody")}
        </Alert>
      ) : (
        <DataTableV2
          id="followups"
          stacked
          caption={t("title")}
          captionHidden
          columns={columns}
          state={toLoadable({ ...list, data: rows })}
          rowKey={(row) => `${row.student.id}:${row.fix_owner}`}
          emptyTitle={t("noneInFilter")}
          onRetry={() => void list.refetch()}
        />
      )}
      {manage ? (
        <RecordStepDialog
          key={pending ? `${pending.student.id}:${pending.fix_owner}` : "closed"}
          profileKey={profileKey}
          item={pending}
          onClose={() => setPending(null)}
        />
      ) : null}
    </div>
  );
}

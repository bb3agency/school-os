"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Timeline, type TimelineItem } from "@/components/ui/Timeline";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatDateTime } from "@/lib/format";
import {
  ACTION_KINDS,
  CLOSE_REASONS,
  ERASE_REASONS,
  KEYS,
  PERM,
  actionSchema,
  assignSchema,
  closeSchema,
  eraseSchema,
  ifMatch,
  todayIst,
  useFlag,
  useOwners,
  type FlagAction,
  type FlagDetail,
} from "./data";
import { FlagReason, FlagStatusPill, LoadGate, OverduePill, PurposeNote } from "./parts";

const NOTE_MAX = 1000;

/** The intervention log, oldest first: raised, what people did, closed (FR-EW-007). */
export function ActionLog({ actions, label }: { actions: readonly FlagAction[]; label: string }) {
  const t = useTranslations("insights.log");
  const items: TimelineItem[] = actions.map((action) => ({
    id: action.id,
    title: t(`kind.${action.kind}`),
    time: <time dateTime={action.acted_on}>{formatDate(action.acted_on)}</time>,
    body: (
      <>
        {action.note ? (
          <p className="break-words whitespace-pre-line text-ink">{action.note}</p>
        ) : null}
        <p className="text-xs text-ink-subtle">
          {t("by", {
            name: action.by?.display_name ?? t("someone"),
            at: formatDateTime(action.created_at) ?? action.created_at,
          })}
        </p>
      </>
    ),
    status: "done",
  }));
  return <Timeline items={items} label={label} />;
}

function FlagFacts({ flag }: { flag: FlagDetail }) {
  const t = useTranslations("insights");
  const rows: [string, ReactNode][] = [
    [t("detail.indicator"), t(`indicator.${flag.indicator}`)],
    [t("detail.why"), <FlagReason key="why" flag={flag} />],
    [t("detail.raisedOn"), formatDate(flag.raised_on)],
    [
      t("detail.dueOn"),
      <span key="due" className="inline-flex flex-wrap items-center gap-1">
        <span className="font-mono">{formatDate(flag.due_on)}</span>
        <OverduePill overdue={flag.overdue} />
      </span>,
    ],
    [t("detail.owner"), flag.owner?.display_name ?? t("unassigned")],
    [
      t("detail.raisedBy"),
      flag.raised_by ? (flag.raised_by.display_name ?? t("someone")) : t("detail.byRule"),
    ],
    ...(flag.close_reason
      ? ([[t("detail.closeReason"), t(`close.reason.${flag.close_reason}`)]] as [
          string,
          ReactNode,
        ][])
      : []),
  ];
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[max-content_1fr]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-sm font-medium text-ink-muted">{label}</dt>
          <dd className="text-sm break-words text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Log an action, close, reassign and erase (principal only for the last two; FR-EW-007, FR-EW-014). */
function FlagActions({ flag }: { flag: FlagDetail }) {
  const t = useTranslations("insights");
  const api = useBffClient("staff");
  const router = useRouter();
  const can = useStaffCan();
  const path = { flag_id: flag.id };
  const invalidate = [KEYS.all] as const;
  const open = flag.status !== "closed";
  const canAct = can(PERM.insightsAct);
  const canManage = can(PERM.insightsManage);
  const owners = useOwners(flag.id, open && canManage);

  return (
    <div className="flex flex-wrap gap-2" data-print="hide">
      {open && canAct ? (
        <>
          <ActionDialog
            triggerLabel={t("action.add")}
            triggerVariant="primary"
            title={t("action.title")}
            description={t("action.description")}
            confirmLabel={t("action.save")}
            schema={actionSchema}
            invalidate={invalidate}
            errorNamespace="insights"
            submit={(input, key) =>
              unwrap(
                api.POST("/api/v1/insights/flags/{flag_id}/actions", {
                  params: { path },
                  headers: { "Idempotency-Key": key },
                  body: {
                    kind: input.kind,
                    acted_on: input.acted_on,
                    ...(input.note ? { note: input.note } : {}),
                  },
                }),
              )
            }
          >
            {(errors) => (
              <>
                <SelectField
                  name="kind"
                  label={t("action.kind")}
                  placeholder={t("action.choose")}
                  error={errors.kind}
                  options={ACTION_KINDS.map((value) => ({ value, label: t(`log.kind.${value}`) }))}
                />
                <TextField
                  name="acted_on"
                  type="date"
                  label={t("action.actedOn")}
                  defaultValue={todayIst()}
                  min={flag.raised_on}
                  max={todayIst()}
                  error={errors.acted_on}
                />
                <TextAreaField
                  name="note"
                  label={t("action.note")}
                  hint={t("action.noteHint")}
                  error={errors.note}
                  maxLength={NOTE_MAX}
                  rows={3}
                />
              </>
            )}
          </ActionDialog>
          <ActionDialog
            triggerLabel={t("close.open")}
            title={t("close.title")}
            description={t("close.description")}
            confirmLabel={t("close.save")}
            schema={closeSchema}
            invalidate={invalidate}
            errorNamespace="insights"
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/insights/flags/{flag_id}/close", {
                  params: { path },
                  headers: { "If-Match": ifMatch(flag.version) },
                  body: { reason: input.reason, ...(input.note ? { note: input.note } : {}) },
                }),
              )
            }
          >
            {(errors) => (
              <>
                <SelectField
                  name="reason"
                  label={t("close.reasonLabel")}
                  placeholder={t("action.choose")}
                  error={errors.reason}
                  options={CLOSE_REASONS.map((value) => ({
                    value,
                    label: t(`close.reason.${value}`),
                  }))}
                />
                <TextAreaField
                  name="note"
                  label={t("action.note")}
                  hint={t("action.noteHint")}
                  error={errors.note}
                  maxLength={NOTE_MAX}
                  rows={3}
                />
              </>
            )}
          </ActionDialog>
        </>
      ) : null}
      {open && canManage && owners.status === "ready" ? (
        <ActionDialog
          triggerLabel={t("assign.open")}
          title={t("assign.title")}
          description={t("assign.description")}
          confirmLabel={t("assign.save")}
          stepUp
          schema={assignSchema}
          invalidate={invalidate}
          errorNamespace="insights"
          submit={(input) =>
            unwrap(
              api.POST("/api/v1/insights/flags/{flag_id}/assign", {
                params: { path },
                headers: { "If-Match": ifMatch(flag.version) },
                body: input,
              }),
            )
          }
        >
          {(errors) =>
            owners.data.length === 0 ? (
              <Alert tone="info">{t("assign.nobody")}</Alert>
            ) : (
              <SelectField
                name="owner_membership_id"
                label={t("assign.owner")}
                placeholder={t("action.choose")}
                error={errors.owner_membership_id}
                defaultValue={flag.owner?.membership_id ?? ""}
                options={owners.data.map((owner) => ({
                  value: owner.membership_id,
                  label: owner.display_name,
                }))}
              />
            )
          }
        </ActionDialog>
      ) : null}
      {canManage ? (
        <ActionDialog
          triggerLabel={t("erase.open")}
          triggerVariant="danger"
          title={t("erase.title")}
          description={t("erase.description")}
          confirmLabel={t("erase.save")}
          confirmVariant="danger"
          stepUp
          schema={eraseSchema}
          errorNamespace="insights"
          submit={(input) =>
            unwrap(
              api.POST("/api/v1/insights/flags/{flag_id}/erase", { params: { path }, body: input }),
            )
          }
          onSuccess={() => router.push("/flags")}
          invalidate={invalidate}
        >
          {(errors) => (
            <SelectField
              name="reason"
              label={t("erase.reasonLabel")}
              placeholder={t("action.choose")}
              error={errors.reason}
              options={ERASE_REASONS.map((value) => ({ value, label: t(`erase.reason.${value}`) }))}
            />
          )}
        </ActionDialog>
      ) : null}
    </div>
  );
}

/**
 * One flag (US-1706): why the rule raised it, who owns it, what has been done (the intervention
 * log) and the next step. Reading it is audited by the API (FR-EW-012).
 */
export function FlagDetailScreen({ flagId }: { flagId: string }) {
  const t = useTranslations("insights");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(PERM.insightsRead);
  const flag = useFlag(flagId, allowed);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("detail.title")}
        description={t("detail.description")}
        breadcrumb={[{ label: t("title"), href: "/flags" }, { label: t("detail.title") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <LoadGate data={meLoaded ? flag : { status: "loading" }}>
          {(value) => (
            <>
              <Card
                title={
                  <span className="inline-flex flex-wrap items-center gap-2">
                    <span className="break-words">{value.student.full_name ?? t("unnamed")}</span>
                    <FlagStatusPill status={value.status} />
                  </span>
                }
                description={[value.student.section_label, value.student.admission_no]
                  .filter(Boolean)
                  .join(" · ")}
                actions={
                  <Link
                    href={`/students/${value.student.id}`}
                    className="text-sm text-primary underline underline-offset-4"
                    data-print="hide"
                  >
                    {t("detail.profile")}
                  </Link>
                }
              >
                <div className="space-y-4">
                  <FlagFacts flag={value} />
                  <FlagActions flag={value} />
                </div>
              </Card>
              <Card title={t("detail.logTitle")} description={t("detail.logDescription")}>
                <ActionLog actions={value.actions} label={t("detail.logTitle")} />
              </Card>
              <PurposeNote />
            </>
          )}
        </LoadGate>
      )}
    </div>
  );
}

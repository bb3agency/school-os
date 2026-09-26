"use client";

import {
  TICKET_CATEGORIES,
  TICKET_PRIORITIES,
  TICKET_STATUSES,
  type SupportTicket,
} from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { TicketThread } from "@/features/support/TicketThread";
import { known, priorityTone, ticketTone } from "@/features/status";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";
import { useApiForm } from "@/lib/forms";
import type { Loadable } from "@/lib/loadable";
import { checkbox, text, uuid } from "@/lib/validation";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { PK, ifMatch, useCan, useOperatorMe, useSchoolDirectory } from "./data";

/** Always-visible warning on every support screen (docs/16 §15; CLAUDE.md §11). */
export function StudentDataWarning() {
  const t = useTranslations("support");
  return (
    <Alert tone="warning" title={t("piiWarningTitle")}>
      {t("piiWarningBody")}
    </Alert>
  );
}

function StatusBadge({ ticket }: { ticket: SupportTicket }) {
  const tstatus = useTranslations("status.ticket");
  return <Badge tone={ticketTone[ticket.status]}>{tstatus(ticket.status)}</Badge>;
}

function PriorityBadge({ priority }: { priority: string }) {
  const tprio = useTranslations("status.priority");
  const value = known(priorityTone, priority);
  return value ? (
    <Badge tone={priorityTone[value]}>{tprio(value)}</Badge>
  ) : (
    <Badge>{priority}</Badge>
  );
}

/** Operator ticket queue table. */
export function TicketTable({
  tickets,
  caption,
  schoolName,
}: {
  tickets: Loadable<readonly SupportTicket[]>;
  caption: string;
  schoolName?: (tenantId: string) => string;
}) {
  const t = useTranslations("platform.support");
  const columns: Column<SupportTicket>[] = [
    {
      key: "number",
      header: t("colNumber"),
      cell: (row) => (
        <Link href={`/platform/support/${row.id}`} className="font-semibold text-primary underline">
          {row.number}
        </Link>
      ),
    },
    ...(schoolName
      ? [
          {
            key: "school",
            header: t("colSchool"),
            cell: (row: SupportTicket) => schoolName(row.tenant_id),
          },
        ]
      : []),
    { key: "subject", header: t("colSubject"), cell: (row) => row.subject },
    { key: "status", header: t("colStatus"), cell: (row) => <StatusBadge ticket={row} /> },
    {
      key: "priority",
      header: t("colPriority"),
      cell: (row) => <PriorityBadge priority={row.priority} />,
    },
    {
      key: "sla",
      header: t("colSla"),
      cell: (row) => (
        <Value>
          {formatDateTime(
            row.first_responded_at ? row.resolution_due_at : row.first_response_due_at,
          )}
        </Value>
      ),
    },
    {
      key: "updated",
      header: t("colUpdated"),
      cell: (row) => <Value>{formatDateTime(row.updated_at)}</Value>,
    },
  ];
  return (
    <DataTable
      caption={caption}
      captionHidden
      columns={columns}
      state={tickets}
      rowKey={(row) => row.id}
      emptyTitle={t("emptyTitle")}
      emptyBody={t("emptyBody")}
    />
  );
}

export interface TicketFilters {
  status?: string;
  priority?: string;
  mine?: boolean;
}

const newTicketSchema = z.object({
  tenant_id: uuid,
  channel: z.enum(["email", "phone", "whatsapp"], { error: "chooseOption" }),
  category: z.enum(TICKET_CATEGORIES, { error: "chooseOption" }),
  priority: z.enum(TICKET_PRIORITIES, { error: "chooseOption" }),
  subject: text(200),
  body: text(4000),
});

/** FR-PLT-027 (docs/16 §5.14): the operator queue with filters. */
export function PlatformSupportScreen({ filters = {} }: { filters?: TicketFilters }) {
  const t = useTranslations("platform.support");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.ticket");
  const tprio = useTranslations("status.priority");
  const tcat = useTranslations("support.categories");
  const api = useBffClient("operator");
  const me = useOperatorMe();
  const can = useCan();
  const { nameOf, schools } = useSchoolDirectory();
  const query = {
    limit: 200,
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.priority ? { priority: filters.priority } : {}),
    ...(filters.mine && me ? { assigned_to: me.operator_id } : {}),
  };
  const tickets = useApiQuery(
    [...PK.tickets, "list", query],
    async () =>
      (await unwrap(api.GET("/api/v1/platform/support/tickets", { params: { query } }))).data,
    { enabled: !filters.mine || me !== undefined },
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          can("platform.support.manage") ? (
            <ActionDialog
              triggerLabel={t("newTicket")}
              triggerVariant="primary"
              title={t("newTicketTitle")}
              description={t("newTicketBody")}
              confirmLabel={t("newTicket")}
              schema={newTicketSchema}
              invalidate={[PK.tickets, PK.dashboard]}
              submit={(data, key) =>
                unwrap(
                  api.POST("/api/v1/platform/support/tickets", {
                    params: { header: { "Idempotency-Key": key } },
                    body: data,
                  }),
                )
              }
            >
              {(errors) => (
                <>
                  <StudentDataWarning />
                  <SelectField
                    name="tenant_id"
                    label={t("colSchool")}
                    placeholder={tc("chooseOne")}
                    error={errors.tenant_id}
                    defaultValue=""
                    options={schools.map((school) => ({
                      value: school.tenant_id,
                      label: `${school.school_name} (${school.code})`,
                    }))}
                  />
                  <div className="grid gap-4 md:grid-cols-3">
                    <SelectField
                      name="channel"
                      label={t("channel")}
                      error={errors.channel}
                      defaultValue="email"
                      options={(["email", "phone", "whatsapp"] as const).map((value) => ({
                        value,
                        label: t(`channels.${value}`),
                      }))}
                    />
                    <SelectField
                      name="category"
                      label={t("category")}
                      error={errors.category}
                      defaultValue="other"
                      options={TICKET_CATEGORIES.map((value) => ({ value, label: tcat(value) }))}
                    />
                    <SelectField
                      name="priority"
                      label={t("colPriority")}
                      error={errors.priority}
                      defaultValue="p3"
                      options={TICKET_PRIORITIES.map((value) => ({ value, label: tprio(value) }))}
                    />
                  </div>
                  <TextField
                    name="subject"
                    label={t("colSubject")}
                    error={errors.subject}
                    maxLength={200}
                  />
                  <TextAreaField
                    name="body"
                    label={t("message")}
                    error={errors.body}
                    maxLength={4000}
                    rows={5}
                  />
                </>
              )}
            </ActionDialog>
          ) : undefined
        }
      />
      <StudentDataWarning />
      <form method="get" className="flex flex-wrap items-end gap-3">
        <SelectField
          name="status"
          label={t("colStatus")}
          placeholder={tc("all")}
          defaultValue={filters.status ?? ""}
          options={TICKET_STATUSES.map((value) => ({ value, label: tstatus(value) }))}
          className="w-52"
        />
        <SelectField
          name="priority"
          label={t("colPriority")}
          placeholder={tc("all")}
          defaultValue={filters.priority ?? ""}
          options={TICKET_PRIORITIES.map((value) => ({ value, label: tprio(value) }))}
          className="w-44"
        />
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            name="mine"
            value="true"
            defaultChecked={filters.mine ?? false}
            className="size-4 accent-primary"
          />
          {t("assignedToMe")}
        </label>
        <Button type="submit" variant="secondary">
          {tc("applyFilters")}
        </Button>
      </form>
      <TicketTable tickets={tickets} caption={t("title")} schoolName={nameOf} />
    </div>
  );
}

const replySchema = z.object({ body: text(4000), internal_note: checkbox });

/** One ticket: thread, reply or internal note, status, priority, assignee, PII flag. */
export function PlatformTicketScreen({ ticketId }: { ticketId: string }) {
  const t = useTranslations("platform.support");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.ticket");
  const tprio = useTranslations("status.priority");
  const te = useTranslations("errors");
  const api = useBffClient("operator");
  const me = useOperatorMe();
  const can = useCan();
  const { nameOf } = useSchoolDirectory();
  const key = [...PK.tickets, ticketId];
  const ticket = useApiQuery(key, () =>
    unwrap(
      api.GET("/api/v1/platform/support/tickets/{ticket_id}", {
        params: { path: { ticket_id: ticketId } },
      }),
    ),
  );
  const manage = can("platform.support.manage");

  const reply = useApiForm({
    schema: replySchema,
    invalidate: [PK.tickets],
    submit: (data) =>
      unwrap(
        api.POST("/api/v1/platform/support/tickets/{ticket_id}/messages", {
          params: { path: { ticket_id: ticketId } },
          body: data,
        }),
      ),
    onSuccess: (_result, form) => form.reset(),
  });

  const patchSchema = z.object({
    status: z.enum(TICKET_STATUSES, { error: "chooseOption" }),
    priority: z.enum(TICKET_PRIORITIES, { error: "chooseOption" }),
    assigned: z.enum(["keep", "me", "none"]),
    personal_data_flagged: checkbox,
  });
  const current = ticket.status === "ready" ? ticket.data : null;
  const update = useApiForm({
    schema: patchSchema,
    invalidate: [PK.tickets, PK.dashboard],
    submit: (data) => {
      if (!current) return Promise.reject(new Error("not loaded"));
      const assigned_to =
        data.assigned === "me"
          ? (me?.operator_id ?? null)
          : data.assigned === "none"
            ? null
            : current.assigned_to;
      return unwrap(
        api.PATCH("/api/v1/platform/support/tickets/{ticket_id}", {
          params: {
            path: { ticket_id: ticketId },
            header: { "If-Match": ifMatch(current.version) },
          },
          body: {
            status: data.status,
            priority: data.priority,
            assigned_to,
            personal_data_flagged: data.personal_data_flagged,
          },
        }),
      );
    },
  });

  if (ticket.status === "loading") return <LoadingState label={tc("loading")} />;
  if (ticket.status !== "ready") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {ticket.status === "error" && ticket.reason
          ? te(`load.${ticket.reason}`)
          : tc("loadErrorBody")}
      </Alert>
    );
  }
  const data = ticket.data;
  const assignee =
    data.assigned_to === null
      ? t("unassigned")
      : data.assigned_to === me?.operator_id
        ? t("assignedMe")
        : data.assigned_to.slice(0, 8);

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${data.number} · ${data.subject}`}
        description={nameOf(data.tenant_id)}
        badge={<StatusBadge ticket={data} />}
      />
      <StudentDataWarning />
      {data.personal_data_flagged ? (
        <Alert tone="danger" title={t("flaggedTitle")}>
          {t("flaggedBody")}
        </Alert>
      ) : null}
      <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
        <Card title={t("thread")}>
          <TicketThread ticket={data} />
          {manage && data.status !== "closed" ? (
            <form
              noValidate
              onSubmit={reply.onSubmit}
              className="mt-6 space-y-3 border-t border-border pt-4"
            >
              <TextAreaField
                name="body"
                label={t("reply")}
                hint={t("replyHint")}
                error={reply.errors.body}
                maxLength={4000}
                rows={4}
              />
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" name="internal_note" className="size-4 accent-primary" />
                {t("internalNote")}
              </label>
              <ApiErrorAlert error={reply.error} />
              <div className="flex justify-end">
                <Button type="submit" disabled={reply.pending}>
                  {reply.pending ? tc("working") : t("send")}
                </Button>
              </div>
            </form>
          ) : null}
        </Card>
        <Card title={t("details")}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-ink-muted">{t("colPriority")}</dt>
            <dd>
              <PriorityBadge priority={data.priority} />
            </dd>
            <dt className="text-ink-muted">{t("category")}</dt>
            <dd>{data.category}</dd>
            <dt className="text-ink-muted">{t("channel")}</dt>
            <dd>{data.channel}</dd>
            <dt className="text-ink-muted">{t("assignee")}</dt>
            <dd>{assignee}</dd>
            <dt className="text-ink-muted">{t("firstResponseDue")}</dt>
            <dd>
              <Value>{formatDateTime(data.first_response_due_at)}</Value>
            </dd>
            <dt className="text-ink-muted">{t("resolutionDue")}</dt>
            <dd>
              <Value>{formatDateTime(data.resolution_due_at)}</Value>
            </dd>
          </dl>
          {manage ? (
            <form
              noValidate
              onSubmit={update.onSubmit}
              className="mt-6 space-y-3 border-t border-border pt-4"
            >
              <SelectField
                name="status"
                label={t("colStatus")}
                defaultValue={data.status}
                error={update.errors.status}
                options={TICKET_STATUSES.map((value) => ({ value, label: tstatus(value) }))}
              />
              <SelectField
                name="priority"
                label={t("colPriority")}
                defaultValue={known(priorityTone, data.priority) ?? "p3"}
                error={update.errors.priority}
                options={TICKET_PRIORITIES.map((value) => ({ value, label: tprio(value) }))}
              />
              <SelectField
                name="assigned"
                label={t("assignee")}
                defaultValue="keep"
                options={[
                  { value: "keep", label: t("assignKeep", { name: assignee }) },
                  { value: "me", label: t("assignMe") },
                  { value: "none", label: t("unassigned") },
                ]}
              />
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  name="personal_data_flagged"
                  defaultChecked={data.personal_data_flagged}
                  className="mt-1 size-4 accent-primary"
                />
                {t("flagPersonalData")}
              </label>
              <ApiErrorAlert error={update.error} />
              <div className="flex justify-end">
                <Button type="submit" variant="secondary" disabled={update.pending}>
                  {update.pending ? tc("working") : tc("save")}
                </Button>
              </div>
            </form>
          ) : null}
        </Card>
      </div>
    </div>
  );
}

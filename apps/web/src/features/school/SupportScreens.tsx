"use client";

import { TICKET_CATEGORIES, TICKET_PRIORITIES, type SupportTicket } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useState } from "react";
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
import { known, priorityTone, ticketTone } from "@/features/status";
import { TicketThread } from "@/features/support/TicketThread";
import { Link } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDateTime } from "@/lib/format";
import { useApiForm } from "@/lib/forms";
import { text } from "@/lib/validation";

const KEY = ["staff", "support-tickets"] as const;

function StudentDataWarning() {
  const t = useTranslations("support");
  return (
    <Alert tone="warning" title={t("piiWarningTitle")}>
      {t("piiWarningBody")}
    </Alert>
  );
}

const ticketSchema = z.object({
  subject: text(200),
  category: z.enum(TICKET_CATEGORIES, { error: "chooseOption" }),
  priority: z.enum(TICKET_PRIORITIES, { error: "chooseOption" }),
  body: text(4000),
});

/** FR-PLT-027 (docs/16 §15): the school's own tickets and "open a ticket". */
export function SupportScreen() {
  const t = useTranslations("school.support");
  const ts = useTranslations("support");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.ticket");
  const tprio = useTranslations("status.priority");
  const tcat = useTranslations("support.categories");
  const api = useBffClient("staff");
  const [opened, setOpened] = useState<SupportTicket | null>(null);
  const tickets = useApiQuery(
    [...KEY, "list"],
    async () =>
      (await unwrap(api.GET("/api/v1/support/tickets", { params: { query: { limit: 100 } } })))
        .data,
  );
  const form = useApiForm({
    schema: ticketSchema,
    invalidate: [KEY],
    submit: (data, key) =>
      unwrap(
        api.POST("/api/v1/support/tickets", {
          // Idempotency-Key: a retry after a timeout never opens a second ticket.
          headers: { "Idempotency-Key": key },
          body: data,
        }),
      ),
    onSuccess: (ticket, element) => {
      element.reset();
      setOpened(ticket);
    },
  });

  const columns: Column<SupportTicket>[] = [
    {
      key: "number",
      header: t("colNumber"),
      cell: (row) => (
        <Link href={`/support/${row.id}`} className="font-semibold text-primary underline">
          {row.number}
        </Link>
      ),
    },
    { key: "subject", header: t("colSubject"), cell: (row) => row.subject },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={ticketTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
    {
      key: "updated",
      header: t("colUpdated"),
      cell: (row) => <Value>{formatDateTime(row.updated_at ?? row.created_at)}</Value>,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <StudentDataWarning />
      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <Card title={t("listTitle")}>
          <DataTable
            caption={t("listTitle")}
            captionHidden
            columns={columns}
            state={tickets}
            rowKey={(row) => row.id}
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
          />
        </Card>
        <Card title={t("newTitle")} description={t("newBody")}>
          <form noValidate onSubmit={form.onSubmit} className="space-y-4">
            <TextField
              name="subject"
              label={t("subject")}
              error={form.errors.subject}
              maxLength={200}
              autoComplete="off"
            />
            <div className="grid gap-4 md:grid-cols-2">
              <SelectField
                name="category"
                label={t("category")}
                error={form.errors.category}
                defaultValue="other"
                options={TICKET_CATEGORIES.map((value) => ({ value, label: tcat(value) }))}
              />
              <SelectField
                name="priority"
                label={t("priority")}
                hint={t("priorityHint")}
                error={form.errors.priority}
                defaultValue="p3"
                options={TICKET_PRIORITIES.map((value) => ({ value, label: tprio(value) }))}
              />
            </div>
            <TextAreaField
              name="body"
              label={t("message")}
              hint={ts("piiShortHint")}
              error={form.errors.body}
              maxLength={4000}
              rows={6}
            />
            <ApiErrorAlert error={form.error} />
            {opened ? (
              <Alert tone="success" live title={t("openedTitle", { number: opened.number })}>
                {t("openedBody")}
              </Alert>
            ) : null}
            <div className="flex justify-end">
              <Button type="submit" disabled={form.pending}>
                {form.pending ? tc("working") : t("send")}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}

const replySchema = z.object({ body: text(4000) });

/** One of the school's tickets: thread (no internal notes) and reply. */
export function SupportTicketScreen({ ticketId }: { ticketId: string }) {
  const t = useTranslations("school.support");
  const ts = useTranslations("support");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tstatus = useTranslations("status.ticket");
  const tprio = useTranslations("status.priority");
  const api = useBffClient("staff");
  const ticket = useApiQuery([...KEY, ticketId], () =>
    unwrap(
      api.GET("/api/v1/support/tickets/{ticket_id}", {
        params: { path: { ticket_id: ticketId } },
      }),
    ),
  );
  const reply = useApiForm({
    schema: replySchema,
    invalidate: [KEY],
    submit: (data) =>
      unwrap(
        api.POST("/api/v1/support/tickets/{ticket_id}/messages", {
          params: { path: { ticket_id: ticketId } },
          body: data,
        }),
      ),
    onSuccess: (_result, element) => element.reset(),
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
  const priority = known(priorityTone, data.priority);
  return (
    <div className="space-y-6">
      <PageHeader
        title={`${data.number} · ${data.subject}`}
        badge={<Badge tone={ticketTone[data.status]}>{tstatus(data.status)}</Badge>}
        description={priority ? t("priorityLine", { priority: tprio(priority) }) : undefined}
      />
      <StudentDataWarning />
      <Card title={t("thread")}>
        <TicketThread ticket={data} />
        {data.status !== "closed" ? (
          <form
            noValidate
            onSubmit={reply.onSubmit}
            className="mt-6 space-y-3 border-t border-border pt-4"
          >
            <TextAreaField
              name="body"
              label={t("reply")}
              hint={ts("piiShortHint")}
              error={reply.errors.body}
              maxLength={4000}
              rows={4}
            />
            <ApiErrorAlert error={reply.error} />
            <div className="flex justify-end">
              <Button type="submit" disabled={reply.pending}>
                {reply.pending ? tc("working") : t("send")}
              </Button>
            </div>
          </form>
        ) : (
          <p className="mt-4 text-sm text-ink-muted">{t("closedNote")}</p>
        )}
      </Card>
      <p>
        <Link href="/support" className="text-primary underline">
          {t("backToList")}
        </Link>
      </p>
    </div>
  );
}

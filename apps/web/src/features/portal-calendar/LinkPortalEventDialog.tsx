"use client";

import { useTranslations } from "next-intl";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { SelectField } from "@/components/ui/Select";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import { CALENDAR_KEYS, useCalendar } from "./data";

const linkSchema = z.object({
  event_key: z.string().trim().min(1, { error: "required" }),
});

/**
 * "Link to a portal window" on a task confirmed from a circular (US-3104, FR-RDY-010; POST
 * /tasks/{task_id}/portal-event, readiness.calendar.manage and task.manage): the task's due date
 * becomes the window's confirmed closing date, from that circular. A person's action.
 */
export function LinkPortalEventDialog({
  task,
}: {
  task: { id: string; title: string; due_on: string };
}) {
  const t = useTranslations("portalCalendar.link");
  const api = useBffClient("staff");
  const calendar = useCalendar(null);
  const events = (calendar.data?.events ?? []).filter((event) => event.state !== "closed");
  return (
    <ActionDialog
      triggerLabel={t("open")}
      triggerSize="sm"
      triggerVariant="ghost"
      triggerDescription={task.title}
      title={t("title")}
      description={t("description", { date: formatDate(task.due_on) ?? task.due_on })}
      confirmLabel={t("save")}
      consequence={t("consequence")}
      schema={linkSchema}
      errorNamespace="portalCalendar"
      invalidate={[CALENDAR_KEYS.all, ["staff", "dq", "readiness"]]}
      submit={(data, key) =>
        unwrap(
          api.POST("/api/v1/tasks/{task_id}/portal-event", {
            params: { path: { task_id: task.id } },
            headers: { "Idempotency-Key": key },
            body: {
              event_key: data.event_key,
              academic_year_id: calendar.data?.academic_year_id ?? null,
            },
          }),
        )
      }
    >
      {(errors) => (
        <SelectField
          name="event_key"
          label={t("event")}
          error={errors.event_key}
          defaultValue=""
          options={[
            { value: "", label: t("choose") },
            ...events.map((event) => ({ value: event.event_key, label: event.label_en })),
          ]}
        />
      )}
    </ActionDialog>
  );
}

"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Icon, type IconName } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { useCirculars } from "@/features/circulars/data";
import { ReadyBar } from "@/features/readiness/parts";
import type { Locale } from "@/i18n/routing";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import { formatCount, formatDate } from "@/lib/format";
import type { FieldErrors } from "@/lib/forms";
import * as z from "@/lib/zod";
import {
  CALENDAR_KEYS,
  SOURCE_KINDS,
  expectedRange,
  ifMatch,
  useCalendar,
  useCalendarAssignees,
  type CalendarEvent,
  type CalendarState,
} from "./data";

const MANAGE = "readiness.calendar.manage";

const stateTone: Record<CalendarState, BadgeTone> = {
  expected: "neutral",
  confirmed: "info",
  open: "warning",
  closed: "neutral",
};
const stateIcon: Record<CalendarState, IconName> = {
  expected: "clock",
  confirmed: "calendar",
  open: "alert",
  closed: "check",
};

/** The state of a window in words with an icon (colour is never the only signal). */
export function CalendarStateBadge({ state }: { state: CalendarState }) {
  const t = useTranslations("portalCalendar.state");
  return (
    <Badge tone={stateTone[state]}>
      <Icon name={stateIcon[state]} className="size-3.5" />
      {t(state)}
    </Badge>
  );
}

/** "Expected around Feb 2027" (muted, the file's guess) or "Confirmed: 03/02/2027 – 05/02/2027". */
export function WindowText({
  event,
}: {
  event: Pick<CalendarEvent, "confirmed" | "opens_on" | "closes_on">;
}) {
  const t = useTranslations("portalCalendar");
  const locale = useLocale();
  if (!event.confirmed) {
    return (
      <span className="text-ink-muted">
        {t("expectedAround", { range: expectedRange(event.opens_on, event.closes_on, locale) })}
      </span>
    );
  }
  return (
    <span className="num">
      {t("confirmedRange", {
        from: formatDate(event.opens_on) ?? event.opens_on,
        to: formatDate(event.closes_on) ?? event.closes_on,
      })}
    </span>
  );
}

const dateField = z
  .string()
  .trim()
  .refine((value) => typedDateToIso(value) !== null, { error: "invalidDate" })
  .transform((value) => typedDateToIso(value) ?? "");

const windowShape = {
  version: z.string().trim(),
  opens_on: dateField,
  closes_on: dateField,
  source_kind: z.enum(SOURCE_KINDS),
  document_id: z.string().trim(),
  owner_membership_id: z.string().trim(),
};

/** The window closes on or after the day it opens (the API checks it too). */
function closesAfterOpens(data: { opens_on: string; closes_on: string }): boolean {
  return data.closes_on >= data.opens_on;
}

export const confirmSchema = z
  .object(windowShape)
  .refine(closesAfterOpens, { path: ["closes_on"], error: "beforeOpening" });

export const eventSchema = z
  .object({
    ...windowShape,
    label: z.string().trim().min(3, { error: "tooShort" }).max(120, { error: "tooLong" }),
  })
  .refine(closesAfterOpens, { path: ["closes_on"], error: "beforeOpening" });

/** Optional ids are sent as null when the person chose "Nobody yet" / "No document". */
function optionalId(value: string): string | null {
  return value === "" ? null : value;
}

/** Shared fields of the confirm and add dialogs: dates, where they came from, document, owner. */
function WindowFields({
  errors,
  opensOn,
  closesOn,
  sourceKind,
  documentId,
  ownerId,
  ownerName,
}: {
  errors: FieldErrors;
  opensOn: string;
  closesOn: string;
  sourceKind: string;
  documentId: string | null;
  ownerId: string | null;
  ownerName: string | null;
}) {
  const t = useTranslations("portalCalendar.dialog");
  const dates = useDateInput();
  const can = useStaffCan();
  const assignees = useCalendarAssignees(true);
  const circulars = useCirculars(undefined, can("document.read"));
  const hint = dates.hint(closesOn);
  const owners = [
    { value: "", label: t("nobody") },
    ...(assignees.data ?? []).map((a) => ({ value: a.membership_id, label: a.display_name })),
  ];
  if (ownerId && !owners.some((o) => o.value === ownerId)) {
    owners.push({ value: ownerId, label: ownerName ?? t("formerStaff") });
  }
  const documents = [
    { value: "", label: t("noDocument") },
    ...(circulars.status === "ready" ? circulars.data.data : []).map((c) => ({
      value: c.document_id,
      label: c.title,
    })),
  ];
  if (documentId && !documents.some((d) => d.value === documentId)) {
    documents.push({ value: documentId, label: t("keptDocument") });
  }
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          name="opens_on"
          label={t("opensOn")}
          hint={t("dateHint", hint)}
          inputMode="numeric"
          autoComplete="off"
          placeholder={dates.placeholder}
          defaultValue={dates.fromIso(opensOn)}
          error={errors.opens_on ? t("dateInvalid", hint) : undefined}
        />
        <TextField
          name="closes_on"
          label={t("closesOn")}
          hint={t("dateHint", hint)}
          inputMode="numeric"
          autoComplete="off"
          placeholder={dates.placeholder}
          defaultValue={dates.fromIso(closesOn)}
          error={errors.closes_on ? t("closesInvalid", hint) : undefined}
        />
      </div>
      <SelectField
        name="source_kind"
        label={t("source")}
        hint={t("sourceHint")}
        defaultValue={sourceKind}
        error={errors.source_kind}
        options={SOURCE_KINDS.map((kind) => ({ value: kind, label: t(`sourceKind.${kind}`) }))}
      />
      {can("document.read") ? (
        <SelectField
          name="document_id"
          label={t("document")}
          hint={t("documentHint")}
          defaultValue={documentId ?? ""}
          error={errors.document_id}
          options={documents}
        />
      ) : (
        <input type="hidden" name="document_id" value={documentId ?? ""} readOnly />
      )}
      <SelectField
        name="owner_membership_id"
        label={t("owner")}
        hint={t("ownerHint")}
        defaultValue={ownerId ?? ""}
        error={errors.owner_membership_id}
        options={owners}
      />
    </>
  );
}

/** "Confirm dates" for one event (US-3102, PUT /dq/calendar/{event_key}, If-Match). */
function ConfirmDatesDialog({ event }: { event: CalendarEvent }) {
  const t = useTranslations("portalCalendar.confirm");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [opened] = useState(event);
  return (
    <ActionDialog
      triggerLabel={opened.confirmed ? t("change") : t("open")}
      triggerSize="sm"
      triggerVariant="secondary"
      triggerDescription={opened.label_en}
      title={t("title")}
      description={t("description", { event: opened.label_en })}
      confirmLabel={t("save")}
      consequence={t("consequence")}
      schema={confirmSchema}
      errorNamespace="portalCalendar"
      invalidate={[CALENDAR_KEYS.all, ["staff", "dq", "readiness"]]}
      submit={async (data) => {
        try {
          return await unwrap(
            api.PUT("/api/v1/dq/calendar/{event_key}", {
              params: { path: { event_key: opened.event_key } },
              headers: data.version ? { "If-Match": ifMatch(Number(data.version)) } : {},
              body: {
                academic_year_id: opened.academic_year_id,
                opens_on: data.opens_on,
                closes_on: data.closes_on,
                source_kind: data.source_kind,
                document_id: optionalId(data.document_id),
                owner_membership_id: optionalId(data.owner_membership_id),
              },
            }),
          );
        } catch (failure) {
          if (failure instanceof ApiError && failure.status === 412) {
            void queryClient.invalidateQueries({ queryKey: CALENDAR_KEYS.all });
          }
          throw failure;
        }
      }}
    >
      {(errors) => (
        <>
          <input type="hidden" name="version" value={opened.version ?? ""} readOnly />
          <WindowFields
            errors={errors}
            opensOn={opened.opens_on}
            closesOn={opened.closes_on}
            sourceKind={opened.source_kind ?? "circular"}
            documentId={opened.document_id}
            ownerId={opened.owner?.membership_id ?? null}
            ownerName={opened.owner?.display_name ?? null}
          />
        </>
      )}
    </ActionDialog>
  );
}

/** "Add our own event" (US-3102, FR-RDY-005, POST /dq/calendar). */
function AddEventDialog({ yearId }: { yearId: string }) {
  const t = useTranslations("portalCalendar.add");
  const td = useTranslations("portalCalendar.dialog");
  const api = useBffClient("staff");
  const today = new Date().toISOString().slice(0, 10);
  return (
    <ActionDialog
      triggerLabel={t("open")}
      triggerVariant="primary"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("save")}
      schema={eventSchema}
      errorNamespace="portalCalendar"
      invalidate={[CALENDAR_KEYS.all]}
      submit={(data, key) =>
        unwrap(
          api.POST("/api/v1/dq/calendar", {
            headers: { "Idempotency-Key": key },
            body: {
              academic_year_id: yearId,
              label: data.label,
              opens_on: data.opens_on,
              closes_on: data.closes_on,
              source_kind: data.source_kind,
              document_id: optionalId(data.document_id),
              owner_membership_id: optionalId(data.owner_membership_id),
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <input type="hidden" name="version" value="" readOnly />
          <TextField
            name="label"
            label={td("label")}
            hint={td("labelHint")}
            maxLength={120}
            autoComplete="off"
            error={errors.label}
          />
          <WindowFields
            errors={errors}
            opensOn={today}
            closesOn={today}
            sourceKind="circular"
            documentId={null}
            ownerId={null}
            ownerName={null}
          />
        </>
      )}
    </ActionDialog>
  );
}

/** Remove an own event, or undo a confirmation (DELETE /dq/calendar/{event_key}, If-Match). */
function RemoveDialog({ event }: { event: CalendarEvent }) {
  const t = useTranslations("portalCalendar.remove");
  const api = useBffClient("staff");
  const version = event.version;
  if (version === null) return null;
  return (
    <ActionDialog
      triggerLabel={event.own ? t("openOwn") : t("openConfirmed")}
      triggerSize="sm"
      triggerVariant="ghost"
      triggerDescription={event.label_en}
      title={event.own ? t("titleOwn") : t("titleConfirmed")}
      confirmLabel={event.own ? t("confirmOwn") : t("confirmConfirmed")}
      confirmVariant="danger"
      consequence={event.own ? t("consequenceOwn") : t("consequenceConfirmed")}
      schema={z.object({})}
      errorNamespace="portalCalendar"
      invalidate={[CALENDAR_KEYS.all, ["staff", "dq", "readiness"]]}
      submit={() =>
        unwrap(
          api.DELETE("/api/v1/dq/calendar/{event_key}", {
            params: {
              path: { event_key: event.event_key },
              query: { academic_year_id: event.academic_year_id },
            },
            headers: { "If-Match": ifMatch(version) },
          }),
        )
      }
    />
  );
}

/**
 * Portal calendar (US-3101, US-3102; docs/17 §5.10): the year's UDISE+, board registration and
 * nominal-roll windows and APAAR drives that concern this school, soonest closing first, with
 * the dates in force, the owner of the work, the state and the readiness of the linked checks.
 * Holders of readiness.calendar.manage confirm dates and add the school's own events.
 */
export function CalendarScreen() {
  const t = useTranslations("portalCalendar");
  const tf = useTranslations("findings");
  const tr = useTranslations("readiness");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const manage = can(MANAGE);
  const calendar = useCalendar(null);
  const count = (value: number) => formatCount(value, locale) ?? "0";
  const data = calendar.data;

  const columns: ColumnV2<CalendarEvent>[] = [
    {
      key: "event",
      stack: "title",
      hideable: false,
      header: t("colEvent"),
      className: "min-w-56",
      cell: (row) => (
        <span className="block">
          <span className="block font-semibold">{row.label_en}</span>
          <span className="block text-xs text-ink-muted">
            {row.own ? t("ownEvent") : t(`portal.${row.portal}`)}
          </span>
        </span>
      ),
    },
    {
      key: "window",
      header: t("colWindow"),
      cell: (row) => <WindowText event={row} />,
    },
    {
      key: "owner",
      header: t("colOwner"),
      cell: (row) =>
        row.owner ? (
          <span>{row.owner.display_name ?? t("formerStaff")}</span>
        ) : (
          <span className="text-ink-muted">{t("noOwner")}</span>
        ),
    },
    {
      key: "state",
      header: t("colState"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <CalendarStateBadge state={row.state} />
          {row.state !== "closed" ? (
            <span className="num text-xs text-ink-muted">
              {t("closesIn", { days: row.days_left })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "readiness",
      header: t("colReadiness"),
      className: "min-w-44",
      cell: (row) =>
        row.readiness.length === 0 ? (
          <span className="text-sm text-ink-muted">{t("noReadiness")}</span>
        ) : (
          <span className="flex flex-col gap-2">
            {row.readiness.map((r) => (
              <ReadyBar
                key={r.profile_key}
                ready={r.ready}
                students={r.students}
                label={tr("readyOf", { ready: count(r.ready), students: count(r.students) })}
              />
            ))}
          </span>
        ),
    },
    ...(manage
      ? [
          {
            key: "actions",
            stack: "actions",
            header: tc("actions"),
            className: "print:hidden",
            cell: (row: CalendarEvent) => (
              <span className="flex flex-wrap gap-2">
                <ConfirmDatesDialog event={row} />
                <RemoveDialog event={row} />
              </span>
            ),
          } satisfies ColumnV2<CalendarEvent>,
        ]
      : []),
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={
          data ? t("descriptionYear", { year: data.academic_year_label }) : t("description")
        }
        breadcrumb={[
          { label: tf("crumbHome"), href: "/" },
          { label: tf("title"), href: "/findings" },
          { label: t("title") },
        ]}
        actions={manage && data ? <AddEventDialog yearId={data.academic_year_id} /> : null}
      />
      <Alert tone="info" title={t("unverifiedTitle")}>
        <p>{t("unverifiedBody")}</p>
      </Alert>
      {calendar.isError ? (
        <ApiErrorAlert error={calendar.error} namespace="portalCalendar" />
      ) : (
        <DataTableV2
          id="portal-calendar"
          stacked
          caption={t("tableCaption")}
          captionHidden
          columns={columns}
          state={
            calendar.isPending
              ? { status: "loading" }
              : { status: "ready", data: data?.events ?? [] }
          }
          rowKey={(row) => `${row.academic_year_id}:${row.event_key}`}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
          onRetry={() => void calendar.refetch()}
        />
      )}
    </div>
  );
}

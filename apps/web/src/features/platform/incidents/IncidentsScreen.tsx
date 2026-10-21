"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Link, useRouter } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDateTime } from "@/lib/format";
import { istLocalInput } from "@/features/breaches/schema";
import { LIST_PAGE_SIZE, useCan, usePagedList } from "../data";
import { ShowMore } from "../ShowMore";
import {
  INCIDENTS_MANAGE,
  INCIDENTS_READ,
  INCIDENT_KEYS,
  SEVERITIES,
  clockFor,
  newIncidentSchema,
  type Clock,
  type Incident,
  type IncidentStatus,
} from "./data";

const STATUS: Record<IncidentStatus, PillVariant> = {
  open: "review",
  contained: "progress",
  resolved: "done",
  closed: "tag",
};

export function IncidentStatusPill({ status }: { status: IncidentStatus }) {
  const t = useTranslations("platform.incidents.status");
  return (
    <Pill variant={STATUS[status]} {...(status === "closed" ? { icon: null } : {})}>
      {t(status)}
    </Pill>
  );
}

/** "Due in 4 h" / "Overdue by 2 h" / "Due within the hour" for a clock. */
export function useClockText(): (clock: Clock) => string {
  const t = useTranslations("platform.incidents.clock");
  return (clock) =>
    clock.state === "overdue"
      ? t("overdueBy", { hours: clock.hours })
      : clock.state === "dueSoon"
        ? t("dueSoon")
        : t("dueIn", { hours: clock.hours });
}

/** The CERT-In clock (T0 + 6 h): reported, not required, or the time left. */
export function CertInClock({
  incident,
}: {
  incident: Pick<Incident, "cert_in_required" | "cert_in_reported_at" | "cert_in_due_at">;
}) {
  const t = useTranslations("platform.incidents");
  const text = useClockText();
  if (!incident.cert_in_required) {
    return (
      <Pill variant="tag" size="sm" icon={null}>
        {t("certInNotRequired")}
      </Pill>
    );
  }
  if (incident.cert_in_reported_at) {
    return (
      <Pill variant="done" size="sm">
        <span className="num">
          {t("certInReported", { when: formatDateTime(incident.cert_in_reported_at) ?? "" })}
        </span>
      </Pill>
    );
  }
  const clock = incident.cert_in_due_at ? clockFor(incident.cert_in_due_at) : null;
  if (!clock) return null;
  return (
    <Pill variant={clock.state === "overdue" ? "negative" : "review"} size="sm">
      <span className="num">{t("certInClock", { clock: text(clock) })}</span>
    </Pill>
  );
}

/**
 * Incidents (docs/16, ADR-0050; FR-DPDP-030): SchoolOS's own security incidents that may touch
 * school data. Each shows the CERT-In clock (6 hours from when SchoolOS noticed it), how many
 * affected schools have been told, and its status. Opening one needs
 * `platform.incidents.manage` and a fresh MFA sign-in; the summary holds categories and counts
 * only.
 */
export function IncidentsScreen() {
  const t = useTranslations("platform.incidents");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("operator");
  const can = useCan();
  const router = useRouter();
  const [now] = useState(() => istLocalInput());
  const list = usePagedList(INCIDENT_KEYS.list, (cursor) =>
    unwrap(
      api.GET("/api/v1/platform/incidents", {
        params: { query: { limit: LIST_PAGE_SIZE, ...(cursor ? { cursor } : {}) } },
      }),
    ),
  );

  if (!can(INCIDENTS_READ)) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const columns: ColumnV2<Incident>[] = [
    {
      key: "opened",
      stack: "title",
      header: t("colOpened"),
      cell: (row) => (
        <Link
          href={`/platform/incidents/${row.id}`}
          className="num font-semibold whitespace-nowrap text-primary underline-offset-4 after:absolute after:inset-0 hover:underline"
        >
          {formatDateTime(row.opened_at)}
        </Link>
      ),
    },
    {
      key: "severity",
      header: t("colSeverity"),
      cell: (row) => (
        <Pill variant={row.severity === "P1" ? "negative" : "tag"} size="sm" icon={null}>
          {t(`severity.${row.severity}`)}
        </Pill>
      ),
    },
    {
      key: "summary",
      stack: "wide",
      header: t("colSummary"),
      cell: (row) => <span className="block max-w-md text-sm break-words">{row.summary}</span>,
    },
    { key: "certIn", header: t("colCertIn"), cell: (row) => <CertInClock incident={row} /> },
    {
      key: "schools",
      header: t("colSchools"),
      numeric: true,
      cell: (row) =>
        t("notifiedOf", {
          notified: formatCount(row.schools_notified, locale) ?? "0",
          total: formatCount(row.schools_count, locale) ?? "0",
        }),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <IncidentStatusPill status={row.status} />,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
        actions={
          can(INCIDENTS_MANAGE) ? (
            <ActionDialog
              triggerLabel={t("open")}
              triggerVariant="primary"
              title={t("openTitle")}
              description={t("openBody")}
              confirmLabel={t("open")}
              consequence={t("openConsequence")}
              stepUp
              schema={newIncidentSchema}
              invalidate={[INCIDENT_KEYS.all]}
              errorNamespace="platform.incidents"
              submit={(data, idempotencyKey) =>
                unwrap(
                  api.POST("/api/v1/platform/incidents", {
                    headers: { "Idempotency-Key": idempotencyKey },
                    body: data,
                  }),
                )
              }
              onSuccess={(created) => router.push(`/platform/incidents/${created.id}`)}
            >
              {(errors) => (
                <>
                  <TextField
                    name="opened_at"
                    type="datetime-local"
                    label={t("openedAt")}
                    hint={t("openedAtHint")}
                    error={errors.opened_at}
                    defaultValue={now}
                    max={now}
                    className="max-w-xs"
                  />
                  <SelectField
                    name="severity"
                    label={t("severityLabel")}
                    error={errors.severity}
                    defaultValue=""
                    placeholder={tc("chooseOne")}
                    options={SEVERITIES.map((value) => ({
                      value,
                      label: t(`severity.${value}`),
                    }))}
                  />
                  <TextAreaField
                    name="summary"
                    label={t("summaryLabel")}
                    hint={t("summaryHint")}
                    error={errors.summary}
                    maxLength={500}
                    rows={4}
                  />
                  <label className="flex min-h-10 items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      name="cert_in_required"
                      className="mt-0.5 size-4 accent-primary"
                    />
                    <span>{t("certInRequiredLabel")}</span>
                  </label>
                </>
              )}
            </ActionDialog>
          ) : undefined
        }
      />
      <DataTableV2
        id="platform.incidents"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={
          list.state.status === "ready"
            ? { status: "ready", data: [...list.state.data] }
            : list.state
        }
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
      <ShowMore list={list} />
    </div>
  );
}

"use client";

import { useLocale, useTranslations } from "next-intl";
import * as z from "@/lib/zod";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { istLocalInput } from "@/features/breaches/schema";
import { BREACH_CATEGORIES } from "@/features/breaches/types";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDateTime } from "@/lib/format";
import { formList } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import { ifMatch, useCan, useOperatorMe, useSchoolDirectory } from "../data";
import { CertInClock, IncidentStatusPill, useClockText } from "./IncidentsScreen";
import {
  INCIDENTS_MANAGE,
  INCIDENT_KEYS,
  INCIDENT_STATUSES,
  addSchoolSchema,
  clockFor,
  confirmationOf,
  reportedSchema,
  type IncidentDetail,
  type IncidentSchool,
} from "./data";

/**
 * One incident (docs/16, ADR-0050; FR-DPDP-030, FR-DPDP-031, SEC-029): the CERT-In clock, the
 * affected schools with their notice clocks, the two-person confirmation before any school is
 * told (a second, different operator confirms), recording schools told by phone or e-mail
 * (dedicated hosts have no pull), and the status. Categories and counts only.
 */
export function IncidentDetailScreen({ incidentId }: { incidentId: string }) {
  const t = useTranslations("platform.incidents");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const can = useCan();
  const incident = useApiQuery(INCIDENT_KEYS.detail(incidentId), () =>
    unwrap(
      api.GET("/api/v1/platform/incidents/{incident_id}", {
        params: { path: { incident_id: incidentId } },
      }),
    ),
  );
  const crumbs = [
    { label: tn("dashboard"), href: "/platform" },
    { label: t("title"), href: "/platform/incidents" },
    { label: t("detailTitle") },
  ];
  if (incident.status === "loading") return <LoadingState label={tc("loading")} />;
  if (incident.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader breadcrumb={crumbs} title={t("detailTitle")} />
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {tc("loadErrorBody")}
        </Alert>
      </div>
    );
  }
  const data = incident.data;
  const manage = can(INCIDENTS_MANAGE) && data.status !== "closed";

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumb={crumbs}
        title={t("detailHeading", { when: formatDateTime(data.opened_at) ?? "" })}
        badge={<IncidentStatusPill status={data.status} />}
        description={data.summary}
        actions={manage ? <StatusDialog incident={data} /> : undefined}
      />
      <Card title={t("certInTitle")} description={t("certInDescription")}>
        <div className="flex flex-wrap items-center gap-3">
          <CertInClock incident={data} />
          {manage && data.cert_in_required && !data.cert_in_reported_at ? (
            <ReportedDialog incident={data} />
          ) : null}
        </div>
      </Card>
      <ConfirmationCard incident={data} manage={manage} />
      <Schools incident={data} manage={manage} />
    </div>
  );
}

function StatusDialog({ incident }: { incident: IncidentDetail }) {
  const t = useTranslations("platform.incidents");
  const api = useBffClient("operator");
  return (
    <ActionDialog
      triggerLabel={t("changeStatus")}
      triggerVariant="secondary"
      title={t("changeStatusTitle")}
      confirmLabel={t("changeStatus")}
      consequence={t("changeStatusConsequence")}
      stepUp
      schema={z.object({ status: z.enum(INCIDENT_STATUSES, { error: "chooseOption" }) })}
      invalidate={[INCIDENT_KEYS.all]}
      errorNamespace="platform.incidents"
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/platform/incidents/{incident_id}", {
            params: { path: { incident_id: incident.id } },
            headers: { "If-Match": ifMatch(incident.version) },
            body: { status: data.status },
          }),
        )
      }
    >
      {(errors) => (
        <SelectField
          name="status"
          label={t("colStatus")}
          error={errors.status}
          defaultValue={incident.status}
          options={INCIDENT_STATUSES.map((value) => ({ value, label: t(`status.${value}`) }))}
        />
      )}
    </ActionDialog>
  );
}

function ReportedDialog({ incident }: { incident: IncidentDetail }) {
  const t = useTranslations("platform.incidents");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const now = istLocalInput();
  return (
    <ActionDialog
      triggerLabel={t("recordReport")}
      triggerVariant="primary"
      triggerSize="sm"
      title={t("recordReportTitle")}
      description={t("recordReportBody")}
      confirmLabel={t("recordReport")}
      stepUp
      schema={reportedSchema}
      invalidate={[INCIDENT_KEYS.all]}
      errorNamespace="platform.incidents"
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/platform/incidents/{incident_id}", {
            params: { path: { incident_id: incident.id } },
            headers: { "If-Match": ifMatch(incident.version) },
            body: { cert_in_reported_at: data.cert_in_reported_at },
          }),
        )
      }
    >
      {(errors) => (
        <TextField
          name="cert_in_reported_at"
          type="datetime-local"
          label={t("reportedAt")}
          hint={tc("istHint")}
          error={errors.cert_in_reported_at}
          defaultValue={now}
          max={now}
          className="max-w-xs"
        />
      )}
    </ActionDialog>
  );
}

/** Two-person rule (SEC-029), spelled out: who confirmed, what is still needed, by when. */
function ConfirmationCard({ incident, manage }: { incident: IncidentDetail; manage: boolean }) {
  const t = useTranslations("platform.incidents");
  const api = useBffClient("operator");
  const me = useOperatorMe();
  const state = confirmationOf(incident, me?.operator_id);
  return (
    <Card title={t("confirmTitle")} description={t("confirmDescription")}>
      <div className="space-y-3 text-sm">
        {state.state === "confirmed" ? (
          <Pill variant="done">
            <span className="num">
              {t("confirmedAt", { when: formatDateTime(state.at) ?? "" })}
            </span>
          </Pill>
        ) : state.state === "waiting" ? (
          <>
            <Pill variant="review">
              <span className="num">
                {state.by ? t("waitingBy", { when: formatDateTime(state.by) ?? "" }) : t("waiting")}
              </span>
            </Pill>
            {state.mine ? <p className="text-ink-muted">{t("youConfirmed")}</p> : null}
          </>
        ) : (
          <p>{incident.schools_count === 0 ? t("addSchoolsFirst") : t("notConfirmed")}</p>
        )}
        {manage &&
        state.state !== "confirmed" &&
        !(state.state === "waiting" && state.mine) &&
        incident.schools_count > 0 ? (
          <ActionDialog
            triggerLabel={state.state === "waiting" ? t("confirmSecond") : t("confirm")}
            triggerVariant="primary"
            triggerSize="sm"
            title={state.state === "waiting" ? t("confirmSecondTitle") : t("confirmTitle")}
            description={t("confirmBody", {
              count: incident.schools_count,
            })}
            note={t("twoPersonNote")}
            confirmLabel={state.state === "waiting" ? t("confirmSecond") : t("confirm")}
            consequence={
              state.state === "waiting" ? t("confirmSecondConsequence") : t("confirmConsequence")
            }
            stepUp
            schema={z.object({})}
            invalidate={[INCIDENT_KEYS.all]}
            errorNamespace="platform.incidents"
            submit={() =>
              unwrap(
                api.POST("/api/v1/platform/incidents/{incident_id}/confirm", {
                  params: { path: { incident_id: incident.id } },
                }),
              )
            }
          />
        ) : null}
      </div>
    </Card>
  );
}

function Schools({ incident, manage }: { incident: IncidentDetail; manage: boolean }) {
  const t = useTranslations("platform.incidents");
  const tcat = useTranslations("breaches.categories");
  const locale = useLocale() as Locale;
  const clockText = useClockText();
  const columns: ColumnV2<IncidentSchool>[] = [
    {
      key: "school",
      stack: "title",
      header: t("colSchool"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-anywhere">
            <Value>{row.school_name}</Value>
          </span>
          {row.tenant_code ? (
            <span className="font-mono text-xs text-ink-muted">{row.tenant_code}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "mode",
      header: t("colMode"),
      cell: (row) => (
        <Value>{row.mode ? translateOr(t, `modes.${row.mode}`, "modes.other") : null}</Value>
      ),
    },
    {
      key: "categories",
      header: t("colCategories"),
      cell: (row) => (
        <span className="block max-w-xs text-sm">
          {row.categories.map((code) => translateOr(tcat, code, "other")).join(", ")}
        </span>
      ),
    },
    {
      key: "people",
      header: t("colPeople"),
      numeric: true,
      cell: (row) => formatCount(row.approx_people, locale),
    },
    {
      key: "window",
      header: t("colWindow"),
      cell: (row) => (
        <span className="num text-sm">
          {row.window_from || row.window_to
            ? t("window", {
                from: formatDateTime(row.window_from) ?? "—",
                to: formatDateTime(row.window_to) ?? "—",
              })
            : t("windowUnknown")}
        </span>
      ),
    },
    {
      key: "notice",
      header: t("colNotice"),
      cell: (row) => {
        if (row.school_notified_at) {
          return (
            <Pill variant="done" size="sm">
              <span className="num">
                {t("notifiedAt", { when: formatDateTime(row.school_notified_at) ?? "" })}
              </span>
            </Pill>
          );
        }
        const clock = row.notice_due_at ? clockFor(row.notice_due_at) : null;
        return clock ? (
          <Pill variant={clock.state === "overdue" ? "negative" : "review"} size="sm">
            <span className="num">{t("noticeClock", { clock: clockText(clock) })}</span>
          </Pill>
        ) : (
          <span className="text-sm text-ink-muted">{t("noticeAfterConfirm")}</span>
        );
      },
    },
    {
      key: "pulled",
      header: t("colPulled"),
      cell: (row) => (
        <span className="num text-sm">
          <Value>{row.pulled_at ? formatDateTime(row.pulled_at) : null}</Value>
        </span>
      ),
    },
  ];
  const confirmed = Boolean(incident.confirmed_at);
  const waiting = incident.schools.filter((row) => !row.school_notified_at);
  return (
    <section aria-labelledby="incident-schools" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="incident-schools" className="text-lg font-semibold text-ink">
            {t("schoolsTitle")}
          </h2>
          <p className="num text-sm text-ink-muted">
            {t("notifiedOf", {
              notified: formatCount(incident.schools_notified, locale) ?? "0",
              total: formatCount(incident.schools_count, locale) ?? "0",
            })}
          </p>
        </div>
        {manage ? (
          <div className="flex flex-wrap gap-2">
            {!confirmed ? <AddSchoolDialog incident={incident} /> : null}
            {confirmed && waiting.length > 0 ? (
              <NotifiedDialog incident={incident} schools={waiting} />
            ) : null}
          </div>
        ) : null}
      </div>
      <DataTableV2
        id="platform.incident-schools"
        stacked
        caption={t("schoolsTitle")}
        captionHidden
        columns={columns}
        state={{ status: "ready", data: [...incident.schools] }}
        rowKey={(row) => row.tenant_id}
        emptyTitle={t("schoolsEmptyTitle")}
        emptyBody={t("schoolsEmptyBody")}
      />
    </section>
  );
}

function AddSchoolDialog({ incident }: { incident: IncidentDetail }) {
  const t = useTranslations("platform.incidents");
  const tcat = useTranslations("breaches.categories");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const { schools } = useSchoolDirectory();
  const listed = new Set(incident.schools.map((row) => row.tenant_id));
  return (
    <ActionDialog
      triggerLabel={t("addSchool")}
      triggerVariant="secondary"
      title={t("addSchoolTitle")}
      description={t("addSchoolBody")}
      confirmLabel={t("addSchool")}
      consequence={incident.confirmed_by ? t("addSchoolClears") : undefined}
      schema={addSchoolSchema}
      extra={(form) => ({ categories: formList(form, "categories") })}
      invalidate={[INCIDENT_KEYS.all]}
      errorNamespace="platform.incidents"
      submit={(data) =>
        unwrap(
          api.POST("/api/v1/platform/incidents/{incident_id}/schools", {
            params: { path: { incident_id: incident.id } },
            body: data,
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="tenant_id"
            label={t("colSchool")}
            error={errors.tenant_id}
            defaultValue=""
            placeholder={tc("chooseOne")}
            options={schools
              .filter((school) => !listed.has(school.tenant_id))
              .map((school) => ({
                value: school.tenant_id,
                label: `${school.school_name} (${school.code})`,
              }))}
          />
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-ink">{t("colCategories")}</legend>
            {errors.categories ? (
              <p className="text-sm font-semibold text-danger">{errors.categories}</p>
            ) : null}
            <div className="grid gap-1 sm:grid-cols-2">
              {BREACH_CATEGORIES.map((category) => (
                <label
                  key={category}
                  className="flex min-h-10 items-start gap-2 py-1 text-sm pointer-coarse:min-h-11"
                >
                  <input
                    type="checkbox"
                    name="categories"
                    value={category}
                    aria-invalid={errors.categories ? true : undefined}
                    className="mt-0.5 size-4 shrink-0 accent-primary"
                  />
                  <span>{tcat(category)}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <TextField
            name="approx_people"
            label={t("approxPeople")}
            hint={t("approxPeopleHint")}
            error={errors.approx_people}
            inputMode="numeric"
            maxLength={7}
            className="max-w-xs"
          />
          <TextField
            name="window_from"
            type="datetime-local"
            label={t("windowFrom")}
            error={errors.window_from}
            className="max-w-xs"
          />
          <TextField
            name="window_to"
            type="datetime-local"
            label={t("windowTo")}
            error={errors.window_to}
            className="max-w-xs"
          />
        </>
      )}
    </ActionDialog>
  );
}

function NotifiedDialog({
  incident,
  schools,
}: {
  incident: IncidentDetail;
  schools: IncidentSchool[];
}) {
  const t = useTranslations("platform.incidents");
  const api = useBffClient("operator");
  return (
    <ActionDialog
      triggerLabel={t("recordNotified")}
      triggerVariant="secondary"
      title={t("recordNotifiedTitle")}
      description={t("recordNotifiedBody")}
      confirmLabel={t("recordNotified")}
      consequence={t("recordNotifiedConsequence")}
      stepUp
      schema={z.object({
        tenant_ids: z
          .array(z.string())
          .default([])
          .refine((values) => values.length > 0, { error: "chooseSchools" }),
      })}
      extra={(form) => ({ tenant_ids: formList(form, "tenant_ids") })}
      invalidate={[INCIDENT_KEYS.all]}
      errorNamespace="platform.incidents"
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/platform/incidents/{incident_id}", {
            params: { path: { incident_id: incident.id } },
            headers: { "If-Match": ifMatch(incident.version) },
            body: { notified_tenant_ids: data.tenant_ids },
          }),
        )
      }
    >
      {(errors) => (
        <fieldset className="space-y-2">
          <legend className="text-sm font-semibold text-ink">{t("colSchool")}</legend>
          {errors.tenant_ids ? (
            <p className="text-sm font-semibold text-danger">{errors.tenant_ids}</p>
          ) : null}
          {schools.map((school) => (
            <label
              key={school.tenant_id}
              className="flex min-h-10 items-start gap-2 py-1 text-sm pointer-coarse:min-h-11"
            >
              <input
                type="checkbox"
                name="tenant_ids"
                value={school.tenant_id}
                aria-invalid={errors.tenant_ids ? true : undefined}
                className="mt-0.5 size-4 shrink-0 accent-primary"
              />
              <span className="break-anywhere">
                {school.school_name ?? school.tenant_id}
                {school.tenant_code ? ` (${school.tenant_code})` : ""}
              </span>
            </label>
          ))}
        </fieldset>
      )}
    </ActionDialog>
  );
}

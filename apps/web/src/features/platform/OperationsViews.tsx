"use client";

import type {
  BreakGlassRequest,
  Deployment,
  FeatureFlag,
  FleetVersion,
  PlatformAuditEvent,
  PlatformAuditVerify,
} from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge, Pill } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { breakGlassTone, deploymentTone, known } from "@/features/status";
import { Link } from "@/i18n/navigation";
import { createBffFetch } from "@/lib/bff/fetch";
import { ApiError, unwrap, useApiQuery, useApiMutation, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDateTime } from "@/lib/format";
import { ready, type Loadable } from "@/lib/loadable";
import {
  FLAG_KEY_PATTERN,
  UUID_PATTERN,
  checkbox,
  optionalInt,
  optionalText,
  reason,
  requiredInt,
  uuid,
} from "@/lib/validation";
import { LIST_PAGE_SIZE, PK, ifMatch, useCan, usePagedList, useSchoolDirectory } from "./data";
import { DeploymentActions } from "./DeploymentActions";
import { FilterCard } from "./FilterCard";
import { FlagSwitch } from "./FlagSwitch";
import { HeartbeatPill } from "./HeartbeatPill";
import { Mono, MonoTime, TierTag } from "./pills";
import { ShowMore } from "./ShowMore";
import { UsageTable } from "./UsageTable";

/* ------------------------------------------------------------------ usage */

export interface UsageFilters {
  from?: string;
  to?: string;
  tenantId?: string;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** FR-PLT-020..021 (docs/16 §5.10): daily aggregates per school. */
export function UsageScreen({ filters = {} }: { filters?: UsageFilters }) {
  const t = useTranslations("platform.usage");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const { nameOf, schools } = useSchoolDirectory();
  const query = {
    ...(filters.from && ISO_DATE.test(filters.from) ? { from: filters.from } : {}),
    ...(filters.to && ISO_DATE.test(filters.to) ? { to: filters.to } : {}),
    ...(filters.tenantId && UUID_PATTERN.test(filters.tenantId)
      ? { tenant_id: filters.tenantId }
      : {}),
  };
  const usage = useApiQuery([...PK.usage, "list", query], () =>
    unwrap(api.GET("/api/v1/platform/usage", { params: { query } })),
  );
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
      />
      <FilterCard
        clearHref={filters.from || filters.to || filters.tenantId ? "/platform/usage" : undefined}
      >
        <TextField
          name="from"
          type="date"
          label={t("from")}
          defaultValue={filters.from ?? ""}
          className="w-44"
        />
        <TextField
          name="to"
          type="date"
          label={t("to")}
          defaultValue={filters.to ?? ""}
          className="w-44"
        />
        <SelectField
          name="school"
          label={t("colSchool")}
          placeholder={tc("all")}
          defaultValue={filters.tenantId ?? ""}
          options={schools.map((school) => ({
            value: school.tenant_id,
            label: `${school.school_name} (${school.code})`,
          }))}
          className="w-72"
        />
      </FilterCard>
      <UsageTable usage={usage} caption={t("title")} schoolName={nameOf} />
    </div>
  );
}

/* ------------------------------------------------------------------ flags */

const flagSchema = z.object({
  key: z
    .string()
    .trim()
    .max(100, { error: "tooLong" })
    .regex(FLAG_KEY_PATTERN, { error: "invalidFlagKey" }),
  description: optionalText(300),
  enabled: checkbox,
  rollout_percent: z
    .string()
    .trim()
    .refine((value) => value === "" || (/^\d+$/.test(value) && Number(value) <= 100), {
      error: "invalidPercent",
    })
    .transform((value) => (value === "" ? null : Number(value))),
  /** The flag's `version` when the dialog opened, for If-Match; empty for a new flag (AA-13). */
  version: optionalInt(2_147_483_647),
});

const overrideSchema = z.object({ tenant_id: uuid, enabled: z.enum(["on", "off"]) });

function FlagForm({ errors, flag }: { errors: Record<string, string>; flag?: FeatureFlag }) {
  const t = useTranslations("platform.flags");
  return (
    <>
      {/* Read once, when the dialog opens: a background reload must not move it (If-Match). */}
      <input type="hidden" name="version" defaultValue={flag?.version ?? ""} />
      <TextField
        name="key"
        label={t("colKey")}
        hint={flag ? undefined : t("keyHint")}
        error={errors.key}
        defaultValue={flag?.key ?? ""}
        readOnly={flag !== undefined}
        spellCheck={false}
        autoComplete="off"
      />
      <TextAreaField
        name="description"
        label={t("colDescription")}
        error={errors.description}
        defaultValue={flag?.description ?? ""}
        maxLength={300}
        rows={2}
      />
      <Toggle name="enabled" label={t("enabledLabel")} defaultChecked={flag?.enabled ?? false} />
      <TextField
        name="rollout_percent"
        label={t("colRollout")}
        hint={t("rolloutHint")}
        inputMode="numeric"
        error={errors.rollout_percent}
        defaultValue={flag?.rollout_percent?.toString() ?? ""}
      />
    </>
  );
}

/** FR-PLT-022 (docs/16 §5.11): global flags with % rollout and per-school overrides. */
export function FlagsScreen() {
  const t = useTranslations("platform.flags");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const locale = useLocale();
  const api = useBffClient("operator");
  const can = useCan();
  const manage = can("platform.flags.manage");
  const { nameOf, schools } = useSchoolDirectory();
  const flags = useApiQuery(
    [...PK.flags, "list"],
    async () => (await unwrap(api.GET("/api/v1/platform/flags"))).data,
  );
  const all = flags.status === "ready" ? flags.data : [];
  const globals: Loadable<readonly FeatureFlag[]> =
    flags.status === "ready" ? ready(all.filter((flag) => flag.tenant_id === null)) : flags;
  const overrides: Loadable<readonly FeatureFlag[]> =
    flags.status === "ready" ? ready(all.filter((flag) => flag.tenant_id !== null)) : flags;

  const save = (data: z.output<typeof flagSchema>) =>
    unwrap(
      api.PUT("/api/v1/platform/flags/{key}", {
        params: {
          path: { key: data.key },
          ...(data.version === null ? {} : { header: { "If-Match": ifMatch(data.version) } }),
        },
        body: {
          enabled: data.enabled,
          description: data.description,
          rollout_percent: data.rollout_percent,
        },
      }),
    );

  const columns: Column<FeatureFlag>[] = [
    {
      key: "key",
      header: t("colKey"),
      cell: (row) => <code className="font-mono text-xs">{row.key}</code>,
    },
    {
      key: "description",
      header: t("colDescription"),
      cell: (row) => <Value>{row.description}</Value>,
    },
    {
      key: "global",
      header: t("colGlobal"),
      cell: (row) => <FlagSwitch flag={row} manage={manage} />,
    },
    {
      key: "rollout",
      header: t("colRollout"),
      className: "text-right tabular-nums",
      cell: (row) =>
        row.rollout_percent === null ? (
          <Value>{null}</Value>
        ) : (
          <Mono>{t("rolloutValue", { percent: row.rollout_percent })}</Mono>
        ),
    },
    {
      key: "overrides",
      header: t("colOverrides"),
      className: "text-right tabular-nums",
      cell: (row) => (
        <Value>
          {formatCount(
            all.filter((flag) => flag.key === row.key && flag.tenant_id !== null).length,
            locale,
          )}
        </Value>
      ),
    },
    ...(manage
      ? [
          {
            key: "actions",
            header: tc("actions"),
            cell: (row: FeatureFlag) => (
              <div className="relative flex flex-wrap gap-2">
                <ActionDialog
                  triggerLabel={tc("edit")}
                  triggerSize="sm"
                  triggerDescription={row.key}
                  title={t("editTitle", { key: row.key })}
                  confirmLabel={tc("save")}
                  stepUp
                  schema={flagSchema}
                  invalidate={[PK.flags]}
                  submit={save}
                >
                  {(errors) => <FlagForm errors={errors} flag={row} />}
                </ActionDialog>
                <ActionDialog
                  triggerLabel={t("addOverride")}
                  triggerSize="sm"
                  triggerVariant="ghost"
                  triggerDescription={row.key}
                  title={t("addOverrideTitle", { key: row.key })}
                  confirmLabel={tc("save")}
                  stepUp
                  schema={overrideSchema}
                  invalidate={[PK.flags, PK.tenants]}
                  submit={(data) => {
                    // An existing override is replaced only with its ETag (AA-13).
                    const current = all.find(
                      (flag) => flag.key === row.key && flag.tenant_id === data.tenant_id,
                    );
                    return unwrap(
                      api.PUT("/api/v1/platform/flags/{key}/tenants/{tenant_id}", {
                        params: {
                          path: { key: row.key, tenant_id: data.tenant_id },
                          ...(current ? { header: { "If-Match": ifMatch(current.version) } } : {}),
                        },
                        body: { enabled: data.enabled === "on" },
                      }),
                    );
                  }}
                >
                  {(errors) => (
                    <>
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
                      <SelectField
                        name="enabled"
                        label={t("overrideValue")}
                        defaultValue="on"
                        options={[
                          { value: "on", label: t("forcedOn") },
                          { value: "off", label: t("forcedOff") },
                        ]}
                      />
                    </>
                  )}
                </ActionDialog>
              </div>
            ),
          },
        ]
      : []),
  ];

  const overrideColumns: Column<FeatureFlag>[] = [
    {
      key: "key",
      header: t("colKey"),
      cell: (row) => <code className="font-mono text-xs">{row.key}</code>,
    },
    { key: "school", header: t("colSchool"), cell: (row) => nameOf(row.tenant_id) },
    {
      key: "value",
      header: t("overrideValue"),
      cell: (row) => (
        <Pill variant={row.enabled ? "positive" : "tag"}>
          {row.enabled ? t("forcedOn") : t("forcedOff")}
        </Pill>
      ),
    },
    ...(manage
      ? [
          {
            key: "actions",
            header: tc("actions"),
            cell: (row: FeatureFlag) => (
              <ActionDialog
                triggerLabel={t("clearOverride")}
                triggerSize="sm"
                triggerVariant="ghost"
                triggerDescription={`${row.key} · ${nameOf(row.tenant_id)}`}
                title={t("clearOverrideTitle", { key: row.key })}
                confirmLabel={t("clearOverride")}
                stepUp
                schema={z.object({})}
                invalidate={[PK.flags, PK.tenants]}
                submit={() =>
                  unwrap(
                    api.DELETE("/api/v1/platform/flags/{key}/tenants/{tenant_id}", {
                      params: { path: { key: row.key, tenant_id: row.tenant_id ?? "" } },
                    }),
                  )
                }
              />
            ),
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
        actions={
          manage ? (
            <ActionDialog
              triggerLabel={t("newFlag")}
              triggerVariant="primary"
              title={t("newFlag")}
              confirmLabel={tc("save")}
              stepUp
              schema={flagSchema}
              invalidate={[PK.flags]}
              submit={save}
            >
              {(errors) => <FlagForm errors={errors} />}
            </ActionDialog>
          ) : undefined
        }
      />
      <Card title={t("globalTitle")} description={manage ? t("switchHint") : undefined}>
        <DataTable
          caption={t("globalTitle")}
          captionHidden
          columns={columns}
          state={globals}
          rowKey={(row) => row.key}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
      </Card>
      <Card title={t("overridesTitle")}>
        <DataTable
          caption={t("overridesTitle")}
          captionHidden
          columns={overrideColumns}
          state={overrides}
          rowKey={(row) => `${row.key}:${row.tenant_id ?? ""}`}
          emptyTitle={t("overridesEmptyTitle")}
          emptyBody={t("overridesEmptyBody")}
        />
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ fleet */

/** FR-PLT-023..025 (docs/16 §5.12): deployments, versions, last heartbeat, actions. */
export function FleetScreen({ status = "" }: { status?: string }) {
  const t = useTranslations("platform.fleet");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.deployment");
  const locale = useLocale();
  const api = useBffClient("operator");
  const query = status ? { status } : {};
  const deploymentList = usePagedList([...PK.deployments, "list", query], (cursor) =>
    unwrap(
      api.GET("/api/v1/platform/deployments", {
        params: { query: { ...query, limit: LIST_PAGE_SIZE, ...(cursor ? { cursor } : {}) } },
      }),
    ),
  );
  const deployments = deploymentList.state;
  const versions = useApiQuery(PK.versions, () =>
    unwrap(api.GET("/api/v1/platform/fleet/versions")),
  );
  const columns: Column<Deployment>[] = [
    {
      key: "school",
      header: t("colSchool"),
      cell: (row) => (
        <span className="flex min-w-40 flex-col gap-0.5">
          <Link
            href={`/platform/schools/${row.tenant_id}?tab=deployment`}
            className="font-semibold text-primary underline-offset-4 hover:underline"
          >
            {row.school_name}
          </Link>
          <code className="font-mono text-xs text-ink-muted">{row.tenant_code}</code>
        </span>
      ),
    },
    { key: "mode", header: t("colMode"), cell: (row) => <TierTag tier={row.mode} /> },
    {
      key: "region",
      header: t("colRegion"),
      cell: (row) => <span className="font-mono text-xs whitespace-nowrap">{row.region}</span>,
    },
    {
      key: "host",
      header: t("colHost"),
      cell: (row) => (
        <span className="font-mono text-xs whitespace-nowrap">
          <Value>{row.hostname ?? row.host_ref}</Value>
        </span>
      ),
    },
    {
      key: "domain",
      header: t("colDomain"),
      cell: (row) => (
        <span className="font-mono text-xs whitespace-nowrap">
          <Value>{row.custom_domain}</Value>
        </span>
      ),
    },
    {
      key: "version",
      header: t("colVersion"),
      cell: (row) => (
        <span className="font-mono text-xs">
          <Value>{row.app_version}</Value>
          {row.target_version && row.target_version !== row.app_version ? (
            <span className="block text-xs text-ink-muted">
              {t("targetValue", { version: row.target_version })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "heartbeat",
      header: t("colHeartbeat"),
      cell: (row) => (
        <span className="relative flex flex-col items-start gap-1">
          <HeartbeatPill at={row.last_heartbeat_at} status={row.status} />
          <MonoTime value={row.last_heartbeat_at} />
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => {
        const value = known(deploymentTone, row.status);
        return value ? (
          <Badge tone={deploymentTone[value]}>{tstatus(value)}</Badge>
        ) : (
          <Badge>{row.status}</Badge>
        );
      },
    },
    {
      key: "actions",
      header: tc("actions"),
      cell: (row) => <DeploymentActions deployment={row} />,
    },
  ];
  const versionColumns: Column<FleetVersion>[] = [
    {
      key: "version",
      header: t("colVersion"),
      cell: (row) => <code className="font-mono text-xs">{row.version}</code>,
    },
    {
      key: "count",
      header: t("colDeployments"),
      className: "text-right tabular-nums",
      cell: (row) => (
        <Mono>
          <Value>{formatCount(row.deployments, locale)}</Value>
        </Mono>
      ),
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
      />
      <FilterCard clearHref={status ? "/platform/fleet" : undefined}>
        <SelectField
          name="status"
          label={t("colStatus")}
          placeholder={tc("all")}
          defaultValue={status}
          options={(
            ["provisioning", "healthy", "degraded", "unreachable", "decommissioned"] as const
          ).map((value) => ({ value, label: tstatus(value) }))}
          className="w-52"
        />
      </FilterCard>
      <Card title={t("deploymentsTitle")}>
        <DataTable
          caption={t("deploymentsTitle")}
          captionHidden
          columns={columns}
          state={deployments}
          rowKey={(row) => row.id}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
        <ShowMore list={deploymentList} />
      </Card>
      <Card title={t("versionsTitle")}>
        <DataTable
          caption={t("versionsTitle")}
          captionHidden
          columns={versionColumns}
          state={versions}
          rowKey={(row) => row.version}
          emptyTitle={t("versionsEmptyTitle")}
          emptyBody={t("versionsEmptyBody")}
        />
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------ break-glass */

/** ADR-0023: sign in to the school app with the support client for one approved request. */
export function supportSignInUrl(row: Pick<BreakGlassRequest, "id" | "tenant_id">): string {
  const params = new URLSearchParams({ request: row.id, tenant: row.tenant_id });
  return `/bff/auth/support/login?${params.toString()}`;
}

const breakGlassSchema = z
  .object({
    tenant_id: uuid,
    reason_code: z.enum(["support_request", "security_incident", "legal_obligation"], {
      error: "chooseOption",
    }),
    reason,
    duration_minutes: requiredInt(15, 480),
    emergency: checkbox,
  })
  // A-13: emergency access (no school approval) only for an incident or a legal obligation.
  .refine((data) => !data.emergency || data.reason_code !== "support_request", {
    path: ["reason_code"],
    error: "emergencyReason",
  });

/**
 * Break-glass requests (docs/16 §5.15; 07 §6.4): status list for every operator, request
 * form (M1 workflow), and the second confirmation of an emergency request — which must come
 * from a different operator (409 same_operator otherwise). An active request links to the
 * support sign-in of the school app (ADR-0023): a plain link, because the BFF route starts an
 * OIDC redirect; the API lets only the requesting operator in.
 */
export function BreakGlassScreen() {
  const t = useTranslations("platform.breakGlass");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.breakGlass");
  const api = useBffClient("operator");
  const can = useCan();
  const hintId = useId();
  const { nameOf, schools } = useSchoolDirectory();
  const requestList = usePagedList([...PK.breakGlass, "list"], (cursor) =>
    unwrap(
      api.GET("/api/v1/platform/break-glass-requests", {
        params: { query: { limit: LIST_PAGE_SIZE, ...(cursor ? { cursor } : {}) } },
      }),
    ),
  );
  const requests = requestList.state;
  const columns: Column<BreakGlassRequest>[] = [
    { key: "school", header: t("colSchool"), cell: (row) => nameOf(row.tenant_id) },
    {
      key: "reason",
      header: t("colReason"),
      cell: (row) => (
        <span>
          <span className="block font-semibold">
            {t(`reasonCodes.${row.reason_code as "support_request"}`)}
          </span>
          <span className="text-ink-muted">{row.reason}</span>
        </span>
      ),
    },
    {
      key: "duration",
      header: t("colDuration"),
      cell: (row) => t("durationMinutes", { minutes: row.duration_minutes }),
    },
    {
      key: "emergency",
      header: t("colEmergency"),
      cell: (row) => (row.emergency ? <EmergencyConfirmations request={row} /> : tc("no")),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => {
        const value = known(breakGlassTone, row.status);
        return value ? (
          <Badge tone={breakGlassTone[value]}>{tstatus(value)}</Badge>
        ) : (
          <Badge>{row.status}</Badge>
        );
      },
    },
    {
      key: "created",
      header: t("colCreated"),
      cell: (row) => <MonoTime value={row.created_at} />,
    },
    {
      key: "actions",
      header: tc("actions"),
      cell: (row) => (
        <span className="flex flex-wrap items-start gap-2">
          {breakGlassAction(row)}
          {row.status === "requested" && can("platform.breakglass.request") ? (
            <ActionDialog
              triggerLabel={t("withdraw")}
              triggerSize="sm"
              triggerVariant="ghost"
              triggerDescription={nameOf(row.tenant_id)}
              title={t("withdrawTitle")}
              description={t("withdrawBody")}
              confirmLabel={t("withdraw")}
              schema={z.object({})}
              invalidate={[PK.breakGlass]}
              submit={() =>
                unwrap(
                  api.POST("/api/v1/platform/break-glass-requests/{request_id}/withdraw", {
                    params: { path: { request_id: row.id } },
                  }),
                )
              }
            />
          ) : null}
        </span>
      ),
    },
  ];
  function breakGlassAction(row: BreakGlassRequest) {
    return row.status === "active" ? (
      <span className="flex flex-col gap-1">
        <a
          href={supportSignInUrl(row)}
          className={buttonClasses("secondary", "sm")}
          aria-describedby={`${hintId}-${row.id}`}
        >
          {t("openSchool")}
        </a>
        <span id={`${hintId}-${row.id}`} className="text-xs text-ink-muted">
          {t("openSchoolHint")}
        </span>
      </span>
    ) : row.emergency &&
      row.status === "requested" &&
      row.emergency_confirmed_by_2 === null &&
      can("platform.breakglass.emergency") ? (
      <ActionDialog
        triggerLabel={t("confirmEmergency")}
        triggerSize="sm"
        triggerVariant="danger"
        triggerDescription={nameOf(row.tenant_id)}
        title={t("confirmEmergencyTitle")}
        description={t("confirmEmergencyBody")}
        note={t("twoPersonNote")}
        confirmLabel={t("confirmEmergency")}
        confirmVariant="danger"
        stepUp
        schema={z.object({})}
        invalidate={[PK.breakGlass]}
        submit={() =>
          unwrap(
            api.POST("/api/v1/platform/break-glass-requests/{request_id}/emergency-confirm", {
              params: { path: { request_id: row.id } },
            }),
          )
        }
      />
    ) : null;
  }
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
        actions={
          can("platform.breakglass.request") ? (
            <ActionDialog
              triggerLabel={t("request")}
              triggerVariant="primary"
              title={t("requestTitle")}
              description={t("requestBody")}
              confirmLabel={t("request")}
              schema={breakGlassSchema}
              invalidate={[PK.breakGlass]}
              submit={(data) =>
                unwrap(
                  api.POST("/api/v1/platform/break-glass-requests", {
                    // DL-10: only section_id/class_id narrow a grant; the panel asks for the
                    // whole school and says so (requestBody).
                    body: { ...data, scope: {} },
                  }),
                )
              }
            >
              {(errors) => (
                <>
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
                  <SelectField
                    name="reason_code"
                    label={t("reasonCode")}
                    error={errors.reason_code}
                    defaultValue="support_request"
                    options={(
                      ["support_request", "security_incident", "legal_obligation"] as const
                    ).map((value) => ({ value, label: t(`reasonCodes.${value}`) }))}
                  />
                  <TextAreaField
                    name="reason"
                    label={t("colReason")}
                    hint={tc("reasonHint")}
                    error={errors.reason}
                    maxLength={500}
                    rows={3}
                  />
                  <TextField
                    name="duration_minutes"
                    label={t("durationLabel")}
                    hint={t("durationHint")}
                    inputMode="numeric"
                    defaultValue="60"
                    error={errors.duration_minutes}
                  />
                  <label className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger-soft p-3 text-sm text-danger">
                    <input
                      type="checkbox"
                      name="emergency"
                      className="mt-1 size-4 accent-primary"
                    />
                    {t("emergencyLabel")}
                  </label>
                </>
              )}
            </ActionDialog>
          ) : undefined
        }
      />
      <Alert tone="info">{t("m1Note")}</Alert>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={requests}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
      <ShowMore list={requestList} />
    </div>
  );
}

/* ------------------------------------------------------------ platform audit */

export interface PlatformAuditFilters {
  actor?: string;
  action?: string;
  tenantId?: string;
  from?: string;
  to?: string;
}

/** YYYY-MM-DD (IST day) → RFC 3339 bounds for the API. */
function dayBound(value: string | undefined, end: boolean): string | undefined {
  if (!value || !ISO_DATE.test(value)) return undefined;
  return `${value}T${end ? "23:59:59" : "00:00:00"}+05:30`;
}

function summaryText(summary: Record<string, unknown>): string {
  return Object.entries(summary)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`)
    .join("; ");
}

/** FR-PLT-029 (docs/16 §5.17): filters, CSV export and chain verification. */
export function PlatformAuditScreen({ filters = {} }: { filters?: PlatformAuditFilters }) {
  const t = useTranslations("platform.audit");
  const tn = useTranslations("platform.nav");
  const tc = useTranslations("common");
  const locale = useLocale();
  const api = useBffClient("operator");
  const { nameOf, schools } = useSchoolDirectory();
  const [exportError, setExportError] = useState<unknown>(undefined);
  const [exporting, setExporting] = useState(false);
  const query = {
    limit: 200,
    ...(filters.actor && UUID_PATTERN.test(filters.actor) ? { actor: filters.actor } : {}),
    ...(filters.action?.trim() ? { action: filters.action.trim() } : {}),
    ...(filters.tenantId && UUID_PATTERN.test(filters.tenantId)
      ? { tenant_id: filters.tenantId }
      : {}),
    ...(dayBound(filters.from, false) ? { from: dayBound(filters.from, false) as string } : {}),
    ...(dayBound(filters.to, true) ? { to: dayBound(filters.to, true) as string } : {}),
  };
  const events = useApiQuery(
    [...PK.audit, "list", query],
    async () =>
      (await unwrap(api.GET("/api/v1/platform/audit/events", { params: { query } }))).data,
  );
  const verify = useApiMutation<void, PlatformAuditVerify>(() =>
    unwrap(api.POST("/api/v1/platform/audit/verify")),
  );

  async function downloadCsv() {
    setExportError(undefined);
    setExporting(true);
    try {
      const search = new URLSearchParams(
        Object.entries(query).map(([key, value]) => [key, String(value)]),
      );
      const send = createBffFetch({ kind: "operator", locale });
      const response = await send(
        new Request(
          `${window.location.origin}/bff/api/v1/platform/audit/events?${search.toString()}`,
          { headers: { accept: "text/csv" } },
        ),
      );
      if (!response.ok) {
        const problem = (await response.json().catch(() => ({}))) as { code?: string };
        throw new ApiError(response.status, problem.code, problem);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `platform-audit-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (error) {
      setExportError(error);
    } finally {
      setExporting(false);
    }
  }

  const columns: Column<PlatformAuditEvent>[] = [
    {
      key: "seq",
      header: t("colSeq"),
      className: "tabular-nums",
      cell: (row) => <Mono>{row.seq}</Mono>,
    },
    {
      key: "when",
      header: t("colWhen"),
      cell: (row) => <MonoTime value={row.occurred_at} />,
    },
    {
      key: "operator",
      header: t("colOperator"),
      cell: (row) =>
        row.actor_type === "system" ? (
          <Pill variant="tag">{t("system")}</Pill>
        ) : (
          <span className="whitespace-nowrap">
            {t("operatorShort")}{" "}
            <code className="font-mono text-xs">{row.actor_id?.slice(0, 8) ?? ""}</code>
          </span>
        ),
    },
    {
      key: "action",
      header: t("colAction"),
      cell: (row) => <code className="font-mono text-xs">{row.action}</code>,
    },
    {
      key: "school",
      header: t("colSchool"),
      cell: (row) => <Value>{row.subject_tenant_id ? nameOf(row.subject_tenant_id) : null}</Value>,
    },
    {
      key: "resource",
      header: t("colResource"),
      cell: (row) => (
        <code className="font-mono text-xs">
          {`${row.resource_type}${row.resource_id ? ` · ${row.resource_id.slice(0, 8)}` : ""}`}
        </code>
      ),
    },
    {
      key: "summary",
      header: t("colSummary"),
      cell: (row) => <span className="block max-w-md break-words">{summaryText(row.summary)}</span>,
    },
  ];

  const result = verify.data;
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
        actions={
          <>
            <Button variant="secondary" onClick={downloadCsv} disabled={exporting}>
              {exporting ? tc("working") : t("exportCsv")}
            </Button>
            <Button onClick={() => verify.mutate()} disabled={verify.isPending}>
              {verify.isPending ? t("verifying") : t("verify")}
            </Button>
          </>
        }
      />
      {result ? (
        result.ok ? (
          <Alert tone="success" live title={t("verifyOk")}>
            {t("verifyOkBody", { count: result.checked })}
          </Alert>
        ) : (
          <Alert tone="danger" live title={t("verifyFailed")}>
            {t("verifyFailedBody", { seq: result.first_bad_seq ?? 0 })}
          </Alert>
        )
      ) : null}
      <ApiErrorAlert error={verify.error ?? undefined} />
      <ApiErrorAlert error={exportError} />
      <FilterCard
        clearHref={
          filters.actor || filters.action || filters.tenantId || filters.from || filters.to
            ? "/platform/audit"
            : undefined
        }
      >
        <TextField
          name="actor"
          label={t("filterOperator")}
          hint={t("filterOperatorHint")}
          defaultValue={filters.actor ?? ""}
          className="w-80"
          spellCheck={false}
        />
        <TextField
          name="action"
          label={t("colAction")}
          defaultValue={filters.action ?? ""}
          className="w-56"
        />
        <SelectField
          name="school"
          label={t("colSchool")}
          placeholder={tc("all")}
          defaultValue={filters.tenantId ?? ""}
          options={schools.map((school) => ({
            value: school.tenant_id,
            label: `${school.school_name} (${school.code})`,
          }))}
          className="w-64"
        />
        <TextField
          name="from"
          type="date"
          label={t("from")}
          defaultValue={filters.from ?? ""}
          className="w-44"
        />
        <TextField
          name="to"
          type="date"
          label={t("to")}
          defaultValue={filters.to ?? ""}
          className="w-44"
        />
      </FilterCard>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={events}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}

/** Two-person rule for an emergency request, spelled out: who has confirmed so far. */
function EmergencyConfirmations({ request }: { request: BreakGlassRequest }) {
  const t = useTranslations("platform.breakGlass");
  const confirmed = [request.emergency_confirmed_by_1, request.emergency_confirmed_by_2].filter(
    Boolean,
  ).length;
  return (
    <span className="relative flex flex-col items-start gap-1">
      <Badge tone="danger">{t("emergency")}</Badge>
      <Pill variant={confirmed >= 2 ? "done" : "review"}>
        {t("confirmations", { count: confirmed })}
      </Pill>
      {confirmed < 2 ? (
        <span className="text-xs text-ink-muted">{t("needsSecondOperator")}</span>
      ) : null}
      {request.confirm_by ? (
        <span className="text-xs text-ink-muted">
          {t("confirmBy", { date: formatDateTime(request.confirm_by) ?? "" })}
        </span>
      ) : null}
    </span>
  );
}

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
import { useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { breakGlassTone, deploymentTone, known } from "@/features/status";
import { createBffFetch } from "@/lib/bff/fetch";
import { ApiError, unwrap, useApiQuery, useApiMutation, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDateTime } from "@/lib/format";
import { ready, type Loadable } from "@/lib/loadable";
import {
  FLAG_KEY_PATTERN,
  UUID_PATTERN,
  checkbox,
  optionalText,
  reason,
  requiredInt,
  uuid,
} from "@/lib/validation";
import { PK, useCan, useSchoolDirectory } from "./data";
import { DeploymentActions } from "./DeploymentActions";
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
      <PageHeader title={t("title")} description={t("description")} />
      <form method="get" className="flex flex-wrap items-end gap-3">
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
        <Button type="submit" variant="secondary">
          {tc("applyFilters")}
        </Button>
      </form>
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
});

const overrideSchema = z.object({ tenant_id: uuid, enabled: z.enum(["on", "off"]) });

function FlagForm({ errors, flag }: { errors: Record<string, string>; flag?: FeatureFlag }) {
  const t = useTranslations("platform.flags");
  return (
    <>
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
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          name="enabled"
          defaultChecked={flag?.enabled ?? false}
          className="size-4 accent-primary"
        />
        {t("enabledLabel")}
      </label>
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
        params: { path: { key: data.key } },
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
      cell: (row) => (row.enabled ? <Badge tone="success">{tc("yes")}</Badge> : tc("no")),
    },
    {
      key: "rollout",
      header: t("colRollout"),
      className: "text-right tabular-nums",
      cell: (row) =>
        row.rollout_percent === null ? (
          <Value>{null}</Value>
        ) : (
          t("rolloutValue", { percent: row.rollout_percent })
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
              <div className="flex flex-wrap gap-2">
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
                  submit={(data) =>
                    unwrap(
                      api.PUT("/api/v1/platform/flags/{key}/tenants/{tenant_id}", {
                        params: { path: { key: row.key, tenant_id: data.tenant_id } },
                        body: { enabled: data.enabled === "on" },
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
        <Badge tone={row.enabled ? "success" : "neutral"}>
          {row.enabled ? t("forcedOn") : t("forcedOff")}
        </Badge>
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
      <Card title={t("globalTitle")}>
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
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.deployment");
  const tmode = useTranslations("deploymentMode");
  const locale = useLocale();
  const api = useBffClient("operator");
  const query = status ? { status } : {};
  const deployments = useApiQuery(
    [...PK.deployments, "list", query],
    async () => (await unwrap(api.GET("/api/v1/platform/deployments", { params: { query } }))).data,
  );
  const versions = useApiQuery(PK.versions, () =>
    unwrap(api.GET("/api/v1/platform/fleet/versions")),
  );
  const columns: Column<Deployment>[] = [
    {
      key: "school",
      header: t("colSchool"),
      cell: (row) => `${row.school_name} (${row.tenant_code})`,
    },
    { key: "mode", header: t("colMode"), cell: (row) => tmode(row.mode) },
    { key: "region", header: t("colRegion"), cell: (row) => row.region },
    {
      key: "host",
      header: t("colHost"),
      cell: (row) => <Value>{row.hostname ?? row.host_ref}</Value>,
    },
    { key: "domain", header: t("colDomain"), cell: (row) => <Value>{row.custom_domain}</Value> },
    {
      key: "version",
      header: t("colVersion"),
      cell: (row) => (
        <span>
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
      cell: (row) => <Value>{formatDateTime(row.last_heartbeat_at)}</Value>,
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
      cell: (row) => <Value>{formatCount(row.deployments, locale)}</Value>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <form method="get" className="flex flex-wrap items-end gap-3">
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
        <Button type="submit" variant="secondary">
          {tc("applyFilters")}
        </Button>
      </form>
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

const breakGlassSchema = z.object({
  tenant_id: uuid,
  reason_code: z.enum(["support_request", "security_incident", "legal_obligation"], {
    error: "chooseOption",
  }),
  reason,
  duration_minutes: requiredInt(15, 480),
  emergency: checkbox,
});

/**
 * Break-glass requests (docs/16 §5.15; 07 §6.4): status list for every operator, request
 * form (M1 workflow), and the second confirmation of an emergency request — which must come
 * from a different operator (409 same_operator otherwise).
 */
export function BreakGlassScreen() {
  const t = useTranslations("platform.breakGlass");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.breakGlass");
  const api = useBffClient("operator");
  const can = useCan();
  const { nameOf, schools } = useSchoolDirectory();
  const requests = useApiQuery(
    [...PK.breakGlass, "list"],
    async () => (await unwrap(api.GET("/api/v1/platform/break-glass-requests"))).data,
  );
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
      cell: (row) =>
        row.emergency ? (
          <span>
            <Badge tone="danger">{t("emergency")}</Badge>
            <span className="block text-xs text-ink-muted">
              {t("confirmations", {
                count: [row.emergency_confirmed_by_1, row.emergency_confirmed_by_2].filter(Boolean)
                  .length,
              })}
            </span>
          </span>
        ) : (
          tc("no")
        ),
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
      cell: (row) => <Value>{formatDateTime(row.created_at)}</Value>,
    },
    {
      key: "actions",
      header: tc("actions"),
      cell: (row) =>
        row.emergency &&
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
        ) : null,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
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
                    body: { ...data, scope: { access: "read" } },
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
                  <label className="flex items-start gap-2 text-sm">
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
    { key: "seq", header: t("colSeq"), className: "tabular-nums", cell: (row) => row.seq },
    {
      key: "when",
      header: t("colWhen"),
      cell: (row) => <Value>{formatDateTime(row.occurred_at)}</Value>,
    },
    {
      key: "operator",
      header: t("colOperator"),
      cell: (row) =>
        row.actor_type === "system"
          ? t("system")
          : `${t("operatorShort")} · ${row.actor_id?.slice(0, 8) ?? ""}`,
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
      cell: (row) =>
        `${row.resource_type}${row.resource_id ? ` · ${row.resource_id.slice(0, 8)}` : ""}`,
    },
    { key: "summary", header: t("colSummary"), cell: (row) => summaryText(row.summary) },
  ];

  const result = verify.data;
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
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
      <form method="get" className="flex flex-wrap items-end gap-3">
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
        <Button type="submit" variant="secondary">
          {tc("applyFilters")}
        </Button>
      </form>
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

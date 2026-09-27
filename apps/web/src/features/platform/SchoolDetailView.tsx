"use client";

import type { FeatureFlag, TenantDetail } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { TabNav } from "@/components/ui/TabNav";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { deploymentTone, known, schoolTone, subscriptionTone } from "@/features/status";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDate, formatDateTime, formatInr } from "@/lib/format";
import { ready, type Loadable } from "@/lib/loadable";
import { reason } from "@/lib/validation";
import { billingAccountSchema } from "./billing-account";
import { BillingAccountFields } from "./BillingAccountFields";
import { PK, ifMatch, useCan, usePlanDirectory } from "./data";
import { DeploymentActions } from "./DeploymentActions";
import { InvoiceTable } from "./InvoiceTable";
import {
  ProvisioningLabel,
  ProvisioningStatus,
  ResumeProvisioningAction,
} from "./ProvisioningStatus";
import { TicketTable } from "./SupportScreens";
import { SCHOOL_TABS, type SchoolTab } from "./school-tabs";
import { ReasonField, SubscriptionActions } from "./SubscriptionActions";
import { UsageTable } from "./UsageTable";

const reasonSchema = z.object({ reason });

/**
 * FR-PLT-001..005 school detail (docs/16 §5.3, §5.5): tenant metadata, billing and fleet
 * only (no student data), with activate / suspend / reactivate and two-person offboarding.
 * While the school is `provisioning` it shows where setup stands and offers "Resume
 * provisioning" (FR-PLT-002, docs/16 §5.4); go-live is refused until setup has finished.
 */
export function SchoolDetailScreen({ schoolId, tab }: { schoolId: string; tab: SchoolTab }) {
  const t = useTranslations("platform.schoolDetail");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tschool = useTranslations("status.school");
  const api = useBffClient("operator");
  const can = useCan();
  const path = { tenant_id: schoolId };
  const detail = useApiQuery(PK.tenant(schoolId), () =>
    unwrap(api.GET("/api/v1/platform/tenants/{tenant_id}", { params: { path } })),
  );
  const school = detail.status === "ready" ? detail.data : null;
  const invalidate = [PK.tenants, PK.dashboard, PK.deployments] as const;

  const tabs = SCHOOL_TABS.map((id) => ({
    id,
    label: t(`tabs.${id}`),
    href:
      id === "overview"
        ? `/platform/schools/${schoolId}`
        : `/platform/schools/${schoolId}?tab=${id}`,
  }));

  function actions(data: TenantDetail) {
    const status = data.tenant_status;
    const offboardPending =
      data.offboard_requested_at !== null && data.offboard_approved_at === null;
    return (
      <>
        {status === "provisioning" && can("platform.tenants.provision") ? (
          <>
            {data.provisioning?.resumable ? <ResumeProvisioningAction schoolId={schoolId} /> : null}
            <ActionDialog
              triggerLabel={t("activate")}
              triggerVariant={data.provisioning?.resumable ? "secondary" : "primary"}
              title={t("activateTitle")}
              description={t("activateBody")}
              note={
                data.provisioning && data.provisioning.state !== "completed"
                  ? t("provisioning.activateIncomplete")
                  : undefined
              }
              confirmLabel={t("activate")}
              stepUp
              schema={z.object({})}
              invalidate={invalidate}
              submit={() =>
                unwrap(
                  api.POST("/api/v1/platform/tenants/{tenant_id}/activate", { params: { path } }),
                )
              }
            />
            <ActionDialog
              triggerLabel={t("resendInvite")}
              title={t("resendInviteTitle")}
              description={t("resendInviteBody")}
              confirmLabel={t("resendInvite")}
              schema={z.object({})}
              invalidate={invalidate}
              submit={() =>
                unwrap(
                  api.POST("/api/v1/platform/tenants/{tenant_id}/owner-invite:resend", {
                    params: { path },
                  }),
                )
              }
            />
          </>
        ) : null}
        {status === "active" && can("platform.tenants.suspend") ? (
          <ActionDialog
            triggerLabel={t("suspend")}
            title={t("suspendDialogTitle")}
            description={t("suspendDialogBody")}
            confirmLabel={t("suspendConfirm")}
            confirmVariant="danger"
            stepUp
            schema={reasonSchema}
            invalidate={invalidate}
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/platform/tenants/{tenant_id}/suspend", {
                  params: { path },
                  body: { reason: input.reason },
                }),
              )
            }
          >
            {(errors) => <ReasonField error={errors.reason} />}
          </ActionDialog>
        ) : null}
        {status === "suspended" && can("platform.tenants.suspend") ? (
          <ActionDialog
            triggerLabel={t("reactivate")}
            title={t("reactivateTitle")}
            description={t("reactivateBody")}
            confirmLabel={t("reactivate")}
            stepUp
            schema={reasonSchema}
            invalidate={invalidate}
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/platform/tenants/{tenant_id}/reactivate", {
                  params: { path },
                  body: { reason: input.reason },
                }),
              )
            }
          >
            {(errors) => <ReasonField error={errors.reason} />}
          </ActionDialog>
        ) : null}
        {can("platform.tenants.offboard") &&
        data.offboard_requested_at === null &&
        (status === "active" || status === "suspended") ? (
          <ActionDialog
            triggerLabel={t("offboard")}
            triggerVariant="danger"
            title={t("offboardTitle")}
            description={t("offboardBody")}
            note={t("twoPersonNote")}
            confirmLabel={t("offboardConfirm")}
            confirmVariant="danger"
            stepUp
            schema={reasonSchema}
            invalidate={invalidate}
            submit={(input) =>
              unwrap(
                api.POST("/api/v1/platform/tenants/{tenant_id}/offboarding", {
                  params: { path },
                  body: { reason: input.reason },
                }),
              )
            }
          >
            {(errors) => <ReasonField error={errors.reason} label={t("offboardReason")} />}
          </ActionDialog>
        ) : null}
        {can("platform.tenants.offboard") && offboardPending ? (
          <ActionDialog
            triggerLabel={t("approveOffboard")}
            triggerVariant="danger"
            title={t("approveOffboardTitle")}
            description={t("approveOffboardBody")}
            note={t("twoPersonNote")}
            confirmLabel={t("approveOffboard")}
            confirmVariant="danger"
            stepUp
            schema={z.object({})}
            invalidate={invalidate}
            submit={() =>
              unwrap(
                api.POST("/api/v1/platform/tenants/{tenant_id}/offboarding:approve", {
                  params: { path },
                }),
              )
            }
          />
        ) : null}
      </>
    );
  }

  function panel() {
    if (detail.status === "loading") return <LoadingState label={tc("loading")} />;
    if (detail.status === "error" || detail.status === "unavailable" || !school) {
      return (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {detail.status === "error" && detail.reason
            ? te(`load.${detail.reason}`)
            : tc("loadErrorBody")}
        </Alert>
      );
    }
    switch (tab) {
      case "overview":
        return <OverviewTab school={school} />;
      case "subscription":
        return <SubscriptionTab school={school} />;
      case "invoices":
        return <InvoiceTable invoices={ready(school.invoices)} caption={t("tabs.invoices")} />;
      case "usage":
        return <UsageTab schoolId={schoolId} />;
      case "deployment":
        return <DeploymentTab schoolId={schoolId} />;
      case "flags":
        return <FlagsTab school={school} />;
      case "tickets":
        return <TicketsTab schoolId={schoolId} />;
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={school?.school_name ?? t("title")}
        description={school ? t("codeLine", { code: school.code }) : undefined}
        badge={
          school ? (
            <Badge tone={schoolTone[school.tenant_status]}>{tschool(school.tenant_status)}</Badge>
          ) : undefined
        }
        actions={school ? actions(school) : undefined}
      />
      {school?.offboard_requested_at && !school.offboard_approved_at ? (
        <Alert tone="warning" title={t("offboardPendingTitle")}>
          {t("offboardPendingBody", {
            date: formatDateTime(school.offboard_requested_at) ?? "",
          })}
        </Alert>
      ) : null}
      {school?.offboard_approved_at ? (
        <Alert tone="warning" title={t("offboardApprovedTitle")}>
          {t("offboardApprovedBody", { date: formatDateTime(school.offboard_approved_at) ?? "" })}
        </Alert>
      ) : null}
      {school ? <ProvisioningStatus school={school} /> : null}
      <p className="text-sm text-ink-muted">{t("offboardNote")}</p>
      <TabNav label={t("tabsLabel")} items={tabs} activeId={tab} />
      <Card title={t(`tabs.${tab}`)}>{panel()}</Card>
    </div>
  );
}

function OverviewTab({ school }: { school: TenantDetail }) {
  const t = useTranslations("platform.schoolDetail");
  const tmode = useTranslations("deploymentMode");
  const tschool = useTranslations("status.school");
  const locale = useLocale();
  const count = (value: number | undefined) => <Value>{formatCount(value, locale)}</Value>;
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="text-ink-muted">{t("fields.code")}</dt>
        <dd className="font-mono">{school.code}</dd>
        <dt className="text-ink-muted">{t("fields.status")}</dt>
        <dd>
          {tschool(school.tenant_status)}
          {school.tenant_status_reason ? ` · ${school.tenant_status_reason}` : ""}
        </dd>
        {school.provisioning ? (
          <>
            <dt className="text-ink-muted">{t("fields.setup")}</dt>
            <dd>
              <ProvisioningLabel school={school} />
            </dd>
          </>
        ) : null}
        <dt className="text-ink-muted">{t("fields.deployment")}</dt>
        <dd>{tmode(school.tier)}</dd>
        <dt className="text-ink-muted">{t("fields.boards")}</dt>
        <dd>
          <Value>{school.boards.join(", ")}</Value>
        </dd>
        <dt className="text-ink-muted">{t("fields.plan")}</dt>
        <dd>
          <Value>{school.plan_code}</Value>
        </dd>
        <dt className="text-ink-muted">{t("fields.createdOn")}</dt>
        <dd>
          <Value>{formatDate(school.created_at)}</Value>
        </dd>
        <dt className="text-ink-muted">{t("fields.version")}</dt>
        <dd>
          <Value>{school.app_version}</Value>
        </dd>
        <dt className="text-ink-muted">{t("fields.users")}</dt>
        <dd>{count(school.counts?.users)}</dd>
        <dt className="text-ink-muted">{t("fields.activeMembers")}</dt>
        <dd>{count(school.counts?.active_memberships)}</dd>
        <dt className="text-ink-muted">{t("fields.sections")}</dt>
        <dd>{count(school.counts?.sections)}</dd>
        <dt className="text-ink-muted">{t("fields.openTickets")}</dt>
        <dd>{count(school.open_tickets)}</dd>
      </dl>
      <BillingAccountCard schoolId={school.tenant_id} />
    </div>
  );
}

/** Billing account (docs/16 §5.8): read with subscriptions/invoices read; edit is ᴿ. */
function BillingAccountCard({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.billingAccount");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const can = useCan();
  const readable = can("platform.subscriptions.read") || can("platform.invoices.read");
  const key = [...PK.tenant(schoolId), "billing-account"];
  const account = useApiQuery(
    key,
    async () => {
      try {
        return await unwrap(
          api.GET("/api/v1/platform/tenants/{tenant_id}/billing-account", {
            params: { path: { tenant_id: schoolId } },
          }),
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    { enabled: readable },
  );
  if (!readable) return null;
  const data = account.status === "ready" ? account.data : null;
  const schema = billingAccountSchema();
  return (
    <Card
      title={t("title")}
      headingLevel={3}
      actions={
        can("platform.subscriptions.manage") && account.status === "ready" ? (
          <ActionDialog
            triggerLabel={tc("edit")}
            triggerSize="sm"
            title={t("editTitle")}
            description={t("editBody")}
            confirmLabel={tc("save")}
            stepUp
            schema={schema}
            invalidate={[key]}
            submit={(input) =>
              unwrap(
                api.PUT("/api/v1/platform/tenants/{tenant_id}/billing-account", {
                  params: {
                    path: { tenant_id: schoolId },
                    ...(data ? { header: { "If-Match": ifMatch(data.version) } } : {}),
                  },
                  body: input,
                }),
              )
            }
          >
            {(errors) => <BillingAccountFields errors={errors} initial={data} />}
          </ActionDialog>
        ) : undefined
      }
    >
      {account.status === "loading" ? <LoadingState label={tc("loading")} /> : null}
      {account.status === "error" ? <Alert tone="danger">{tc("loadErrorBody")}</Alert> : null}
      {account.status === "ready" && data === null ? (
        <p className="text-sm text-ink-muted">{t("none")}</p>
      ) : null}
      {data ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="text-ink-muted">{t("legalName")}</dt>
          <dd>{data.legal_name}</dd>
          <dt className="text-ink-muted">{t("gstin")}</dt>
          <dd>
            <Value>{data.gstin}</Value>
          </dd>
          <dt className="text-ink-muted">{t("stateCode")}</dt>
          <dd>{data.state_code}</dd>
          <dt className="text-ink-muted">{t("billingEmail")}</dt>
          <dd>{data.billing_email}</dd>
          <dt className="text-ink-muted">{t("address")}</dt>
          <dd>
            {[data.address_line1, data.address_line2, data.city, data.district, data.postal_code]
              .filter(Boolean)
              .join(", ")}
          </dd>
        </dl>
      ) : null}
    </Card>
  );
}

function SubscriptionTab({ school }: { school: TenantDetail }) {
  const t = useTranslations("platform.subscriptions");
  const tsub = useTranslations("status.subscription");
  const locale = useLocale();
  const { nameOf } = usePlanDirectory();
  const sub = school.subscription;
  if (!sub) return <p className="text-sm text-ink-muted">{t("none")}</p>;
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="text-ink-muted">{t("colPlan")}</dt>
        <dd>{nameOf(sub.plan_id)}</dd>
        <dt className="text-ink-muted">{t("colStatus")}</dt>
        <dd>
          <Badge tone={subscriptionTone[sub.status]}>{tsub(sub.status)}</Badge>
          {sub.cancel_at_period_end ? ` · ${t("cancelsAtPeriodEnd")}` : ""}
        </dd>
        <dt className="text-ink-muted">{t("colPeriod")}</dt>
        <dd>
          {formatDate(sub.current_period_start)} – {formatDate(sub.current_period_end)}
        </dd>
        <dt className="text-ink-muted">{t("colTrialEnds")}</dt>
        <dd>
          <Value>{formatDate(sub.trial_ends_at)}</Value>
        </dd>
        <dt className="text-ink-muted">{t("pendingPlan")}</dt>
        <dd>
          <Value>{sub.pending_plan_id ? nameOf(sub.pending_plan_id) : null}</Value>
        </dd>
        <dt className="text-ink-muted">{t("priceOverride")}</dt>
        <dd>
          <Value>{formatInr(sub.price_override_inr, locale)}</Value>
        </dd>
      </dl>
      <SubscriptionActions subscription={sub} label={school.school_name} />
    </div>
  );
}

function UsageTab({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.usage");
  const api = useBffClient("operator");
  const usage = useApiQuery([...PK.usage, schoolId], () =>
    unwrap(
      api.GET("/api/v1/platform/tenants/{tenant_id}/usage", {
        params: { path: { tenant_id: schoolId } },
      }),
    ),
  );
  return <UsageTable usage={usage} caption={t("title")} />;
}

function DeploymentTab({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.fleet");
  const tdep = useTranslations("status.deployment");
  const tmode = useTranslations("deploymentMode");
  const api = useBffClient("operator");
  const deployments = useApiQuery(
    [...PK.deployments, "list", {}],
    async () => (await unwrap(api.GET("/api/v1/platform/deployments"))).data,
  );
  if (deployments.status !== "ready") {
    return <FleetTableState state={deployments} />;
  }
  const deployment = deployments.data.find((row) => row.tenant_id === schoolId);
  if (!deployment) return <p className="text-sm text-ink-muted">{t("emptyBody")}</p>;
  const status = known(deploymentTone, deployment.status);
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="text-ink-muted">{t("colStatus")}</dt>
        <dd>
          {status ? (
            <Badge tone={deploymentTone[status]}>{tdep(status)}</Badge>
          ) : (
            <Badge>{deployment.status}</Badge>
          )}
        </dd>
        <dt className="text-ink-muted">{t("colMode")}</dt>
        <dd>{tmode(deployment.mode)}</dd>
        <dt className="text-ink-muted">{t("colRegion")}</dt>
        <dd>
          {deployment.region} · {t("backupRegion", { region: deployment.backup_region })}
        </dd>
        <dt className="text-ink-muted">{t("colHost")}</dt>
        <dd>
          <Value>{deployment.hostname ?? deployment.host_ref}</Value>
        </dd>
        <dt className="text-ink-muted">{t("colDomain")}</dt>
        <dd>
          <Value>{deployment.custom_domain}</Value>
        </dd>
        <dt className="text-ink-muted">{t("colVersion")}</dt>
        <dd>
          <Value>{deployment.app_version}</Value>
          {deployment.target_version ? ` → ${deployment.target_version}` : ""}
        </dd>
        <dt className="text-ink-muted">{t("colHeartbeat")}</dt>
        <dd>
          <Value>{formatDateTime(deployment.last_heartbeat_at)}</Value>
        </dd>
      </dl>
      <DeploymentActions deployment={deployment} />
    </div>
  );
}

function FleetTableState({ state }: { state: Loadable<unknown> }) {
  const tc = useTranslations("common");
  if (state.status === "loading") return <LoadingState label={tc("loading")} />;
  return (
    <Alert tone="danger" title={tc("loadErrorTitle")}>
      {tc("loadErrorBody")}
    </Alert>
  );
}

/** Effective flags for this school: its override wins, else the global value (§5.11). */
function FlagsTab({ school }: { school: TenantDetail }) {
  const t = useTranslations("platform.flags");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const can = useCan();
  const flags = useApiQuery(
    [...PK.flags, "list"],
    async () => (await unwrap(api.GET("/api/v1/platform/flags"))).data,
  );
  const manage = can("platform.flags.manage");
  const invalidate = [PK.flags, PK.tenant(school.tenant_id)] as const;
  const globals: Loadable<readonly FeatureFlag[]> =
    flags.status === "ready" ? ready(flags.data.filter((flag) => flag.tenant_id === null)) : flags;
  const columns: Column<FeatureFlag>[] = [
    {
      key: "key",
      header: t("colKey"),
      cell: (row) => <code className="font-mono text-xs">{row.key}</code>,
    },
    {
      key: "global",
      header: t("colGlobal"),
      cell: (row) =>
        row.enabled
          ? row.rollout_percent !== null && row.rollout_percent < 100
            ? t("rolloutValue", { percent: row.rollout_percent })
            : tc("yes")
          : tc("no"),
    },
    {
      key: "override",
      header: t("colSchoolOverride"),
      cell: (row) => {
        const value = school.flag_overrides[row.key];
        return value === undefined ? (
          <span className="text-ink-muted">{t("noOverride")}</span>
        ) : (
          <Badge tone={value ? "success" : "neutral"}>
            {value ? t("forcedOn") : t("forcedOff")}
          </Badge>
        );
      },
    },
    ...(manage
      ? [
          {
            key: "actions",
            header: tc("actions"),
            cell: (row: FeatureFlag) => {
              const path = { key: row.key, tenant_id: school.tenant_id };
              const has = school.flag_overrides[row.key] !== undefined;
              return (
                <div className="flex flex-wrap gap-2">
                  <ActionDialog
                    triggerLabel={t("setOverride")}
                    triggerSize="sm"
                    triggerDescription={row.key}
                    title={t("setOverrideTitle", { key: row.key })}
                    confirmLabel={tc("save")}
                    stepUp
                    schema={z.object({ enabled: z.enum(["on", "off"]) })}
                    invalidate={invalidate}
                    submit={(input) =>
                      unwrap(
                        api.PUT("/api/v1/platform/flags/{key}/tenants/{tenant_id}", {
                          params: { path },
                          body: { enabled: input.enabled === "on" },
                        }),
                      )
                    }
                  >
                    {() => (
                      <fieldset className="space-y-2">
                        <legend className="text-sm font-semibold">{t("overrideValue")}</legend>
                        {(["on", "off"] as const).map((value) => (
                          <label key={value} className="flex items-center gap-2 text-sm">
                            <input
                              type="radio"
                              name="enabled"
                              value={value}
                              defaultChecked={
                                (school.flag_overrides[row.key] ?? row.enabled) === (value === "on")
                              }
                              className="size-4 accent-primary"
                            />
                            {value === "on" ? t("forcedOn") : t("forcedOff")}
                          </label>
                        ))}
                      </fieldset>
                    )}
                  </ActionDialog>
                  {has ? (
                    <ActionDialog
                      triggerLabel={t("clearOverride")}
                      triggerSize="sm"
                      triggerVariant="ghost"
                      triggerDescription={row.key}
                      title={t("clearOverrideTitle", { key: row.key })}
                      confirmLabel={t("clearOverride")}
                      stepUp
                      schema={z.object({})}
                      invalidate={invalidate}
                      submit={() =>
                        unwrap(
                          api.DELETE("/api/v1/platform/flags/{key}/tenants/{tenant_id}", {
                            params: { path },
                          }),
                        )
                      }
                    />
                  ) : null}
                </div>
              );
            },
          },
        ]
      : []),
  ];
  return (
    <DataTable
      caption={t("title")}
      captionHidden
      columns={columns}
      state={globals}
      rowKey={(row) => row.key}
      emptyTitle={t("emptyTitle")}
      emptyBody={t("emptyBody")}
    />
  );
}

function TicketsTab({ schoolId }: { schoolId: string }) {
  const t = useTranslations("platform.support");
  const api = useBffClient("operator");
  const query = { tenant_id: schoolId, limit: 100 };
  const tickets = useApiQuery(
    [...PK.tickets, "list", query],
    async () =>
      (await unwrap(api.GET("/api/v1/platform/support/tickets", { params: { query } }))).data,
  );
  return <TicketTable tickets={tickets} caption={t("title")} />;
}

import type {
  BreakGlassRequest,
  Deployment,
  FeatureFlag,
  FleetVersion,
  PlatformAuditEvent,
  SupportTicket,
  UsageDaily,
} from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { breakGlassTone, deploymentTone, priorityTone, ticketTone } from "@/features/status";
import { formatBytes, formatCount, formatDate, formatDateTime, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/** FR-PLT-020..021: daily aggregates only (counts, bytes, tokens, cost). */
export function UsageView({ usage }: { usage: Loadable<readonly UsageDaily[]> }) {
  const t = useTranslations("platform.usage");
  const locale = useLocale();
  const n = (value: number) => <Value>{formatCount(value, locale)}</Value>;
  const columns: Column<UsageDaily>[] = [
    { key: "school", header: t("colSchool"), cell: (row) => row.school_name },
    { key: "date", header: t("colDate"), cell: (row) => <Value>{formatDate(row.date)}</Value> },
    {
      key: "users",
      header: t("colActiveUsers"),
      className: "text-right tabular-nums",
      cell: (row) => n(row.active_users),
    },
    {
      key: "students",
      header: t("colStudents"),
      className: "text-right tabular-nums",
      cell: (row) => n(row.students),
    },
    {
      key: "storage",
      header: t("colStorage"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatBytes(row.storage_bytes, locale)}</Value>,
    },
    {
      key: "docs",
      header: t("colDocuments"),
      className: "text-right tabular-nums",
      cell: (row) => n(row.documents),
    },
    {
      key: "tokens",
      header: t("colAiTokens"),
      className: "text-right tabular-nums",
      cell: (row) => n(row.ai_tokens),
    },
    {
      key: "cost",
      header: t("colAiCost"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatInr(row.ai_cost_inr, locale)}</Value>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={usage}
        rowKey={(row) => `${row.tenant_id}:${row.date}`}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}

/** FR-PLT-022: global and per-tenant flags with percentage rollout. */
export function FlagsView({ flags }: { flags: Loadable<readonly FeatureFlag[]> }) {
  const t = useTranslations("platform.flags");
  const tc = useTranslations("common");
  const locale = useLocale();
  const columns: Column<FeatureFlag>[] = [
    {
      key: "key",
      header: t("colKey"),
      cell: (row) => <code className="font-mono text-xs">{row.key}</code>,
    },
    { key: "description", header: t("colDescription"), cell: (row) => row.description },
    {
      key: "global",
      header: t("colGlobal"),
      cell: (row) => (row.enabled_globally ? <Badge tone="success">{tc("yes")}</Badge> : tc("no")),
    },
    {
      key: "rollout",
      header: t("colRollout"),
      className: "text-right tabular-nums",
      cell: (row) => t("rolloutValue", { percent: row.rollout_percent }),
    },
    {
      key: "overrides",
      header: t("colOverrides"),
      className: "text-right tabular-nums",
      cell: (row) => <Value>{formatCount(row.tenant_override_count, locale)}</Value>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<Button disabled>{t("newFlag")}</Button>}
      />
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={flags}
        rowKey={(row) => row.key}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}

/** FR-PLT-023..025: deployments, versions and last heartbeat (no personal data). */
export function FleetView({
  deployments,
  versions,
}: {
  deployments: Loadable<readonly Deployment[]>;
  versions: Loadable<readonly FleetVersion[]>;
}) {
  const t = useTranslations("platform.fleet");
  const tstatus = useTranslations("status.deployment");
  const tmode = useTranslations("deploymentMode");
  const locale = useLocale();
  const columns: Column<Deployment>[] = [
    { key: "school", header: t("colSchool"), cell: (row) => row.school_name },
    { key: "mode", header: t("colMode"), cell: (row) => tmode(row.mode) },
    { key: "region", header: t("colRegion"), cell: (row) => row.region },
    { key: "host", header: t("colHost"), cell: (row) => <Value>{row.host}</Value> },
    { key: "domain", header: t("colDomain"), cell: (row) => <Value>{row.custom_domain}</Value> },
    { key: "version", header: t("colVersion"), cell: (row) => <Value>{row.version}</Value> },
    {
      key: "heartbeat",
      header: t("colHeartbeat"),
      cell: (row) => <Value>{formatDateTime(row.last_heartbeat_at)}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={deploymentTone[row.status]}>{tstatus(row.status)}</Badge>,
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

/** FR-PLT-027: support tickets, with the "no student data" warning always visible. */
export function SupportView({ tickets }: { tickets: Loadable<readonly SupportTicket[]> }) {
  const t = useTranslations("platform.support");
  const tstatus = useTranslations("status.ticket");
  const tprio = useTranslations("status.priority");
  const columns: Column<SupportTicket>[] = [
    { key: "number", header: t("colNumber"), cell: (row) => row.number },
    { key: "school", header: t("colSchool"), cell: (row) => row.school_name },
    { key: "subject", header: t("colSubject"), cell: (row) => row.subject },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={ticketTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
    {
      key: "priority",
      header: t("colPriority"),
      cell: (row) => <Badge tone={priorityTone[row.priority]}>{tprio(row.priority)}</Badge>,
    },
    {
      key: "sla",
      header: t("colSla"),
      cell: (row) => <Value>{formatDateTime(row.sla_due_at)}</Value>,
    },
    {
      key: "updated",
      header: t("colUpdated"),
      cell: (row) => <Value>{formatDateTime(row.updated_at)}</Value>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <Alert tone="warning" title={t("piiWarningTitle")}>
        {t("piiWarningBody")}
      </Alert>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={tickets}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}

/** Break-glass requests (07 §6.4): status only here; approval happens in the school. */
export function BreakGlassView({ requests }: { requests: Loadable<readonly BreakGlassRequest[]> }) {
  const t = useTranslations("platform.breakGlass");
  const tstatus = useTranslations("status.breakGlass");
  const columns: Column<BreakGlassRequest>[] = [
    { key: "school", header: t("colSchool"), cell: (row) => row.school_name },
    { key: "by", header: t("colRequestedBy"), cell: (row) => row.requested_by },
    { key: "reason", header: t("colReason"), cell: (row) => row.reason },
    {
      key: "duration",
      header: t("colDuration"),
      cell: (row) => t("durationHours", { hours: row.duration_hours }),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={breakGlassTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
    {
      key: "expires",
      header: t("colExpires"),
      cell: (row) => <Value>{formatDateTime(row.expires_at)}</Value>,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
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

/** FR-PLT-029: hash-chained platform audit log with chain verification. */
export function PlatformAuditView({ events }: { events: Loadable<readonly PlatformAuditEvent[]> }) {
  const t = useTranslations("platform.audit");
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
      cell: (row) => <Value>{row.operator_name}</Value>,
    },
    {
      key: "action",
      header: t("colAction"),
      cell: (row) => <code className="font-mono text-xs">{row.action}</code>,
    },
    {
      key: "resource",
      header: t("colResource"),
      cell: (row) => `${row.resource_type}${row.resource_id ? ` · ${row.resource_id}` : ""}`,
    },
    { key: "summary", header: t("colSummary"), cell: (row) => row.summary },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button variant="secondary" disabled>
            {t("verify")}
          </Button>
        }
      />
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

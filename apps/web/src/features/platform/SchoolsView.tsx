import { TENANT_STATUSES, type TenantSummary } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { deploymentTone, known, schoolTone, subscriptionTone } from "@/features/status";
import { Link } from "@/i18n/navigation";
import { formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

export interface SchoolFilters {
  q?: string;
  status?: string;
  tier?: string;
  trialEnding?: boolean;
  pastDue?: boolean;
}

/** FR-PLT-001..005 schools list (docs/16 §5.2). Tenant metadata only, never student data. */
export function SchoolsView({
  schools,
  filters = {},
  canProvision = true,
}: {
  schools: Loadable<readonly TenantSummary[]>;
  filters?: SchoolFilters;
  canProvision?: boolean;
}) {
  const t = useTranslations("platform.schools");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.school");
  const tsub = useTranslations("status.subscription");
  const tdep = useTranslations("status.deployment");
  const tmode = useTranslations("deploymentMode");

  const columns: Column<TenantSummary>[] = [
    {
      key: "name",
      header: t("colName"),
      cell: (row) => (
        <Link
          href={`/platform/schools/${row.tenant_id}`}
          className="font-semibold text-primary underline"
        >
          {row.school_name}
        </Link>
      ),
    },
    {
      key: "code",
      header: t("colCode"),
      cell: (row) => <code className="font-mono text-xs">{row.code}</code>,
    },
    { key: "tier", header: t("colDeployment"), cell: (row) => tmode(row.tier) },
    { key: "plan", header: t("colPlan"), cell: (row) => <Value>{row.plan_code}</Value> },
    {
      key: "subscription",
      header: t("colSubscription"),
      cell: (row) =>
        row.subscription_status ? (
          <Badge tone={subscriptionTone[row.subscription_status]}>
            {tsub(row.subscription_status)}
          </Badge>
        ) : (
          <Value>{null}</Value>
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <Badge tone={schoolTone[row.tenant_status]}>{tstatus(row.tenant_status)}</Badge>
      ),
    },
    {
      key: "deployment",
      header: t("colDeploymentStatus"),
      cell: (row) => {
        const status = known(deploymentTone, row.deployment_status);
        return status ? (
          <Badge tone={deploymentTone[status]}>{tdep(status)}</Badge>
        ) : (
          <Badge>{row.deployment_status}</Badge>
        );
      },
    },
    { key: "version", header: t("colVersion"), cell: (row) => <Value>{row.app_version}</Value> },
    {
      key: "heartbeat",
      header: t("colHeartbeat"),
      cell: (row) => <Value>{formatDateTime(row.last_heartbeat_at)}</Value>,
    },
  ];

  const provision = canProvision ? (
    <ButtonLink href="/platform/provision">{t("provision")}</ButtonLink>
  ) : undefined;

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} actions={provision} />
      <form method="get" role="search" className="flex flex-wrap items-end gap-3">
        <TextField
          name="q"
          type="search"
          label={t("searchLabel")}
          hint={t("searchHint")}
          defaultValue={filters.q ?? ""}
          autoComplete="off"
          maxLength={100}
          className="w-full max-w-sm"
        />
        <SelectField
          name="status"
          label={t("filterStatus")}
          placeholder={tc("all")}
          defaultValue={filters.status ?? ""}
          options={TENANT_STATUSES.map((value) => ({ value, label: tstatus(value) }))}
          className="w-44"
        />
        <SelectField
          name="tier"
          label={t("filterTier")}
          placeholder={tc("all")}
          defaultValue={filters.tier ?? ""}
          options={(["shared", "dedicated"] as const).map((value) => ({
            value,
            label: tmode(value),
          }))}
          className="w-44"
        />
        <fieldset className="flex flex-wrap gap-4 pb-2">
          <legend className="sr-only">{t("filterMore")}</legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              name="trial_ending"
              value="true"
              defaultChecked={filters.trialEnding ?? false}
              className="size-4 accent-primary"
            />
            {t("filterTrialEnding")}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              name="past_due"
              value="true"
              defaultChecked={filters.pastDue ?? false}
              className="size-4 accent-primary"
            />
            {t("filterPastDue")}
          </label>
        </fieldset>
        <Button type="submit" variant="secondary">
          {tc("search")}
        </Button>
      </form>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={schools}
        rowKey={(row) => row.tenant_id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        emptyAction={provision}
      />
    </div>
  );
}

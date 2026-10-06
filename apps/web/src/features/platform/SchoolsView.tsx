import { TENANT_STATUSES, type TenantSummary } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { SearchInput } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { deploymentTone, known, subscriptionTone } from "@/features/status";
import { Link } from "@/i18n/navigation";
import type { Loadable } from "@/lib/loadable";
import { MonoTime, SchoolStatusPill, TierTag } from "./pills";

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
  const tn = useTranslations("platform.nav");
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
        <span className="flex min-w-48 flex-col gap-0.5">
          <Link
            href={`/platform/schools/${row.tenant_id}`}
            className="font-semibold text-primary underline-offset-4 hover:underline"
          >
            {row.school_name}
          </Link>
          <span className="text-xs text-ink-muted">
            <span className="sr-only">{t("colCode")}: </span>
            <code className="font-mono">{row.code}</code>
          </span>
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <SchoolStatusPill status={row.tenant_status} />,
    },
    { key: "tier", header: t("colDeployment"), cell: (row) => <TierTag tier={row.tier} /> },
    {
      key: "plan",
      header: t("colPlan"),
      cell: (row) => (
        <span className="font-mono text-xs">
          <Value>{row.plan_code}</Value>
        </span>
      ),
    },
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
    {
      key: "version",
      header: t("colVersion"),
      cell: (row) => (
        <span className="font-mono text-xs">
          <Value>{row.app_version}</Value>
        </span>
      ),
    },
    {
      key: "heartbeat",
      header: t("colHeartbeat"),
      cell: (row) => <MonoTime value={row.last_heartbeat_at} />,
    },
  ];

  const provision = canProvision ? (
    <ButtonLink href="/platform/provision">
      <Icon name="plus" className="size-4" />
      {t("provision")}
    </ButtonLink>
  ) : undefined;

  const filtered = Boolean(
    filters.q || filters.status || filters.tier || filters.trialEnding || filters.pastDue,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("dashboard"), href: "/platform" }, { label: t("title") }]}
        actions={provision}
      />
      <Card padding="sm">
        <form method="get" role="search" className="flex flex-wrap items-end gap-3">
          <SearchInput
            wrapperClassName="min-w-64 flex-1"
            name="q"
            label={t("searchLabel")}
            labelVisible
            placeholder={t("searchHint")}
            defaultValue={filters.q ?? ""}
            autoComplete="off"
            maxLength={100}
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
          <fieldset className="flex min-h-10 flex-wrap items-center gap-4">
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
          <div className="relative flex flex-wrap gap-2">
            <Button type="submit" variant="secondary">
              <Icon name="filter" className="size-4" />
              {tc("search")}
            </Button>
            {filtered ? (
              <ButtonLink href="/platform/schools" variant="ghost">
                {t("clearFilters")}
              </ButtonLink>
            ) : null}
          </div>
        </form>
      </Card>
      <DataTable
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={schools}
        rowKey={(row) => row.tenant_id}
        emptyTitle={filtered ? t("noMatchTitle") : t("emptyTitle")}
        emptyBody={filtered ? t("noMatchBody") : t("emptyBody")}
        emptyAction={filtered ? undefined : provision}
      />
    </div>
  );
}

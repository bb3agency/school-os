import type { TenantSummary } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { schoolTone } from "@/features/status";
import { Link } from "@/i18n/navigation";
import { formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/** FR-PLT-001..005: schools list. Shows tenant metadata only, never student data. */
export function SchoolsView({
  schools,
  query = "",
}: {
  schools: Loadable<readonly TenantSummary[]>;
  query?: string;
}) {
  const t = useTranslations("platform.schools");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.school");
  const tmode = useTranslations("deploymentMode");

  const columns: Column<TenantSummary>[] = [
    {
      key: "name",
      header: t("colName"),
      cell: (row) => (
        <Link href={`/platform/schools/${row.id}`} className="font-semibold text-primary underline">
          {row.name}
        </Link>
      ),
    },
    { key: "plan", header: t("colPlan"), cell: (row) => <Value>{row.plan_name}</Value> },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={schoolTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
    { key: "mode", header: t("colDeployment"), cell: (row) => tmode(row.deployment_mode) },
    { key: "region", header: t("colRegion"), cell: (row) => row.region },
    {
      key: "created",
      header: t("colCreated"),
      cell: (row) => <Value>{formatDate(row.created_at)}</Value>,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<ButtonLink href="/platform/provision">{t("provision")}</ButtonLink>}
      />
      <form method="get" role="search" className="flex flex-wrap items-end gap-3">
        <TextField
          name="q"
          type="search"
          label={t("searchLabel")}
          hint={t("searchHint")}
          defaultValue={query}
          autoComplete="off"
          className="w-full max-w-md"
        />
        <Button type="submit" variant="secondary">
          {tc("search")}
        </Button>
      </form>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={schools}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        emptyAction={<ButtonLink href="/platform/provision">{t("provision")}</ButtonLink>}
      />
    </div>
  );
}

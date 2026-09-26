import { TENANT_ROLES, type TenantRoleKey, type TenantUser } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { personTone } from "@/features/status";
import { formatDateTime, formatList } from "@/lib/format";
import { ready, type Loadable } from "@/lib/loadable";

/** US-102 / FR-IAM-010..014: staff, roles and scopes. */
export function UsersView({ users }: { users: Loadable<readonly TenantUser[]> }) {
  const t = useTranslations("school.users");
  const ts = useTranslations("status.person");
  const locale = useLocale();

  const userColumns: Column<TenantUser>[] = [
    { key: "name", header: t("colName"), cell: (row) => row.display_name },
    { key: "login", header: t("colLogin"), cell: (row) => row.login },
    {
      key: "roles",
      header: t("colRoles"),
      cell: (row) =>
        formatList(
          row.roles.map((role) => t(`roles.${role}`)),
          locale,
        ),
    },
    { key: "scope", header: t("colScope"), cell: (row) => row.scope_summary ?? t("scopeSchool") },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={personTone[row.status]}>{ts(row.status)}</Badge>,
    },
    {
      key: "last",
      header: t("colLastSignIn"),
      cell: (row) => <Value>{formatDateTime(row.last_sign_in_at)}</Value>,
    },
  ];

  const roleColumns: Column<TenantRoleKey>[] = [
    { key: "name", header: t("colRole"), cell: (role) => t(`roles.${role}`) },
    {
      key: "key",
      header: t("colRoleKey"),
      cell: (role) => <code className="font-mono text-xs">{role}</code>,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<Button disabled>{t("invite")}</Button>}
      />
      <DataTable
        caption={t("title")}
        captionHidden
        columns={userColumns}
        state={users}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
      <Card title={t("rolesTitle")} description={t("rolesDescription")}>
        <DataTable
          caption={t("rolesTitle")}
          captionHidden
          columns={roleColumns}
          state={ready(TENANT_ROLES)}
          rowKey={(role) => role}
          emptyTitle={t("emptyTitle")}
        />
      </Card>
    </div>
  );
}

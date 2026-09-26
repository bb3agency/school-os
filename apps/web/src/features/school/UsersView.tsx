import {
  TENANT_ROLES,
  type Scope,
  type TenantRoleKey,
  type TenantUser,
} from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { memberTone } from "@/features/status";
import type { Locale } from "@/i18n/routing";
import { formatDateTime, formatList } from "@/lib/format";
import { ready, type Loadable } from "@/lib/loadable";

const SYSTEM_ROLES: readonly string[] = TENANT_ROLES;

function isSystemRole(role: string): role is TenantRoleKey {
  return SYSTEM_ROLES.includes(role);
}

/** US-102 / FR-IAM-010..014: staff, roles and scopes. */
export function UsersView({ users }: { users: Loadable<readonly TenantUser[]> }) {
  const t = useTranslations("school.users");
  const ts = useTranslations("status.member");
  const locale = useLocale() as Locale;

  function scopeSummary(scopes: readonly Scope[]): string {
    if (scopes.length === 0 || scopes.some((scope) => scope.type === "school")) {
      return t("scopeSchool");
    }
    const classes = scopes.filter((scope) => scope.type === "class").length;
    const sections = scopes.filter((scope) => scope.type === "section").length;
    const parts = [
      ...(classes > 0 ? [t("scopeClasses", { count: classes })] : []),
      ...(sections > 0 ? [t("scopeSections", { count: sections })] : []),
    ];
    return formatList(parts, locale);
  }

  const userColumns: Column<TenantUser>[] = [
    { key: "name", header: t("colName"), cell: (row) => row.display_name },
    { key: "email", header: t("colEmail"), cell: (row) => <Value>{row.email}</Value> },
    {
      key: "roles",
      header: t("colRoles"),
      cell: (row) =>
        formatList(
          row.roles.map((role) => (isSystemRole(role) ? t(`roles.${role}`) : role)),
          locale,
        ),
    },
    { key: "scope", header: t("colScope"), cell: (row) => scopeSummary(row.scopes) },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={memberTone[row.status]}>{ts(row.status)}</Badge>,
    },
    {
      key: "last",
      header: t("colLastSignIn"),
      cell: (row) => <Value>{formatDateTime(row.last_login_at)}</Value>,
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

"use client";

import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDateTime, formatList } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { useRoles, useUserList } from "./data";
import { useRoleLabel, UserStatusBadge, useScopeSummary } from "./parts";
import { USER_PERM, type StaffRole, type StaffUser } from "./types";

/**
 * Users and roles (US-102, FR-IAM-010..014): the school's staff with their roles, classes and
 * status; "Invite user" for `user.manage` holders. Each name opens the person's page (the URL
 * holds only their ID). The list waits for /me; when /me cannot be read it is still asked for:
 * the API decides and the screen explains its answer.
 */
export function UsersScreen() {
  const t = useTranslations("school.users");
  const locale = useLocale() as Locale;
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const pages = useCursorStack();
  // /me unreadable: ask the API anyway (it decides and the table explains its answer).
  const allowed = me.isError || (me.data?.permissions.includes(USER_PERM.manage) ?? false);
  const list = useUserList(pages.cursor, allowed);
  const roles = useRoles(allowed && can(USER_PERM.manage));
  const roleLabel = useRoleLabel(roles.data);
  const scopeSummary = useScopeSummary();

  if (me.data && !allowed) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="warning" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }

  const rows: Loadable<readonly StaffUser[]> =
    list.status === "ready" ? { status: "ready", data: list.data.data } : list;
  const next = list.status === "ready" ? list.data.next_cursor : null;

  const columns: Column<StaffUser>[] = [
    {
      key: "name",
      header: t("colName"),
      cell: (row) => (
        <Link href={`/settings/users/${row.id}`} className="font-semibold text-primary underline">
          {row.display_name}
        </Link>
      ),
    },
    { key: "email", header: t("colEmail"), cell: (row) => <Value>{row.email}</Value> },
    {
      key: "roles",
      header: t("colRoles"),
      cell: (row) =>
        row.roles.length > 0 ? formatList(row.roles.map(roleLabel), locale) : t("noRoles"),
    },
    { key: "scope", header: t("colScope"), cell: (row) => scopeSummary(row.scopes) },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <UserStatusBadge status={row.status} />,
    },
    {
      key: "last",
      header: t("colLastSignIn"),
      cell: (row) => <Value>{formatDateTime(row.last_login_at)}</Value>,
    },
  ];

  const roleColumns: Column<StaffRole>[] = [
    { key: "name", header: t("colRole"), cell: (role) => roleLabel(role.key) },
    {
      key: "kind",
      header: t("colRoleKind"),
      cell: (role) => (role.is_system ? t("roleBuiltIn") : t("roleSchool")),
    },
    {
      key: "key",
      header: t("colRoleKey"),
      cell: (role) => <code className="font-mono text-xs">{role.key}</code>,
    },
  ];
  const roleRows: Loadable<readonly StaffRole[]> = roles.data
    ? { status: "ready", data: roles.data }
    : roles.isError
      ? { status: "error" }
      : { status: "loading" };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          can(USER_PERM.manage) ? (
            <ButtonLink href="/settings/users/new">{t("invite")}</ButtonLink>
          ) : null
        }
      />
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={rows}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
      <Pager
        label={t("pagesLabel")}
        page={pages.page}
        onPrevious={pages.hasPrevious ? pages.previous : undefined}
        onNext={next ? () => pages.next(next) : undefined}
      />
      {roles.fetchStatus !== "idle" || roles.data ? (
        <Card title={t("rolesTitle")} description={t("rolesDescription")}>
          <DataTable
            caption={t("rolesTitle")}
            captionHidden
            columns={roleColumns}
            state={roleRows}
            rowKey={(role) => role.key}
            emptyTitle={t("rolesEmpty")}
          />
        </Card>
      ) : null}
    </div>
  );
}

import { PLATFORM_ROLES, type Operator, type PlatformRole } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { personTone } from "@/features/status";
import { formatDateTime, formatList } from "@/lib/format";
import { ready, type Loadable } from "@/lib/loadable";
import { InviteOperatorForm } from "./InviteOperatorForm";

/** FR-PLT-028: operators, platform roles and MFA status. */
export function OperatorsView({ operators }: { operators: Loadable<readonly Operator[]> }) {
  const t = useTranslations("platform.operators");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.person");
  const locale = useLocale();

  const columns: Column<Operator>[] = [
    { key: "name", header: t("colName"), cell: (row) => row.display_name },
    { key: "email", header: t("colEmail"), cell: (row) => row.email },
    {
      key: "roles",
      header: t("colRoles"),
      cell: (row) =>
        formatList(
          row.roles.map((role) => t(`roles.${role}`)),
          locale,
        ),
    },
    {
      key: "mfa",
      header: t("colMfa"),
      cell: (row) =>
        row.mfa_enrolled ? (
          <Badge tone="success">{t("mfaOn")}</Badge>
        ) : (
          <Badge tone="danger">{t("mfaOff")}</Badge>
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <Badge tone={personTone[row.status]}>{tstatus(row.status)}</Badge>,
    },
    {
      key: "last",
      header: t("colLastSignIn"),
      cell: (row) => <Value>{formatDateTime(row.last_sign_in_at)}</Value>,
    },
  ];

  const roleColumns: Column<PlatformRole>[] = [
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
        actions={
          <Dialog
            title={t("inviteDialogTitle")}
            triggerLabel={t("invite")}
            closeLabel={tc("close")}
          >
            <InviteOperatorForm />
          </Dialog>
        }
      />
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={operators}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
      <Card title={t("rolesTitle")}>
        <DataTable
          caption={t("rolesTitle")}
          captionHidden
          columns={roleColumns}
          state={ready(PLATFORM_ROLES)}
          rowKey={(role) => role}
          emptyTitle={t("emptyTitle")}
        />
      </Card>
    </div>
  );
}

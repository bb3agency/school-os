"use client";

import { PLATFORM_ROLES, type Operator, type PlatformRole } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { personTone } from "@/features/status";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatDateTime, formatList } from "@/lib/format";
import { formList } from "@/lib/forms";
import { ready } from "@/lib/loadable";
import { email, text } from "@/lib/validation";
import { PK, useOperatorMe } from "./data";

const roles = z.array(z.enum(PLATFORM_ROLES)).min(1, { error: "chooseRole" });

const inviteSchema = z.object({
  display_name: text(200),
  email,
  idp_subject: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .max(255, { error: "tooLong" })
    .refine((value) => !/\s/.test(value), { error: "noSpaces" }),
  roles,
});

const rolesSchema = z.object({ roles });
const extraRoles = (form: HTMLFormElement) => ({ roles: formList(form, "roles") });

function RoleCheckboxes({
  error,
  selected = [],
}: {
  error?: string | undefined;
  selected?: readonly PlatformRole[];
}) {
  const t = useTranslations("platform.operators");
  return (
    <fieldset className="space-y-2" aria-describedby={error ? "operator-roles-error" : undefined}>
      <legend className="text-sm font-semibold">{t("colRoles")}</legend>
      {PLATFORM_ROLES.map((role) => (
        <div key={role} className="flex items-start gap-2 text-sm">
          <input
            id={`operator-role-${role}`}
            type="checkbox"
            name="roles"
            value={role}
            defaultChecked={selected.includes(role)}
            aria-describedby={`operator-role-${role}-hint`}
            className="mt-1 size-4 accent-primary"
          />
          <div>
            <label htmlFor={`operator-role-${role}`} className="font-semibold">
              {t(`roles.${role}`)}
            </label>
            <p id={`operator-role-${role}-hint`} className="text-ink-muted">
              {t(`roleHints.${role}`)}
            </p>
          </div>
        </div>
      ))}
      {error ? (
        <p id="operator-roles-error" className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

/** FR-PLT-028 (docs/16 §5.16): invite, assign roles, deactivate (all step-up MFA). */
export function OperatorsScreen() {
  const t = useTranslations("platform.operators");
  const tc = useTranslations("common");
  const tstatus = useTranslations("status.person");
  const locale = useLocale();
  const api = useBffClient("operator");
  const me = useOperatorMe();
  const operators = useApiQuery(
    [...PK.operators, "list"],
    async () =>
      (await unwrap(api.GET("/api/v1/platform/operators", { params: { query: { limit: 200 } } })))
        .data,
  );

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
      cell: (row) => <Value>{formatDateTime(row.last_login_at)}</Value>,
    },
    {
      key: "actions",
      header: tc("actions"),
      cell: (row) => {
        const self = me?.operator_id === row.id;
        if (row.status === "deactivated") return null;
        if (self) return <span className="text-sm text-ink-muted">{t("you")}</span>;
        return (
          <div className="flex flex-wrap gap-2">
            <ActionDialog
              triggerLabel={t("changeRoles")}
              triggerSize="sm"
              triggerDescription={row.display_name}
              title={t("changeRolesTitle", { name: row.display_name })}
              description={t("changeRolesBody")}
              confirmLabel={tc("save")}
              stepUp
              schema={rolesSchema}
              extra={extraRoles}
              invalidate={[PK.operators]}
              submit={(data) =>
                unwrap(
                  api.PUT("/api/v1/platform/operators/{operator_id}/roles", {
                    params: { path: { operator_id: row.id } },
                    body: { roles: data.roles },
                  }),
                )
              }
            >
              {(errors) => <RoleCheckboxes error={errors.roles} selected={row.roles} />}
            </ActionDialog>
            <ActionDialog
              triggerLabel={t("deactivate")}
              triggerSize="sm"
              triggerVariant="danger"
              triggerDescription={row.display_name}
              title={t("deactivateTitle", { name: row.display_name })}
              description={t("deactivateBody")}
              confirmLabel={t("deactivate")}
              confirmVariant="danger"
              stepUp
              schema={z.object({})}
              invalidate={[PK.operators]}
              submit={() =>
                unwrap(
                  api.POST("/api/v1/platform/operators/{operator_id}/deactivate", {
                    params: { path: { operator_id: row.id } },
                  }),
                )
              }
            />
          </div>
        );
      },
    },
  ];

  const roleColumns: Column<PlatformRole>[] = [
    { key: "name", header: t("colRole"), cell: (role) => t(`roles.${role}`) },
    { key: "hint", header: t("colRoleHint"), cell: (role) => t(`roleHints.${role}`) },
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
          <ActionDialog
            triggerLabel={t("invite")}
            triggerVariant="primary"
            title={t("inviteDialogTitle")}
            description={t("inviteDialogBody")}
            confirmLabel={t("inviteSend")}
            stepUp
            schema={inviteSchema}
            extra={extraRoles}
            invalidate={[PK.operators]}
            submit={(data, key) =>
              unwrap(
                api.POST("/api/v1/platform/operators", {
                  params: { header: { "Idempotency-Key": key } },
                  body: data,
                }),
              )
            }
          >
            {(errors) => (
              <>
                <TextField
                  name="display_name"
                  label={t("inviteName")}
                  error={errors.display_name}
                  autoComplete="off"
                />
                <TextField
                  name="email"
                  type="email"
                  label={t("inviteEmail")}
                  error={errors.email}
                  autoComplete="off"
                />
                <TextField
                  name="idp_subject"
                  label={t("inviteSubject")}
                  hint={t("inviteSubjectHint")}
                  error={errors.idp_subject}
                  autoComplete="off"
                  spellCheck={false}
                />
                <RoleCheckboxes error={errors.roles} />
              </>
            )}
          </ActionDialog>
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

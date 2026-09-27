"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState, type FormEvent, type ReactNode } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { STAFF_ME_KEY, useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime, formatList } from "@/lib/format";
import { formList, useApiForm } from "@/lib/forms";
import { USER_KEYS, useRoles, useUser } from "./data";
import {
  canGrantRole,
  refineScopes,
  RoleCheckboxes,
  rolesField,
  ScopeEditor,
  scopeExtra,
  scopeFields,
  scopesBody,
  useRoleLabel,
  UserStatusBadge,
  useScopeSummary,
  userFieldMap,
} from "./parts";
import {
  ifMatch,
  isBreakGlass,
  STATUS_ACTIONS,
  USER_PERM,
  type StaffUser,
  type StatusChange,
} from "./types";

const rolesSchema = z.object({ roles: rolesField });
const scopesSchema = z.object(scopeFields).superRefine(refineScopes);

function Item({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="font-semibold text-ink">{children}</dd>
    </div>
  );
}

/** Keys of `school.users.detail.status.<change>.*` per status change. */
const STATUS_COPY: Record<StatusChange, "activate" | "suspend" | "remove"> = {
  active: "activate",
  suspended: "suspend",
  removed: "remove",
};

/**
 * Give access back, suspend or remove (PATCH /users/{id}; `user.manage`, step-up, If-Match).
 * Only the changes the API allows from the current status are offered.
 */
function StatusActions({ user, isSelf }: { user: StaffUser; isSelf: boolean }) {
  const t = useTranslations("school.users.detail.status");
  const api = useBffClient("staff");
  const invalidate = [USER_KEYS.all, ...(isSelf ? [STAFF_ME_KEY] : [])];
  return (
    <>
      {STATUS_ACTIONS[user.status].map((change) => {
        const copy = STATUS_COPY[change];
        const removing = change === "removed";
        return (
          <ActionDialog
            key={change}
            triggerLabel={t(`${copy}.button`)}
            triggerVariant={removing ? "danger" : "secondary"}
            title={t(`${copy}.title`, { name: user.display_name })}
            description={
              user.status === "invited" && removing ? t("remove.invitedBody") : t(`${copy}.body`)
            }
            confirmLabel={t(`${copy}.button`)}
            confirmVariant={removing ? "danger" : "primary"}
            note={isSelf && change !== "active" ? t("selfNote") : undefined}
            stepUp
            schema={z.object({})}
            invalidate={invalidate}
            errorNamespace="school.users"
            submit={() =>
              unwrap(
                api.PATCH("/api/v1/users/{user_id}", {
                  params: { path: { user_id: user.id } },
                  headers: { "If-Match": ifMatch(user.version) },
                  body: { status: change },
                }),
              )
            }
          />
        );
      })}
    </>
  );
}

/**
 * Save button + result line for the inline role and scope forms. The confirmation is
 * announced politely; errors use the shared alert (step-up and school pause are handled by
 * the global prompt and banner).
 */
function SaveRow({
  pending,
  saved,
  error,
  label,
}: {
  pending: boolean;
  saved: boolean;
  error: unknown;
  label: string;
}) {
  const t = useTranslations("school.users.detail");
  const tc = useTranslations("common");
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
      <ApiErrorAlert error={error} namespace="school.users" />
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
          {pending ? tc("working") : label}
        </Button>
        <p role="status" aria-live="polite" className="text-sm font-semibold text-success-ink">
          {saved ? t("saved") : null}
        </p>
      </div>
    </div>
  );
}

/** Replace the person's roles (PUT /users/{id}/roles; `role.assign`, step-up; FR-IAM-014). */
function RolesForm({ user, isSelf }: { user: StaffUser; isSelf: boolean }) {
  const t = useTranslations("school.users.detail");
  const api = useBffClient("staff");
  const me = useStaffMe();
  const roles = useRoles(true);
  const [saved, setSaved] = useState(false);
  const form = useApiForm({
    schema: rolesSchema,
    extra: (element) => ({ roles: formList(element, "roles") }),
    fieldMap: userFieldMap,
    invalidate: [USER_KEYS.all, ...(isSelf ? [STAFF_ME_KEY] : [])],
    submit: (data) =>
      unwrap(
        api.PUT("/api/v1/users/{user_id}/roles", {
          params: { path: { user_id: user.id } },
          body: { roles: [...new Set(data.roles)] },
        }),
      ),
    onSuccess: () => setSaved(true),
  });
  if (roles.isPending) return <p className="text-sm">{t("loadingRoles")}</p>;
  if (!roles.data) return <ApiErrorAlert error={roles.error} namespace="school.users" />;
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    setSaved(false);
    form.onSubmit(event);
  };
  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4" aria-label={t("rolesTitle")}>
      <RoleCheckboxes
        roles={roles.data}
        selected={user.roles}
        grantable={(role) => (me ? canGrantRole(role, me, "assign") : false)}
        error={form.errors.roles}
        legend={t("rolesLegend")}
      />
      <SaveRow pending={form.pending} saved={saved} error={form.error} label={t("saveRoles")} />
    </form>
  );
}

/** Replace the person's classes and sections (PUT /users/{id}/scopes; FR-IAM-012). */
function ScopesForm({ user, isSelf }: { user: StaffUser; isSelf: boolean }) {
  const t = useTranslations("school.users.detail");
  const api = useBffClient("staff");
  const [saved, setSaved] = useState(false);
  const form = useApiForm({
    schema: scopesSchema,
    extra: scopeExtra,
    fieldMap: userFieldMap,
    invalidate: [USER_KEYS.all, ...(isSelf ? [STAFF_ME_KEY] : [])],
    submit: (data) =>
      unwrap(
        api.PUT("/api/v1/users/{user_id}/scopes", {
          params: { path: { user_id: user.id } },
          body: { scopes: scopesBody(data) },
        }),
      ),
    onSuccess: () => setSaved(true),
  });
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    setSaved(false);
    form.onSubmit(event);
  };
  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4" aria-label={t("scopeTitle")}>
      <ScopeEditor initial={user.scopes} errors={form.errors} />
      <SaveRow pending={form.pending} saved={saved} error={form.error} label={t("saveScope")} />
    </form>
  );
}

/**
 * One staff member (US-102, FR-IAM-010..014): who they are, their status, roles and classes.
 * `user.manage` holders can suspend, give access back or remove; `role.assign` holders can
 * change roles and classes. Temporary SchoolOS support access is changed only on the Support
 * access page. Every change is audited by the API.
 */
export function UserDetailScreen({ userId }: { userId: string }) {
  const t = useTranslations("school.users");
  const td = useTranslations("school.users.detail");
  const tl = useTranslations("language");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const me = useStaffMe();
  const can = useStaffCan();
  const state = useUser(userId);
  const roles = useRoles(can(USER_PERM.manage));
  const roleLabel = useRoleLabel(roles.data);
  const scopeSummary = useScopeSummary();

  const back = (
    <Link href="/settings/users" className="text-primary underline">
      {td("back")}
    </Link>
  );

  if (state.status === "loading") return <LoadingState label={tc("loading")} />;
  if (state.status !== "ready") {
    const missing = state.status === "error" && state.reason === "not_found";
    const forbidden = state.status === "error" && state.reason === "forbidden";
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert
          tone={missing || forbidden ? "warning" : "danger"}
          title={td(missing ? "notFoundTitle" : forbidden ? "noAccessTitle" : "loadErrorTitle")}
        >
          <p>{td(missing ? "notFoundBody" : forbidden ? "noAccessBody" : "loadErrorBody")}</p>
        </Alert>
        {back}
      </div>
    );
  }

  const user = state.data;
  const isSelf = me?.user_id === user.id;
  const breakGlass = isBreakGlass(user);
  const removed = user.status === "removed";
  const canManage = can(USER_PERM.manage) && !breakGlass;
  const canAssign = can(USER_PERM.assign) && !breakGlass && !removed;

  return (
    <div className="space-y-6">
      <PageHeader
        title={user.display_name}
        badge={
          <span className="inline-flex flex-wrap gap-2">
            <UserStatusBadge status={user.status} />
            {isSelf ? <Badge tone="info">{td("you")}</Badge> : null}
          </span>
        }
        actions={
          <>
            {canManage ? <StatusActions user={user} isSelf={isSelf} /> : null}
            {back}
          </>
        }
      />

      {breakGlass ? (
        <Alert tone="info" title={td("breakGlassTitle")}>
          <p>{td("breakGlassBody")}</p>
          {can(USER_PERM.breakGlass) ? (
            <p className="mt-2">
              <Link href="/break-glass" className="font-semibold underline">
                {td("breakGlassLink")}
              </Link>
            </p>
          ) : null}
        </Alert>
      ) : null}
      {user.status === "invited" ? (
        <Alert tone="info" title={td("invitedTitle")}>
          {td("invitedBody")}
        </Alert>
      ) : null}
      {removed ? (
        <Alert tone="warning" title={td("removedTitle")}>
          {td("removedBody")}
        </Alert>
      ) : null}

      <Card title={td("aboutTitle")}>
        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Item label={t("colEmail")}>
            <Value>{user.email}</Value>
          </Item>
          <Item label={td("language")}>{tl(user.preferred_language)}</Item>
          <Item label={t("colStatus")}>
            <UserStatusBadge status={user.status} />
          </Item>
          <Item label={td("invitedOn")}>
            <Value>{formatDateTime(user.created_at)}</Value>
          </Item>
          <Item label={t("colLastSignIn")}>
            <Value>{formatDateTime(user.last_login_at)}</Value>
          </Item>
          <Item label={td("accessEnds")}>
            {user.expires_at ? <Value>{formatDateTime(user.expires_at)}</Value> : td("noEnd")}
          </Item>
        </dl>
        <p className="mt-4 text-sm text-ink-muted">{td("detailsNote")}</p>
      </Card>

      <Card title={td("rolesTitle")} description={td("rolesDescription")}>
        {canAssign ? (
          <RolesForm user={user} isSelf={isSelf} />
        ) : (
          <p className="font-semibold">
            {user.roles.length > 0 ? formatList(user.roles.map(roleLabel), locale) : t("noRoles")}
          </p>
        )}
        {!canAssign && !breakGlass && !removed ? (
          <p className="mt-2 text-sm text-ink-muted">{td("assignNeeded")}</p>
        ) : null}
      </Card>

      <Card title={td("scopeTitle")} description={td("scopeDescription")}>
        {canAssign ? (
          <ScopesForm user={user} isSelf={isSelf} />
        ) : (
          <p className="font-semibold">{scopeSummary(user.scopes)}</p>
        )}
      </Card>
    </div>
  );
}

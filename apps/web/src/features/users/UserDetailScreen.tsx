"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent, type ReactNode } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { STAFF_ME_KEY, useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { formList, useApiForm } from "@/lib/forms";
import { optionalEmail, text } from "@/lib/validation";
import { USER_KEYS, useRoles, useUser } from "./data";
import {
  refineScopes,
  RoleCheckboxes,
  rolesField,
  RoleTags,
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
  EMAIL_MAX,
  ifMatch,
  isBreakGlass,
  NAME_MAX,
  STATUS_ACTIONS,
  USER_LANGUAGES,
  USER_PERM,
  type StaffUser,
  type StatusChange,
  type UserLanguage,
} from "./types";

const rolesSchema = z.object({ roles: rolesField });
const scopesSchema = z.object(scopeFields).superRefine(refineScopes);

function Item({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1 rounded-lg border border-border bg-surface-muted px-4 py-3">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="font-semibold break-words text-ink">{children}</dd>
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
 * Only the changes the API allows from the current status are offered. Never on your own
 * account: locking yourself out is done by someone else, not by a slip of the mouse.
 */
function StatusActions({ user }: { user: StaffUser }) {
  const t = useTranslations("school.users.detail.status");
  const api = useBffClient("staff");
  const invalidate = [USER_KEYS.all];
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
 * Send the invitation email again to someone who has not signed in yet
 * (POST /users/{id}/invitation-email; `user.manage`, step-up, 202). The API refuses with 409
 * when email is off, the invitation expired or ended, or there is no address, and with 429
 * within 10 minutes of the last one; each is explained in plain words.
 */
function ResendInvitation({ user }: { user: StaffUser & { email: string } }) {
  const t = useTranslations("school.users.detail.resend");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("button")}
      title={t("title")}
      description={t("body", { email: user.email })}
      confirmLabel={t("button")}
      stepUp
      schema={z.object({})}
      errorNamespace="school.users"
      submit={() =>
        unwrap(
          api.POST("/api/v1/users/{user_id}/invitation-email", {
            params: { path: { user_id: user.id } },
          }),
        )
      }
      renderResult={(result, close) => (
        <>
          <Alert tone="success" live title={t("sentTitle")}>
            {t("sentBody", { date: formatDateTime(result.expires_at) })}
          </Alert>
          <div className="flex justify-end">
            <Button onClick={close}>{tc("done")}</Button>
          </div>
        </>
      )}
    />
  );
}

/* ------------------------------------------------------------------ profile */

/** Client checks mirroring `UserUpdateIn` (display name 1–200, email ≤ 254 or empty). */
export const profileSchema = z
  .object({
    display_name: text(NAME_MAX),
    email: optionalEmail,
    preferred_language: z.enum(USER_LANGUAGES, { error: "chooseOption" }),
  })
  .superRefine((data, context) => {
    // Invariant 4: never a full Aadhaar number, not even in a name or an email address.
    if (containsAadhaarNumber(data.display_name)) {
      context.addIssue({ code: "custom", path: ["display_name"], message: "noAadhaar" });
    }
    if (data.email !== null && containsAadhaarNumber(data.email)) {
      context.addIssue({ code: "custom", path: ["email"], message: "noAadhaar" });
    }
  });
export type ProfileInput = z.output<typeof profileSchema>;

/** PATCH body with only what changed (`email: null` clears it; docs/09 UserUpdateIn). */
export function profileBody(current: StaffUser, data: ProfileInput) {
  const body: {
    display_name?: string;
    email?: string | null;
    preferred_language?: UserLanguage;
  } = {};
  if (data.display_name !== current.display_name) body.display_name = data.display_name;
  if (data.email !== (current.email?.toLowerCase() ?? null)) body.email = data.email;
  if (data.preferred_language !== current.preferred_language) {
    body.preferred_language = data.preferred_language;
  }
  return body;
}

/**
 * Correct a staff member's name, email or language (PATCH /users/{id}; `user.manage`,
 * step-up through the global prompt, If-Match). The sign-in account never changes here. The
 * API audits the changed field names only; a removed member answers 409 `invalid_state`.
 */
function EditProfile({ user, isSelf }: { user: StaffUser; isSelf: boolean }) {
  const t = useTranslations("school.users.detail.edit");
  const tl = useTranslations("language");
  const api = useBffClient("staff");
  const telugu = useTeluguEnabled();
  return (
    <ActionDialog
      triggerLabel={t("trigger")}
      title={t("title")}
      description={t("description")}
      confirmLabel={t("submit")}
      stepUp
      schema={profileSchema}
      // ADR-0036: while Telugu is switched off the language is not asked for and stays as is.
      {...(telugu ? {} : { extra: () => ({ preferred_language: user.preferred_language }) })}
      fieldMap={userFieldMap}
      invalidate={[USER_KEYS.all, ...(isSelf ? [STAFF_ME_KEY] : [])]}
      errorNamespace="school.users"
      submit={(data) => {
        const body = profileBody(user, data);
        if (Object.keys(body).length === 0) return Promise.resolve(user);
        return unwrap(
          api.PATCH("/api/v1/users/{user_id}", {
            params: { path: { user_id: user.id } },
            headers: { "If-Match": ifMatch(user.version) },
            body,
          }),
        );
      }}
    >
      {(errors) => (
        <>
          <TextField
            name="display_name"
            label={t("name")}
            hint={t("nameHint")}
            defaultValue={user.display_name}
            error={errors.display_name}
            maxLength={NAME_MAX}
            autoComplete="off"
            required
          />
          <TextField
            name="email"
            type="email"
            label={t("email")}
            hint={t("emailHint")}
            defaultValue={user.email ?? ""}
            error={errors.email}
            maxLength={EMAIL_MAX}
            autoComplete="off"
          />
          {telugu ? (
            <SelectField
              name="preferred_language"
              label={t("language")}
              hint={t("languageHint")}
              defaultValue={user.preferred_language}
              error={errors.preferred_language}
              options={USER_LANGUAGES.map((value) => ({ value, label: tl(value) }))}
            />
          ) : null}
        </>
      )}
    </ActionDialog>
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
  const telugu = useTeluguEnabled();
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const me = useStaffMe();
  const can = useStaffCan();
  const state = useUser(userId);
  const roles = useRoles(can(USER_PERM.manage));
  const roleLabel = useRoleLabel(roles.data);
  const scopeSummary = useScopeSummary();

  const back = (
    <Link href="/settings/users" className="text-sm text-primary underline underline-offset-4">
      {td("back")}
    </Link>
  );

  if (state.status === "loading") return <LoadingState label={tc("loading")} />;
  if (state.status !== "ready") {
    const missing = state.status === "error" && state.reason === "not_found";
    const forbidden = state.status === "error" && state.reason === "forbidden";
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("title")}
          breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
        />
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
  // ADR-0028: a profile shared with another school is not edited here (409 profile_shared).
  const profileShared = user.profile_shared;

  return (
    <div className="space-y-6">
      <PageHeader
        title={user.display_name}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: t("title"), href: "/settings/users" },
          { label: user.display_name },
        ]}
        badge={
          <span className="inline-flex flex-wrap items-center gap-2">
            <UserStatusBadge status={user.status} />
            {isSelf ? <Badge tone="info">{td("you")}</Badge> : null}
          </span>
        }
        actions={
          <>
            {canManage && !isSelf ? <StatusActions user={user} /> : null}
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
          <p>{td("invitedBody")}</p>
          {canManage && user.email ? (
            <div className="mt-3">
              <ResendInvitation user={{ ...user, email: user.email }} />
            </div>
          ) : null}
          {canManage && !user.email ? <p className="mt-2">{td("resend.noEmail")}</p> : null}
        </Alert>
      ) : null}
      {removed ? (
        <Alert tone="warning" title={td("removedTitle")}>
          {td("removedBody")}
        </Alert>
      ) : null}

      <Card
        title={td("aboutTitle")}
        actions={
          canManage && !removed && !profileShared ? (
            <EditProfile user={user} isSelf={isSelf} />
          ) : null
        }
      >
        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Item label={t("colEmail")}>
            <span className="font-mono text-sm break-all">
              <Value>{user.email}</Value>
            </span>
          </Item>
          {telugu ? <Item label={td("language")}>{tl(user.preferred_language)}</Item> : null}
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
        {isSelf && canManage && !removed ? (
          <p className="mt-4 text-sm text-ink-muted">{td("selfNoStatus")}</p>
        ) : null}
        {profileShared && canManage && !removed ? (
          <p className="mt-4 text-sm text-ink-muted">{td("profileShared")}</p>
        ) : null}
        {!canManage && !breakGlass ? (
          <p className="mt-4 text-sm text-ink-muted">{td("detailsNote")}</p>
        ) : null}
      </Card>

      <Card title={td("rolesTitle")} description={td("rolesDescription")}>
        {canAssign ? (
          <RolesForm user={user} isSelf={isSelf} />
        ) : (
          <RoleTags roles={user.roles} label={roleLabel} />
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

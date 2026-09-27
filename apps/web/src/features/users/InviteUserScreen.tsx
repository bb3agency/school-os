"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffMe, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formList, useApiForm } from "@/lib/forms";
import { optionalEmail, text } from "@/lib/validation";
import { USER_KEYS, useRoles } from "./data";
import {
  canGrantRole,
  refineScopes,
  RoleCheckboxes,
  rolesField,
  ScopeEditor,
  scopeExtra,
  scopeFields,
  scopesBody,
  userFieldMap,
} from "./parts";
import {
  NAME_MAX,
  SUBJECT_MAX,
  SUBJECT_PATTERN,
  USER_LANGUAGES,
  USER_PERM,
  type StaffUser,
} from "./types";

/** Client checks mirroring `InviteIn` (apps/api/app/identity/schemas.py). */
export const inviteSchema = z
  .object({
    display_name: text(NAME_MAX),
    email: optionalEmail,
    idp_subject: z
      .string()
      .trim()
      .min(1, { error: "required" })
      .max(SUBJECT_MAX, { error: "tooLong" })
      .refine((value) => !/\s/.test(value), { error: "noSpaces" })
      .refine((value) => value === "" || /\s/.test(value) || SUBJECT_PATTERN.test(value), {
        error: "invalidSubject",
      }),
    preferred_language: z.enum(USER_LANGUAGES, { error: "chooseOption" }),
    roles: rolesField,
    ...scopeFields,
  })
  .superRefine(refineScopes);

/**
 * Invite a staff member (US-102 AC1, FR-IAM-010..012; POST /users with an Idempotency-Key,
 * `user.manage` and a fresh MFA sign-in). The person's sign-in account must already exist in
 * the school's sign-in service: the office types its user ID here. The invitation becomes
 * active when they sign in (ADR-0019).
 */
export function InviteUserScreen() {
  const t = useTranslations("school.users.inviteForm");
  const tu = useTranslations("school.users");
  const tl = useTranslations("language");
  const tc = useTranslations("common");
  const locale = useLocale();
  const router = useRouter();
  const api = useBffClient("staff");
  const meQuery = useStaffMeQuery();
  const me = useStaffMe();
  const allowed = !me || me.permissions.includes(USER_PERM.manage);
  const roles = useRoles(allowed);
  const languageErrorId = useId();

  const form = useApiForm({
    schema: inviteSchema,
    extra: (element) => ({ roles: formList(element, "roles"), ...scopeExtra(element) }),
    fieldMap: userFieldMap,
    invalidate: [USER_KEYS.all],
    submit: (data, key): Promise<StaffUser> =>
      unwrap(
        api.POST("/api/v1/users", {
          headers: { "Idempotency-Key": key },
          body: {
            display_name: data.display_name,
            email: data.email,
            idp_subject: data.idp_subject,
            preferred_language: data.preferred_language,
            roles: [...new Set(data.roles)],
            scopes: scopesBody(data),
          },
        }),
      ),
    onSuccess: (created) => router.push(`/settings/users/${created.id}`),
  });

  if (meQuery.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="warning" title={tu("noAccessTitle")}>
          {tu("noAccessBody")}
        </Alert>
      </div>
    );
  }
  if (roles.isPending && roles.fetchStatus !== "idle") {
    return <LoadingState label={tc("loading")} />;
  }

  const { errors } = form;
  const initialLanguage = locale === "te" ? "te" : "en";

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <Alert tone="info" title={t("stepUpTitle")}>
        {t("stepUpBody")}
      </Alert>
      <form noValidate onSubmit={form.onSubmit} className="space-y-6">
        <Card title={t("personTitle")}>
          <div className="grid gap-4 lg:grid-cols-2">
            <TextField
              name="display_name"
              label={t("name")}
              hint={t("nameHint")}
              error={errors.display_name}
              maxLength={NAME_MAX}
              autoComplete="off"
            />
            <TextField
              name="email"
              type="email"
              label={t("email")}
              hint={t("emailHint")}
              error={errors.email}
              autoComplete="off"
            />
            <TextField
              name="idp_subject"
              label={t("subject")}
              hint={t("subjectHint")}
              error={errors.idp_subject}
              maxLength={SUBJECT_MAX}
              autoComplete="off"
              spellCheck={false}
            />
            <fieldset
              className="space-y-1"
              aria-describedby={errors.preferred_language ? languageErrorId : undefined}
            >
              <legend className="text-sm font-semibold text-ink">{t("language")}</legend>
              <p className="text-sm text-ink-muted">{t("languageHint")}</p>
              <div className="flex flex-wrap gap-x-6">
                {USER_LANGUAGES.map((value) => (
                  <label key={value} className="inline-flex min-h-8 items-center gap-2 text-sm">
                    <input
                      type="radio"
                      name="preferred_language"
                      value={value}
                      defaultChecked={value === initialLanguage}
                      className="size-4"
                    />
                    {tl(value)}
                  </label>
                ))}
              </div>
              {errors.preferred_language ? (
                <p id={languageErrorId} className="text-sm font-semibold text-danger">
                  {errors.preferred_language}
                </p>
              ) : null}
            </fieldset>
          </div>
        </Card>

        <Card title={t("rolesTitle")}>
          {roles.data ? (
            <RoleCheckboxes
              roles={roles.data}
              selected={[]}
              grantable={(role) => (me ? canGrantRole(role, me, "invite") : false)}
              error={errors.roles}
              legend={t("rolesLegend")}
            />
          ) : (
            <ApiErrorAlert error={roles.error} namespace="school.users" />
          )}
        </Card>

        <Card title={t("scopeTitle")}>
          <ScopeEditor initial={[]} initialMode="none" errors={errors} />
        </Card>

        <ApiErrorAlert error={form.error} namespace="school.users" />
        <div className="flex flex-wrap justify-end gap-3">
          <Link href="/settings/users" className="self-center text-primary underline">
            {tc("cancel")}
          </Link>
          <Button
            type="submit"
            disabled={form.pending || !roles.data}
            aria-disabled={form.pending || !roles.data || undefined}
          >
            {form.pending ? tc("working") : t("submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}

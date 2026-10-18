"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import * as z from "@/lib/zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { useRouter } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffMe, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formList, useApiForm } from "@/lib/forms";
import { optionalEmail, text } from "@/lib/validation";
import { USER_KEYS, useRoles } from "./data";
import {
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

/** Ids of the invite form's fields (and id prefixes of its choice lists), for the summary. */
const IDS = {
  display_name: "invite-name",
  email: "invite-email",
  idp_subject: "invite-subject",
  language: "invite-language",
  roles: "invite-role",
  scope: "invite-scope",
} as const;

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
  const tn = useTranslations("school.nav");
  const locale = useLocale();
  const router = useRouter();
  const api = useBffClient("staff");
  const meQuery = useStaffMeQuery();
  const me = useStaffMe();
  const allowed = !me || me.permissions.includes(USER_PERM.manage);
  const roles = useRoles(allowed);
  const languageErrorId = useId();
  const telugu = useTeluguEnabled();
  const [attempt, setAttempt] = useState(0);
  const [dirty, setDirty] = useState(false);

  const form = useApiForm({
    onInvalid: () => setAttempt((value) => value + 1),
    schema: inviteSchema,
    extra: (element) => ({
      roles: formList(element, "roles"),
      ...scopeExtra(element),
      // ADR-0036: while Telugu is switched off nobody is asked; everyone works in English.
      ...(telugu ? {} : { preferred_language: "en" }),
    }),
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

  const breadcrumb = [
    { label: tn("home"), href: "/" },
    { label: tu("title"), href: "/settings/users" },
    { label: t("title") },
  ];
  if (meQuery.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
    return (
      <div className="space-y-5">
        <PageHeader title={t("title")} breadcrumb={breadcrumb} />
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
  const firstRole = roles.data?.find((role) => role.grantable)?.key;
  const scopeError = errors.scope_mode ?? errors.class_refs ?? errors.section_refs;
  const summary: SummaryError[] = [
    ...(errors.display_name ? [{ fieldId: IDS.display_name, message: errors.display_name }] : []),
    ...(errors.email ? [{ fieldId: IDS.email, message: errors.email }] : []),
    ...(errors.idp_subject ? [{ fieldId: IDS.idp_subject, message: errors.idp_subject }] : []),
    ...(errors.preferred_language && telugu
      ? [{ fieldId: `${IDS.language}-${initialLanguage}`, message: errors.preferred_language }]
      : []),
    ...(errors.roles && firstRole
      ? [{ fieldId: `${IDS.roles}-${firstRole}`, message: errors.roles }]
      : []),
    ...(scopeError
      ? [
          {
            fieldId: `${IDS.scope}-mode-${errors.scope_mode ? "none" : "chosen"}`,
            message: scopeError,
          },
        ]
      : []),
  ];

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <PageHeader title={t("title")} description={t("description")} breadcrumb={breadcrumb} />
      <Alert tone="info" title={t("stepUpTitle")}>
        {t("stepUpBody")}
      </Alert>
      <form
        noValidate
        onSubmit={form.onSubmit}
        onChange={() => setDirty(true)}
        className="space-y-5"
      >
        <ErrorSummary errors={summary} attempt={attempt} />
        <Card title={t("personTitle")}>
          <div className="grid gap-4 lg:grid-cols-2">
            <TextField
              id={IDS.display_name}
              name="display_name"
              label={t("name")}
              hint={t("nameHint")}
              error={errors.display_name}
              maxLength={NAME_MAX}
              autoComplete="off"
            />
            <TextField
              id={IDS.email}
              name="email"
              type="email"
              label={t("email")}
              hint={t("emailHint")}
              error={errors.email}
              autoComplete="off"
            />
            <TextField
              id={IDS.idp_subject}
              name="idp_subject"
              label={t("subject")}
              hint={t("subjectHint")}
              error={errors.idp_subject}
              maxLength={SUBJECT_MAX}
              autoComplete="off"
              spellCheck={false}
            />
            {telugu ? (
              <fieldset
                className="space-y-1"
                aria-describedby={errors.preferred_language ? languageErrorId : undefined}
              >
                <legend className="text-sm font-semibold text-ink">{t("language")}</legend>
                <p className="text-sm text-ink-muted">{t("languageHint")}</p>
                <div className="flex flex-wrap gap-2">
                  {USER_LANGUAGES.map((value) => (
                    <label
                      key={value}
                      className="inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-md border border-border-soft bg-surface px-3 text-sm has-checked:border-primary has-checked:bg-primary-soft"
                    >
                      <input
                        id={`${IDS.language}-${value}`}
                        type="radio"
                        name="preferred_language"
                        value={value}
                        defaultChecked={value === initialLanguage}
                        className="size-4 accent-primary"
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
            ) : null}
          </div>
        </Card>

        <Card title={t("rolesTitle")}>
          {roles.data ? (
            <RoleCheckboxes
              id={IDS.roles}
              roles={roles.data}
              selected={[]}
              error={errors.roles}
              legend={t("rolesLegend")}
            />
          ) : (
            <ApiErrorAlert error={roles.error} namespace="school.users" />
          )}
        </Card>

        <Card title={t("scopeTitle")}>
          <ScopeEditor id={IDS.scope} initial={[]} initialMode="none" errors={errors} />
        </Card>

        <ApiErrorAlert error={form.error} namespace="school.users" />
        <SaveBar dirty={dirty}>
          <ButtonLink href="/settings/users" variant="secondary">
            {tc("cancel")}
          </ButtonLink>
          <Button
            type="submit"
            disabled={form.pending || !roles.data}
            aria-disabled={form.pending || !roles.data || undefined}
          >
            {form.pending ? tc("working") : t("submit")}
          </Button>
        </SaveBar>
      </form>
    </div>
  );
}

"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { attributeLabel } from "@/features/findings/data";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formList, useApiForm } from "@/lib/forms";
import { checkbox } from "@/lib/validation";
import { EXPORT_KEYS, useExportAttributes, useExportProfiles } from "./data";
import type { NewPrecheckParams } from "./filters";
import {
  exportFieldMap,
  LanguageField,
  refineScope,
  ScopePicker,
  scopeBody,
  scopeFields,
  StepUpNotice,
} from "./parts";
import {
  EXPORT_LANGUAGES,
  EXPORT_PERM,
  PRECHECK_FORMATS,
  PROFILE_KEY_PATTERN,
  PROFILE_PERMISSION,
  RESTRICTED_CLASSIFICATION,
  type Export,
  type ExportProfile,
} from "./types";

/** Client checks mirroring `PrecheckCreate` (apps/api/app/exports/schemas.py). */
export const precheckSchema = z
  .object({
    profile_key: z.string().trim().regex(PROFILE_KEY_PATTERN, { error: "chooseOption" }),
    format: z
      .array(z.enum(PRECHECK_FORMATS))
      .min(1, { error: "chooseFormats" })
      .max(PRECHECK_FORMATS.length, { error: "chooseFormats" }),
    language: z.enum(EXPORT_LANGUAGES, { error: "chooseOption" }),
    include_sensitive: checkbox,
    ...scopeFields,
  })
  .superRefine(refineScope);

/** Profiles this member may run: the API's `allowed` and their own permission from /me. */
export function runnableProfiles(
  profiles: readonly ExportProfile[] | undefined,
  can: (permission: string) => boolean,
): ExportProfile[] {
  return (profiles ?? []).filter(
    (profile) => profile.allowed && can(PROFILE_PERMISSION[profile.kind]),
  );
}

/**
 * New board or portal pre-check (US-501 AC4, FR-EXP-001..004): profile, students, formats and
 * language; restricted details (e.g. UDISE+ category) only by explicit opt-in of
 * `student.read_sensitive` holders. Every export asks for a fresh MFA sign-in (ADR-0021): the
 * global step-up prompt handles the 428 and sends the same request again.
 */
export function NewPrecheckScreen({ params }: { params: NewPrecheckParams }) {
  const t = useTranslations("exports.new");
  const tp = useTranslations("exports.precheck");
  const tf = useTranslations("exports.format");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const te = useTranslations("exports");
  const locale = useLocale();
  const router = useRouter();
  const api = useBffClient("staff");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const mayRun = can([EXPORT_PERM.board, EXPORT_PERM.portal]);
  const profiles = useExportProfiles(mayRun);
  const canSensitive = can(EXPORT_PERM.readSensitive);
  const attributes = useExportAttributes(mayRun && can(EXPORT_PERM.readBasic));
  const runnable = runnableProfiles(profiles.data, (permission) => can(permission));
  const [profileKey, setProfileKey] = useState<string>(params.profileKey ?? "");
  const formatErrorId = useId();
  const profile =
    runnable.find((item) => item.key === profileKey) ??
    (runnable.length === 1 ? runnable[0] : undefined);
  const restricted = (profile?.fields ?? []).filter(
    (key) =>
      attributes.data?.find((item) => item.key === key)?.classification ===
      RESTRICTED_CLASSIFICATION,
  );
  // Offer the opt-in only to read_sensitive holders, and only when the profile has restricted
  // fields (shown too while the field list cannot be read, so it is never silently missing).
  const offerSensitive = canSensitive && (!attributes.data || restricted.length > 0);
  const fieldName = (key: string) => attributeLabel(attributes.data, key, locale) ?? key;

  const form = useApiForm({
    schema: precheckSchema,
    extra: (element) => ({
      format: formList(element, "format"),
      class_ids: formList(element, "class_ids"),
      section_ids: formList(element, "section_ids"),
    }),
    fieldMap: exportFieldMap,
    invalidate: [EXPORT_KEYS.all],
    submit: (data, key): Promise<Export> =>
      unwrap(
        api.POST("/api/v1/exports", {
          headers: { "Idempotency-Key": key },
          body: {
            profile_key: data.profile_key,
            scope: scopeBody(data),
            format: data.format,
            language: data.language,
            include_sensitive: canSensitive && data.include_sensitive,
          },
        }),
      ),
    onSuccess: (created) => router.push(`/exports/${created.id}`),
  });

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!mayRun) {
    return (
      <div className="space-y-6">
        <PageHeader
          title={tp("title")}
          breadcrumb={[
            { label: tn("home"), href: "/" },
            { label: te("title"), href: "/exports" },
            { label: tp("title") },
          ]}
        />
        <Alert tone="warning" title={tp("noPermissionTitle")}>
          {tp("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  if (profiles.isPending) return <LoadingState label={tc("loading")} />;
  if (profiles.isError || runnable.length === 0) {
    return (
      <div className="space-y-6">
        <PageHeader
          title={tp("title")}
          breadcrumb={[
            { label: tn("home"), href: "/" },
            { label: te("title"), href: "/exports" },
            { label: tp("title") },
          ]}
        />
        <Alert
          tone="warning"
          title={tp(profiles.isError ? "profilesErrorTitle" : "noProfilesTitle")}
        >
          {tp(profiles.isError ? "profilesErrorBody" : "noProfilesBody")}
        </Alert>
        <Link href="/exports" className="text-primary underline">
          {t("backToList")}
        </Link>
      </div>
    );
  }

  const { errors } = form;
  const profileLabel = (item: ExportProfile) =>
    `${locale === "te" && item.label_te ? item.label_te : item.label_en} (${t(`profileKind.${item.kind}`)})`;

  return (
    <div className="space-y-6">
      <PageHeader
        title={tp("title")}
        description={tp("description")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: te("title"), href: "/exports" },
          { label: tp("title") },
        ]}
      />
      <StepUpNotice />
      <form noValidate onSubmit={form.onSubmit} className="space-y-6">
        <Card title={tp("profileTitle")}>
          <div className="space-y-4">
            <SelectField
              name="profile_key"
              label={tp("profile")}
              hint={tp("profileHint")}
              error={errors.profile_key}
              value={profile?.key ?? ""}
              onChange={(event) => setProfileKey(event.target.value)}
              {...(runnable.length === 1 ? {} : { placeholder: tc("chooseOne") })}
              options={runnable.map((item) => ({ value: item.key, label: profileLabel(item) }))}
            />
            {profile ? (
              <div className="space-y-1 text-sm">
                <p className="text-ink-muted">{tp("fieldsIntro")}</p>
                <p className="font-semibold">{profile.fields.map(fieldName).join(", ")}</p>
              </div>
            ) : null}
          </div>
        </Card>

        <Card title={t("studentsTitle")}>
          <ScopePicker errors={errors} />
        </Card>

        <Card title={t("fileTitle")}>
          <div className="space-y-4">
            <fieldset
              className="space-y-1"
              aria-describedby={errors.format ? formatErrorId : undefined}
            >
              <legend className="text-sm font-semibold text-ink">{t("formats")}</legend>
              <p className="text-sm text-ink-muted">{tp("formatsHint")}</p>
              <div className="flex flex-wrap gap-x-6">
                {PRECHECK_FORMATS.map((format, index) => (
                  <label key={format} className="inline-flex min-h-8 items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      name="format"
                      value={format}
                      defaultChecked
                      aria-invalid={(index === 0 && Boolean(errors.format)) || undefined}
                      className="size-4"
                    />
                    {tf(format)}
                  </label>
                ))}
              </div>
              {errors.format ? (
                <p id={formatErrorId} className="text-sm font-semibold text-danger">
                  {errors.format}
                </p>
              ) : null}
            </fieldset>
            <LanguageField errors={errors} />
          </div>
        </Card>

        {offerSensitive ? (
          <Card title={tp("sensitiveTitle")}>
            <div className="space-y-3">
              <p className="text-sm">
                {restricted.length > 0
                  ? tp("sensitiveFields", { fields: restricted.map(fieldName).join(", ") })
                  : tp("sensitiveGeneric")}
              </p>
              <label className="flex min-h-8 items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  name="include_sensitive"
                  value="on"
                  className="mt-0.5 size-4"
                />
                <span className="font-semibold">{tp("sensitiveLabel")}</span>
              </label>
              <Alert tone="warning">{tp("sensitiveWarning")}</Alert>
            </div>
          </Card>
        ) : null}

        <ApiErrorAlert error={form.error} namespace="exports" />
        <div className="flex flex-wrap justify-end gap-3">
          <Link href="/exports" className={buttonClasses("ghost", "md")}>
            {tc("cancel")}
          </Link>
          <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
            {form.pending ? tc("working") : tp("submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}

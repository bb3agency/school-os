"use client";

import { ANNOUNCEMENT_SEVERITIES, type AnnouncementInput } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { Tabs } from "@/components/ui/Tabs";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formList, useApiForm } from "@/lib/forms";
import { localDateTime } from "@/lib/validation";
import { PK, useSchoolDirectory } from "./data";

const bilingual = (max: number) =>
  z.string().trim().min(1, { error: "bothLanguages" }).max(max, { error: "tooLong" });
const english = (max: number) =>
  z.string().trim().min(1, { error: "required" }).max(max, { error: "tooLong" });

/**
 * The banner form. With Telugu switched on both languages are required; with it off
 * (ADR-0036) only English is asked for, and because the API still requires the Telugu texts
 * the English ones stand in for them until Telugu returns.
 */
export function announcementSchemaFor(telugu: boolean) {
  return z
    .object({
      title_en: telugu ? bilingual(120) : english(120),
      title_te: telugu ? bilingual(120) : z.string().optional(),
      body_en: telugu ? bilingual(1000) : english(1000),
      body_te: telugu ? bilingual(1000) : z.string().optional(),
      severity: z.enum(ANNOUNCEMENT_SEVERITIES, { error: "chooseOption" }),
      audience: z.enum(["all", "shared", "dedicated", "tenants"], { error: "chooseOption" }),
      audience_tenant_ids: z.array(z.string()),
      starts_at: localDateTime,
      ends_at: localDateTime,
      status: z.enum(["draft", "scheduled"], { error: "chooseOption" }),
    })
    .superRefine((value, ctx) => {
      if (value.audience === "tenants" && value.audience_tenant_ids.length === 0) {
        ctx.addIssue({ code: "custom", path: ["audience_tenant_ids"], message: "chooseSchools" });
      }
      if (value.ends_at <= value.starts_at) {
        ctx.addIssue({ code: "custom", path: ["ends_at"], message: "endAfterStart" });
      }
    })
    .transform((value): AnnouncementInput => ({
      title_en: value.title_en,
      title_te: telugu ? (value.title_te ?? "") : value.title_en,
      body_en: value.body_en,
      body_te: telugu ? (value.body_te ?? "") : value.body_en,
      severity: value.severity,
      audience:
        value.audience === "shared" || value.audience === "dedicated" ? "tier" : value.audience,
      audience_tier:
        value.audience === "shared" || value.audience === "dedicated" ? value.audience : null,
      audience_tenant_ids: value.audience === "tenants" ? value.audience_tenant_ids : [],
      starts_at: value.starts_at,
      ends_at: value.ends_at,
      status: value.status,
    }));
}

export const announcementSchema = announcementSchemaFor(true);

/**
 * FR-PLT-026 (docs/16 §5.13, §14): bilingual banner editor. English and Telugu title and
 * message are both required; times are entered in IST and stored in UTC. While Telugu is
 * switched off (ADR-0036) only the English title and message are shown.
 */
export function AnnouncementEditor() {
  const t = useTranslations("platform.announcements");
  const tv = useTranslations("validation");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const { schools } = useSchoolDirectory();
  const [audience, setAudience] = useState("all");
  const [saved, setSaved] = useState(false);
  const telugu = useTeluguEnabled();

  const form = useApiForm({
    schema: announcementSchemaFor(telugu),
    extra: (element) => ({ audience_tenant_ids: formList(element, "audience_tenant_ids") }),
    invalidate: [PK.announcements],
    submit: (data, key) => {
      setSaved(false);
      return unwrap(
        api.POST("/api/v1/platform/announcements", {
          params: { header: { "Idempotency-Key": key } },
          body: data,
        }),
      );
    },
    onSuccess: (_result, element) => {
      element.reset();
      setAudience("all");
      setSaved(true);
    },
  });
  const errors = form.errors;

  const languagePanel = (lang: "en" | "te") => (
    <div className="space-y-4" lang={lang}>
      <TextField
        name={`title_${lang}`}
        label={t("titleLabel")}
        error={errors[`title_${lang}`]}
        maxLength={120}
        autoComplete="off"
      />
      <TextAreaField
        name={`body_${lang}`}
        label={t("bodyLabel")}
        error={errors[`body_${lang}`]}
        maxLength={1000}
        rows={4}
      />
    </div>
  );

  const languageErrors = errors.title_en || errors.title_te || errors.body_en || errors.body_te;

  return (
    <form noValidate onSubmit={form.onSubmit} className="space-y-4">
      {telugu ? (
        <>
          <p className="text-sm text-ink-muted">{t("bothLanguagesHint")}</p>
          {languageErrors ? (
            <Alert tone="danger" live>
              {tv("bothLanguages")}
            </Alert>
          ) : null}
          <Tabs
            label={t("languagesLabel")}
            items={[
              { id: "en", label: t("english"), panel: languagePanel("en") },
              { id: "te", label: t("telugu"), panel: languagePanel("te") },
            ]}
          />
        </>
      ) : (
        languagePanel("en")
      )}
      <div className="grid gap-4 md:grid-cols-3">
        <SelectField
          name="severity"
          label={t("severity")}
          error={errors.severity}
          defaultValue="info"
          options={ANNOUNCEMENT_SEVERITIES.map((value) => ({
            value,
            label: t(`severities.${value}`),
          }))}
        />
        <SelectField
          name="audience"
          label={t("audience")}
          error={errors.audience}
          value={audience}
          onChange={(event) => setAudience(event.currentTarget.value)}
          options={[
            { value: "all", label: t("audienceAll") },
            { value: "shared", label: t("audienceShared") },
            { value: "dedicated", label: t("audienceDedicated") },
            { value: "tenants", label: t("audienceTenants") },
          ]}
        />
        <SelectField
          name="status"
          label={t("publishAs")}
          defaultValue="scheduled"
          options={[
            { value: "scheduled", label: t("statusScheduled") },
            { value: "draft", label: t("statusDraft") },
          ]}
        />
        <TextField
          name="starts_at"
          type="datetime-local"
          label={t("startsAt")}
          hint={tc("istHint")}
          error={errors.starts_at}
        />
        <TextField
          name="ends_at"
          type="datetime-local"
          label={t("endsAt")}
          hint={tc("istHint")}
          error={errors.ends_at}
        />
      </div>
      {audience === "tenants" ? (
        <fieldset
          className="max-h-56 space-y-1 overflow-y-auto rounded-md border border-border p-3"
          aria-describedby={errors.audience_tenant_ids ? "announcement-schools-error" : undefined}
        >
          <legend className="px-1 text-sm font-semibold">{t("chooseSchools")}</legend>
          {schools.map((school) => (
            <label key={school.tenant_id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="audience_tenant_ids"
                value={school.tenant_id}
                className="size-4 accent-primary"
              />
              {school.school_name} ({school.code})
            </label>
          ))}
          {errors.audience_tenant_ids ? (
            <p id="announcement-schools-error" className="text-sm font-semibold text-danger">
              {errors.audience_tenant_ids}
            </p>
          ) : null}
        </fieldset>
      ) : null}
      <ApiErrorAlert error={form.error} />
      {saved ? (
        <Alert tone="success" live>
          {t("saved")}
        </Alert>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={form.pending}>
          {form.pending ? tc("working") : t("save")}
        </Button>
      </div>
    </form>
  );
}

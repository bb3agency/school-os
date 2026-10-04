"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  ANNOUNCEMENT_SEVERITIES,
  type Announcement,
  type AnnouncementInput,
} from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { Tabs } from "@/components/ui/Tabs";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { formList, useApiForm, type FieldErrors } from "@/lib/forms";
import { localDateTime } from "@/lib/validation";
import { ifMatch, PK, useSchoolDirectory } from "./data";

const bilingual = (max: number) =>
  z.string().trim().min(1, { error: "bothLanguages" }).max(max, { error: "tooLong" });
const english = (max: number) =>
  z.string().trim().min(1, { error: "required" }).max(max, { error: "tooLong" });

/**
 * The banner form. With Telugu switched on both languages are required; with it off
 * (ADR-0036) only English is asked for. A new announcement then sends the English texts in the
 * API's Telugu fields; an update (`update: true`) sends them empty, so the API keeps the
 * Telugu text it already has (docs/16 §5.13).
 */
export function announcementSchemaFor(telugu: boolean, { update = false } = {}) {
  const hiddenTelugu = (english: string) => (update ? "" : english);
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
      title_te: telugu ? (value.title_te ?? "") : hiddenTelugu(value.title_en),
      body_en: value.body_en,
      body_te: telugu ? (value.body_te ?? "") : hiddenTelugu(value.body_en),
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

/** The edit form: the same fields and limits, plus the version the operator opened. */
export function announcementEditSchemaFor(telugu: boolean) {
  return z
    .object({ version: z.coerce.number().int().min(1) })
    .and(announcementSchemaFor(telugu, { update: true }));
}

/** A stored UTC time as the value of a `datetime-local` input in IST (UTC+05:30). */
export function utcToLocalDateTime(value: string): string {
  const time = Date.parse(value);
  if (Number.isNaN(time)) return "";
  return new Date(time + 330 * 60_000).toISOString().slice(0, 16);
}

/** The form's audience choice for a stored announcement (`tier` splits into its two tiers). */
function audienceChoice(row: Announcement | undefined): string {
  if (!row) return "all";
  if (row.audience === "tier") return row.audience_tier === "dedicated" ? "dedicated" : "shared";
  return row.audience;
}

/**
 * The banner's fields, empty for a new announcement or filled from `initial` to edit one.
 * Uncontrolled except the audience, which shows the school list for "Chosen schools".
 */
function AnnouncementFields({
  errors,
  initial,
}: {
  errors: FieldErrors;
  initial?: Announcement | undefined;
}) {
  const t = useTranslations("platform.announcements");
  const tv = useTranslations("validation");
  const tc = useTranslations("common");
  const { schools } = useSchoolDirectory();
  const telugu = useTeluguEnabled();
  const [audience, setAudience] = useState(() => audienceChoice(initial));
  const schoolsErrorId = useId();
  const chosen = new Set(initial?.audience_tenant_ids ?? []);

  const languagePanel = (lang: "en" | "te") => (
    <div className="space-y-4" lang={lang}>
      <TextField
        name={`title_${lang}`}
        label={t("titleLabel")}
        error={errors[`title_${lang}`]}
        maxLength={120}
        autoComplete="off"
        defaultValue={initial?.[`title_${lang}`]}
      />
      <TextAreaField
        name={`body_${lang}`}
        label={t("bodyLabel")}
        error={errors[`body_${lang}`]}
        maxLength={1000}
        rows={4}
        defaultValue={initial?.[`body_${lang}`]}
      />
    </div>
  );

  const languageErrors = errors.title_en || errors.title_te || errors.body_en || errors.body_te;

  return (
    <>
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
          defaultValue={initial?.severity ?? "info"}
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
          error={errors.status}
          defaultValue={initial?.status === "draft" ? "draft" : "scheduled"}
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
          defaultValue={initial ? utcToLocalDateTime(initial.starts_at) : undefined}
        />
        <TextField
          name="ends_at"
          type="datetime-local"
          label={t("endsAt")}
          hint={tc("istHint")}
          error={errors.ends_at}
          defaultValue={initial ? utcToLocalDateTime(initial.ends_at) : undefined}
        />
      </div>
      {audience === "tenants" ? (
        <fieldset
          className="max-h-56 space-y-1 overflow-y-auto rounded-md border border-border p-3"
          aria-describedby={errors.audience_tenant_ids ? schoolsErrorId : undefined}
        >
          <legend className="px-1 text-sm font-semibold">{t("chooseSchools")}</legend>
          {schools.map((school) => (
            <label key={school.tenant_id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="audience_tenant_ids"
                value={school.tenant_id}
                defaultChecked={chosen.has(school.tenant_id)}
                className="size-4 accent-primary"
              />
              {school.school_name} ({school.code})
            </label>
          ))}
          {errors.audience_tenant_ids ? (
            <p id={schoolsErrorId} className="text-sm font-semibold text-danger">
              {errors.audience_tenant_ids}
            </p>
          ) : null}
        </fieldset>
      ) : null}
    </>
  );
}

const tenantIds = (element: HTMLFormElement) => ({
  audience_tenant_ids: formList(element, "audience_tenant_ids"),
});

/**
 * FR-PLT-026 (docs/16 §5.13, §14): bilingual banner editor. English and Telugu title and
 * message are both required; times are entered in IST and stored in UTC. While Telugu is
 * switched off (ADR-0036) only the English title and message are shown.
 */
export function AnnouncementEditor() {
  const t = useTranslations("platform.announcements");
  const tc = useTranslations("common");
  const api = useBffClient("operator");
  const [saved, setSaved] = useState(false);
  // A new round of empty fields after each save (also resets the audience choice).
  const [round, setRound] = useState(0);
  const telugu = useTeluguEnabled();

  const form = useApiForm({
    schema: announcementSchemaFor(telugu),
    extra: tenantIds,
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
      setRound((value) => value + 1);
      setSaved(true);
    },
  });

  return (
    <form noValidate onSubmit={form.onSubmit} className="space-y-4">
      <AnnouncementFields key={round} errors={form.errors} />
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

/**
 * The edit dialog's fields. Mounted when the dialog opens, so the version (and every starting
 * value) is the one the operator saw then, even if the list refreshes behind the dialog.
 */
function EditFields({ row, errors }: { row: Announcement; errors: FieldErrors }) {
  const [opened] = useState(row);
  return (
    <>
      <input type="hidden" name="version" value={opened.version} readOnly />
      <AnnouncementFields errors={errors} initial={opened} />
    </>
  );
}

/**
 * Edit an announcement that is not cancelled (PATCH, `platform.announcements.manage`,
 * FR-PLT-026). The whole banner is sent again with `If-Match`: the API replaces it. Someone
 * else's change first (412) or a cancellation meanwhile (409) refreshes the list.
 */
export function AnnouncementEditDialog({
  row,
  onSaved,
}: {
  row: Announcement;
  onSaved?: () => void;
}) {
  const t = useTranslations("platform.announcements");
  const api = useBffClient("operator");
  const queryClient = useQueryClient();
  const telugu = useTeluguEnabled();

  return (
    <ActionDialog
      triggerLabel={t("edit")}
      triggerSize="sm"
      triggerVariant="secondary"
      triggerDescription={row.title_en}
      title={t("editTitle")}
      description={t("editBody")}
      confirmLabel={t("editSave")}
      schema={announcementEditSchemaFor(telugu)}
      extra={tenantIds}
      invalidate={[PK.announcements]}
      onSuccess={() => onSaved?.()}
      submit={async ({ version, ...body }) => {
        try {
          return await unwrap(
            api.PATCH("/api/v1/platform/announcements/{announcement_id}", {
              params: {
                path: { announcement_id: row.id },
                header: { "If-Match": ifMatch(version) },
              },
              body,
            }),
          );
        } catch (failure) {
          if (failure instanceof ApiError && (failure.status === 409 || failure.status === 412)) {
            void queryClient.invalidateQueries({ queryKey: PK.announcements });
          }
          throw failure;
        }
      }}
    >
      {(errors) => <EditFields row={row} errors={errors} />}
    </ActionDialog>
  );
}

"use client";

import type { TenantProfile } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { known, schoolTone } from "@/features/status";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatInr } from "@/lib/format";
import { formList, useApiForm } from "@/lib/forms";
import type { Locale } from "@/i18n/routing";
import {
  DATE_FORMATS,
  LANGUAGES,
  SETTINGS_MANAGE,
  TENANT_KEY,
  changedSettings,
  ifMatch,
  settingsSchema,
  type SchoolSettings,
} from "./data";

/**
 * School profile and settings (FR-TEN-012). Everyone in the school can read them; holders of
 * `tenant.settings.manage` can change languages, date format, idle timeout and the AI switch
 * and budget. Saving sends only what changed, with If-Match (412 → reload) and may ask for
 * step-up (428 is handled globally). Name, code, boards and state are set by SchoolOS when
 * the school is provisioned: the API offers no school-side edit for them.
 */
export function SchoolSettingsScreen() {
  const t = useTranslations("schoolSettings");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const manage = can(SETTINGS_MANAGE);
  const tenant = useApiQuery(TENANT_KEY, () => unwrap(api.GET("/api/v1/tenant")));

  let body: ReactNode;
  if (tenant.status === "loading") body = <LoadingState label={tc("loading")} />;
  else if (tenant.status === "unavailable") {
    body = (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  } else if (tenant.status === "error") {
    body = (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {tenant.reason ? te(`load.${tenant.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  } else {
    body = (
      <>
        <ProfileCard tenant={tenant.data} />
        <Card title={t("settings.title")} description={t("settings.description")}>
          {manage ? (
            <SettingsEditor tenant={tenant.data} />
          ) : (
            <>
              {meLoaded ? (
                <Alert tone="info" className="mb-4">
                  {t("readOnlyNote")}
                </Alert>
              ) : null}
              <SettingsList settings={tenant.data.settings} />
            </>
          )}
        </Card>
      </>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      {body}
    </div>
  );
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-1 py-2 sm:grid-cols-[14rem_1fr] sm:gap-4">
      <dt className="font-semibold text-ink">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  );
}

function ProfileCard({ tenant }: { tenant: TenantProfile }) {
  const t = useTranslations("schoolSettings.profile");
  const tstatus = useTranslations("status.school");
  const tmode = useTranslations("deploymentMode");
  const status = known(schoolTone, tenant.status);
  return (
    <Card title={t("title")} description={t("description")}>
      <dl className="divide-y divide-border">
        <Row label={t("name")}>{tenant.name}</Row>
        <Row label={t("code")}>{tenant.code}</Row>
        <Row label={t("boards")}>
          <Value>{tenant.boards.join(", ")}</Value>
        </Row>
        <Row label={t("stateCode")}>{tenant.state_code}</Row>
        <Row label={t("status")}>
          {status ? <Badge tone={schoolTone[status]}>{tstatus(status)}</Badge> : tenant.status}
        </Row>
        <Row label={t("tier")}>{tmode(tenant.plan_tier)}</Row>
      </dl>
    </Card>
  );
}

function useSettingsText() {
  const t = useTranslations("schoolSettings");
  const tl = useTranslations("language");
  const locale = useLocale() as Locale;
  return {
    languages: (settings: SchoolSettings) =>
      (settings.languages ?? []).map((code) => tl(code)).join(", "),
    idle: (minutes: number) => t("minutes", { count: minutes }),
    ai: (on: boolean) => (on ? t("aiOn") : t("aiOff")),
    budget: (amount: number) => formatInr(amount, locale),
  };
}

function SettingsList({ settings }: { settings: SchoolSettings }) {
  const t = useTranslations("schoolSettings.form");
  const text = useSettingsText();
  return (
    <dl className="divide-y divide-border">
      <Row label={t("languagesField")}>
        <Value>{text.languages(settings)}</Value>
      </Row>
      <Row label={t("dateFormatField")}>
        <Value>{settings.date_format}</Value>
      </Row>
      <Row label={t("idleField")}>
        <Value>
          {settings.idle_timeout_minutes === undefined
            ? null
            : text.idle(settings.idle_timeout_minutes)}
        </Value>
      </Row>
      <Row label={t("aiField")}>
        <Value>
          {settings.ai_features_enabled === undefined
            ? null
            : text.ai(settings.ai_features_enabled)}
        </Value>
      </Row>
      <Row label={t("budgetField")}>
        <Value>
          {settings.ai_monthly_budget_inr === undefined
            ? null
            : text.budget(settings.ai_monthly_budget_inr)}
        </Value>
      </Row>
    </dl>
  );
}

/** Remounts the form after "load the latest", so every field shows the reloaded values. */
function SettingsEditor({ tenant }: { tenant: TenantProfile }) {
  const [generation, setGeneration] = useState(0);
  // Kept here so "Settings saved" survives the remount after the saved version reloads.
  const [outcome, setOutcome] = useState<Outcome>(null);
  const queryClient = useQueryClient();
  const reload = async () => {
    setOutcome(null);
    await queryClient.invalidateQueries({ queryKey: TENANT_KEY });
    setGeneration((value) => value + 1);
  };
  return (
    <SettingsForm
      key={`${tenant.version}:${generation}`}
      tenant={tenant}
      onReload={reload}
      outcome={outcome}
      setOutcome={setOutcome}
    />
  );
}

type Outcome = "saved" | "unchanged" | null;

function SettingsForm({
  tenant,
  onReload,
  outcome,
  setOutcome,
}: {
  tenant: TenantProfile;
  onReload: () => Promise<void>;
  outcome: Outcome;
  setOutcome: (outcome: Outcome) => void;
}) {
  const t = useTranslations("schoolSettings");
  const tc = useTranslations("common");
  const tl = useTranslations("language");
  const api = useBffClient("staff");
  const languagesId = useId();
  const dateId = useId();
  const aiId = useId();
  const [reloading, setReloading] = useState(false);
  const settings = tenant.settings;

  const form = useApiForm({
    schema: settingsSchema,
    extra: (element) => ({ languages: formList(element, "languages") }),
    invalidate: [TENANT_KEY],
    submit: async (data): Promise<TenantProfile | null> => {
      setOutcome(null);
      const patch = changedSettings(settings, data);
      if (Object.keys(patch).length === 0) return null;
      return unwrap(
        api.PATCH("/api/v1/tenant", {
          headers: { "If-Match": ifMatch(tenant.version) },
          body: patch,
        }),
      );
    },
    onSuccess: (result) => setOutcome(result === null ? "unchanged" : "saved"),
  });

  const stale = form.error instanceof ApiError && form.error.status === 412;
  const errors = form.errors;
  const languagesError = errors.languages;
  const dateError = errors.date_format;

  return (
    <form noValidate onSubmit={form.onSubmit} className="max-w-2xl space-y-5">
      <fieldset
        className="space-y-2"
        aria-describedby={languagesError ? `${languagesId}-error` : `${languagesId}-hint`}
      >
        <legend className="text-sm font-semibold text-ink">{t("form.languagesField")}</legend>
        <p id={`${languagesId}-hint`} className="text-sm text-ink-muted">
          {t("form.languagesHint")}
        </p>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {LANGUAGES.map((code) => (
            <label key={code} className="inline-flex min-h-8 items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="languages"
                value={code}
                defaultChecked={(settings.languages ?? []).includes(code)}
                aria-invalid={languagesError ? true : undefined}
                className="size-4 accent-primary"
              />
              <span lang={code}>{tl(code)}</span>
            </label>
          ))}
        </div>
        {languagesError ? (
          <p id={`${languagesId}-error`} className="text-sm font-semibold text-danger">
            {languagesError}
          </p>
        ) : null}
      </fieldset>

      <fieldset className="space-y-2" aria-describedby={dateError ? `${dateId}-error` : undefined}>
        <legend className="text-sm font-semibold text-ink">{t("form.dateFormatField")}</legend>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {DATE_FORMATS.map((format) => (
            <label key={format} className="inline-flex min-h-8 items-center gap-2 text-sm">
              <input
                type="radio"
                name="date_format"
                value={format}
                defaultChecked={settings.date_format === format}
                className="size-4 accent-primary"
              />
              <span>{format}</span>
            </label>
          ))}
        </div>
        {dateError ? (
          <p id={`${dateId}-error`} className="text-sm font-semibold text-danger">
            {dateError}
          </p>
        ) : null}
      </fieldset>

      <TextField
        name="idle_timeout_minutes"
        label={t("form.idleField")}
        hint={t("form.idleHint")}
        error={errors.idle_timeout_minutes}
        defaultValue={String(settings.idle_timeout_minutes ?? 15)}
        inputMode="numeric"
        maxLength={2}
        className="max-w-48"
        required
      />

      <div className="space-y-1">
        <label className="flex items-center gap-2 text-sm font-semibold">
          <input
            type="checkbox"
            name="ai_features_enabled"
            defaultChecked={settings.ai_features_enabled ?? true}
            aria-describedby={`${aiId}-hint`}
            className="size-4 accent-primary"
          />
          {t("form.aiField")}
        </label>
        <p id={`${aiId}-hint`} className="text-sm text-ink-muted">
          {t("form.aiHint")}
        </p>
      </div>

      <TextField
        name="ai_monthly_budget_inr"
        label={t("form.budgetField")}
        hint={t("form.budgetHint")}
        error={errors.ai_monthly_budget_inr}
        defaultValue={String(settings.ai_monthly_budget_inr ?? 5000)}
        inputMode="numeric"
        maxLength={8}
        className="max-w-48"
        required
      />

      <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
      <ApiErrorAlert error={form.error} namespace="schoolSettings" />
      {stale ? (
        <Button
          variant="secondary"
          disabled={reloading}
          onClick={() => {
            setReloading(true);
            void onReload().finally(() => setReloading(false));
          }}
        >
          {t("form.reload")}
        </Button>
      ) : null}
      {outcome === "saved" ? (
        <Alert tone="success" live>
          {t("form.saved")}
        </Alert>
      ) : null}
      {outcome === "unchanged" ? (
        <Alert tone="info" live>
          {t("form.nothingChanged")}
        </Alert>
      ) : null}
      <div>
        <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
          {form.pending ? tc("working") : t("form.save")}
        </Button>
      </div>
    </form>
  );
}

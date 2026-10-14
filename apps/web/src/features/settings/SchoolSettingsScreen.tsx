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
import { Toggle } from "@/components/ui/Toggle";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { LetterheadCard } from "@/features/certificates/LetterheadCard";
import { BoardsCard } from "./BoardsCard";
import { known, schoolTone } from "@/features/status";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { refreshSessionInfo } from "@/lib/bff/session-client";
import { STAFF_ME_KEY, useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatInr } from "@/lib/format";
import { formList, useApiForm } from "@/lib/forms";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
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
 * step-up (428 is handled globally). Name, code and state are set by SchoolOS when the school
 * is provisioned; the boards and the operating mode are chosen in the boards card (US-203).
 */
export function SchoolSettingsScreen() {
  const t = useTranslations("schoolSettings");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tn = useTranslations("school.nav");
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
        {manage ? (
          <SettingsEditor tenant={tenant.data} />
        ) : (
          <Card title={t("settings.title")} description={t("settings.description")}>
            {meLoaded ? (
              <Alert tone="info" className="mb-4">
                {t("readOnlyNote")}
              </Alert>
            ) : null}
            <SettingsList settings={tenant.data.settings} />
          </Card>
        )}
        {/* US-203, US-204 (FR-TEN-020..022): boards, class boards and the operating mode. */}
        <BoardsCard
          key={`boards-${tenant.data.version}`}
          tenant={tenant.data}
          manage={manage}
          tenantKey={TENANT_KEY}
        />
        {/* US-1108, FR-CERT-013: what certificates print at the top and at the signature. */}
        <LetterheadCard
          key={tenant.data.version}
          tenant={tenant.data}
          manage={manage}
          tenantKey={TENANT_KEY}
        />
      </>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {body}
    </div>
  );
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr] sm:gap-4">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  );
}

/** One fact of the school profile: small muted label over the value. */
function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1 rounded-lg border border-border bg-surface-muted px-4 py-3">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="font-semibold break-words text-ink">{children}</dd>
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
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Fact label={t("name")}>{tenant.name}</Fact>
        <Fact label={t("code")}>
          <span className="font-mono text-sm">{tenant.code}</span>
        </Fact>
        <Fact label={t("stateCode")}>{tenant.state_code}</Fact>
        <Fact label={t("status")}>
          {status ? <Badge tone={schoolTone[status]}>{tstatus(status)}</Badge> : tenant.status}
        </Fact>
        <Fact label={t("tier")}>{tmode(tenant.plan_tier)}</Fact>
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
  const telugu = useTeluguEnabled();
  return (
    <dl className="divide-y divide-border">
      {/* ADR-0036: English is the only language while Telugu is switched off. */}
      {telugu ? (
        <Row label={t("languagesField")}>
          <Value>{text.languages(settings)}</Value>
        </Row>
      ) : null}
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
      <Row label={t("memoryField")}>
        <Value>
          {settings.ai_memory_enabled === undefined ? null : text.ai(settings.ai_memory_enabled)}
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
  const saveId = useId();
  const [reloading, setReloading] = useState(false);
  const settings = tenant.settings;
  const queryClient = useQueryClient();
  const telugu = useTeluguEnabled();

  /**
   * A saved setting applies without a reload (FR-TEN-012): GET /me through the BFF sets the
   * session's idle timeout from the school's settings (and the date format for the screens),
   * then the idle warning re-reads the session facts. The server enforces the timeout on
   * every request whether or not this runs.
   */
  const applyToSession = async () => {
    try {
      await queryClient.invalidateQueries({ queryKey: STAFF_ME_KEY });
      await refreshSessionInfo("staff");
    } catch {
      // The next page load applies it anyway.
    }
  };

  const form = useApiForm({
    schema: settingsSchema,
    // ADR-0036: while Telugu is switched off the languages are not asked for; the stored
    // choice is kept (English when none is stored).
    extra: (element) => ({
      languages: telugu
        ? formList(element, "languages")
        : settings.languages?.length
          ? [...settings.languages]
          : ["en"],
    }),
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
    onSuccess: (result) => {
      setOutcome(result === null ? "unchanged" : "saved");
      if (result !== null) void applyToSession();
    },
  });

  const stale = form.error instanceof ApiError && form.error.status === 412;
  const errors = form.errors;
  const languagesError = errors.languages;
  const dateError = errors.date_format;

  // Checkbox and radio "chips": the native input stays (keyboard, form value); the label
  // around it gets a soft outline, and a blue fill when checked (`:has()`, Baseline).
  const chip =
    "inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-md border border-border-soft bg-surface px-3 text-sm has-checked:border-primary has-checked:bg-primary-soft";

  return (
    <form noValidate onSubmit={form.onSubmit} className="space-y-6">
      <Card title={t("groups.languages.title")} description={t("groups.languages.description")}>
        <div className="space-y-6">
          {telugu ? (
            <fieldset
              className="space-y-2"
              aria-describedby={languagesError ? `${languagesId}-error` : `${languagesId}-hint`}
            >
              <legend className="text-sm font-semibold text-ink">{t("form.languagesField")}</legend>
              <p id={`${languagesId}-hint`} className="text-sm text-ink-muted">
                {t("form.languagesHint")}
              </p>
              <div className="flex flex-wrap gap-2">
                {LANGUAGES.map((code) => (
                  <label key={code} className={chip}>
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
          ) : null}

          <fieldset
            className="space-y-2"
            aria-describedby={dateError ? `${dateId}-error` : undefined}
          >
            <legend className="text-sm font-semibold text-ink">{t("form.dateFormatField")}</legend>
            <div className="flex flex-wrap gap-2">
              {DATE_FORMATS.map((format) => (
                <label key={format} className={chip}>
                  <input
                    type="radio"
                    name="date_format"
                    value={format}
                    defaultChecked={settings.date_format === format}
                    className="size-4 accent-primary"
                  />
                  <span className="font-mono">{format}</span>
                </label>
              ))}
            </div>
            {dateError ? (
              <p id={`${dateId}-error`} className="text-sm font-semibold text-danger">
                {dateError}
              </p>
            ) : null}
          </fieldset>
        </div>
      </Card>

      <Card title={t("groups.session.title")} description={t("groups.session.description")}>
        <TextField
          name="idle_timeout_minutes"
          label={t("form.idleField")}
          hint={t("form.idleHint")}
          error={errors.idle_timeout_minutes}
          defaultValue={String(settings.idle_timeout_minutes ?? 15)}
          inputMode="numeric"
          maxLength={2}
          className="max-w-sm"
          required
        />
      </Card>

      <Card title={t("groups.ai.title")} description={t("groups.ai.description")}>
        <div className="space-y-6">
          <div className="max-w-xl rounded-lg border border-border bg-surface-muted p-4">
            {/* Submitted with the form ("on"/"off"); it applies when the settings are saved. */}
            <Toggle
              name="ai_features_enabled"
              label={t("form.aiToggle")}
              description={t("form.aiHint")}
              defaultChecked={settings.ai_features_enabled ?? true}
              labelFirst
            />
          </div>
          <div className="max-w-xl rounded-lg border border-border bg-surface-muted p-4">
            {/* Ask memory for everyone (ADR-0034); each person can also turn their own off. */}
            <Toggle
              name="ai_memory_enabled"
              label={t("form.memoryToggle")}
              description={t("form.memoryHint")}
              defaultChecked={settings.ai_memory_enabled ?? true}
              labelFirst
            />
          </div>
          <TextField
            name="ai_monthly_budget_inr"
            label={t("form.budgetField")}
            hint={t("form.budgetHint")}
            error={errors.ai_monthly_budget_inr}
            defaultValue={String(settings.ai_monthly_budget_inr ?? 5000)}
            inputMode="numeric"
            maxLength={8}
            className="max-w-sm"
            required
          />
        </div>
      </Card>

      <section
        aria-labelledby={`${saveId}-title`}
        className="space-y-4 rounded-xl border border-border bg-surface p-5 shadow-card md:p-6 print:hidden"
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <h2 id={`${saveId}-title`} className="font-semibold text-ink">
              {t("saveBar.title")}
            </h2>
            <p className="text-sm text-ink-muted">{t("saveBar.body")}</p>
            <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
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
            <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
              {form.pending ? tc("working") : t("form.save")}
            </Button>
          </div>
        </div>
        <ApiErrorAlert error={form.error} namespace="schoolSettings" />
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
      </section>
    </form>
  );
}

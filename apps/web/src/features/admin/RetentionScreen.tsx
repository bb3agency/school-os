"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useMemo, useState, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import { useApiForm } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  ADMIN_KEYS,
  SETTINGS_MANAGE,
  ifMatch,
  orderedCategories,
  retentionRules,
  retentionSchema,
  sameRetention,
  useRetention,
  type RetentionCategory,
  type RetentionSettings,
} from "./data";

type Outcome = "saved" | "unchanged" | "defaults" | null;

function CategoryName({ category }: { category: RetentionCategory }) {
  const t = useTranslations("admin.retention.categories");
  return <>{translateOr(t, `${category.key}.name`, "other.name")}</>;
}

function CategoryAbout({ category }: { category: RetentionCategory }) {
  const t = useTranslations("admin.retention.categories");
  return <>{translateOr(t, `${category.key}.about`, "other.about")}</>;
}

/** A category SchoolOS fixes (law, security or a feature): shown, never editable here. */
function FixedRow({ category }: { category: RetentionCategory }) {
  const t = useTranslations("admin.retention");
  return (
    <li className="grid gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start sm:gap-6">
      <div className="min-w-0 space-y-1">
        <p className="font-semibold text-ink">
          <CategoryName category={category} />
        </p>
        <p className="text-sm text-ink-muted">
          <CategoryAbout category={category} />
        </p>
        {!category.enforced ? <p className="text-sm text-ink-muted">{t("notEnforced")}</p> : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-ink">{t("days", { count: category.days })}</span>
        <Pill variant="tag">{t("fixed")}</Pill>
      </div>
    </li>
  );
}

function RetentionForm({
  settings,
  onReload,
  outcome,
  setOutcome,
}: {
  settings: RetentionSettings;
  onReload: () => Promise<void>;
  outcome: Outcome;
  setOutcome: (outcome: Outcome) => void;
}) {
  const t = useTranslations("admin.retention");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const saveId = useId();
  const [reloading, setReloading] = useState(false);
  const [resetError, setResetError] = useState<unknown>(undefined);
  const [resetting, setResetting] = useState(false);
  const categories = useMemo(() => orderedCategories(settings.categories), [settings.categories]);
  const editable = categories.filter((c) => c.configurable);
  const fixed = categories.filter((c) => !c.configurable);
  const schema = useMemo(() => retentionSchema(categories), [categories]);

  const form = useApiForm({
    schema,
    invalidate: [ADMIN_KEYS.retention],
    submit: async (data): Promise<RetentionSettings | null> => {
      setOutcome(null);
      const values = data as Record<string, number>;
      if (sameRetention(categories, values)) return null;
      return unwrap(
        api.PUT("/api/v1/admin/retention", {
          headers: { "If-Match": ifMatch(settings.version) },
          body: { rules: retentionRules(categories, values) },
        }),
      );
    },
    onSuccess: (result) => setOutcome(result === null ? "unchanged" : "saved"),
  });

  async function restoreDefaults() {
    setOutcome(null);
    setResetError(undefined);
    setResetting(true);
    try {
      await unwrap(
        api.PUT("/api/v1/admin/retention", {
          headers: { "If-Match": ifMatch(settings.version) },
          body: { rules: {} },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ADMIN_KEYS.retention });
      setOutcome("defaults");
    } catch (failure) {
      setResetError(failure);
    } finally {
      setResetting(false);
    }
  }

  const stale =
    (form.error instanceof ApiError && form.error.status === 412) ||
    (resetError instanceof ApiError && resetError.status === 412);
  const allDefault = categories.every((c) => c.is_default);

  return (
    <form noValidate onSubmit={form.onSubmit} className="space-y-6">
      <Card title={t("editableTitle")} description={t("editableDescription")}>
        <ul className="divide-y divide-border">
          {editable.map((category) => (
            <li key={category.key} className="space-y-2 py-4">
              {/* The label is the category (unique per field); the hint says what and how long. */}
              <TextField
                name={category.key}
                label={<CategoryName category={category} />}
                hint={
                  <>
                    <CategoryAbout category={category} />{" "}
                    {t("range", {
                      min: category.min_days,
                      max: category.max_days,
                      default: category.default_days,
                    })}
                  </>
                }
                error={form.errors[category.key]}
                defaultValue={String(category.days)}
                inputMode="numeric"
                maxLength={4}
                className="max-w-xl"
                required
              />
              {!category.is_default ? <Pill variant="tag">{t("changed")}</Pill> : null}
            </li>
          ))}
        </ul>
      </Card>

      <Card title={t("fixedTitle")} description={t("fixedDescription")}>
        <ul className="divide-y divide-border">
          {fixed.map((category) => (
            <FixedRow key={category.key} category={category} />
          ))}
        </ul>
        <p className="mt-2 text-sm text-ink-muted">{t("recordsNote")}</p>
      </Card>

      <section
        aria-labelledby={`${saveId}-title`}
        className="space-y-4 rounded-xl border border-border bg-surface p-5 shadow-card md:p-6 print:hidden"
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <h2 id={`${saveId}-title`} className="font-semibold text-ink">
              {t("saveTitle")}
            </h2>
            <p className="text-sm text-ink-muted">{t("saveBody")}</p>
            <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
            {settings.updated_at ? (
              <p className="text-sm text-ink-muted">
                {t("lastChanged", {
                  when: formatDateTime(settings.updated_at) ?? "",
                  who: settings.updated_by?.display_name ?? t("formerStaff"),
                })}
              </p>
            ) : null}
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
                {t("reload")}
              </Button>
            ) : null}
            {!allDefault ? (
              <Button
                variant="secondary"
                disabled={resetting || form.pending}
                aria-disabled={resetting || form.pending || undefined}
                onClick={() => void restoreDefaults()}
              >
                {resetting ? tc("working") : t("restoreDefaults")}
              </Button>
            ) : null}
            <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
              {form.pending ? tc("working") : t("save")}
            </Button>
          </div>
        </div>
        <ApiErrorAlert error={form.error ?? resetError} namespace="admin" />
        {outcome ? (
          <Alert tone={outcome === "unchanged" ? "info" : "success"} live>
            {t(`outcome.${outcome}`)}
          </Alert>
        ) : null}
      </section>
    </form>
  );
}

/** Remounts the form after a save or "load the latest", so the fields show saved values. */
function RetentionEditor({ settings }: { settings: RetentionSettings }) {
  const [generation, setGeneration] = useState(0);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const queryClient = useQueryClient();
  const reload = async () => {
    setOutcome(null);
    await queryClient.invalidateQueries({ queryKey: ADMIN_KEYS.retention });
    setGeneration((value) => value + 1);
  };
  return (
    <RetentionForm
      key={`${settings.version}:${generation}`}
      settings={settings}
      onReload={reload}
      outcome={outcome}
      setOutcome={setOutcome}
    />
  );
}

/**
 * Retention settings (US-1201, FR-ADM-002): how long the school keeps each kind of working
 * data, within the bounds SchoolOS allows; fixed categories (audit log, the full export) are
 * shown with their period. Holders of `tenant.settings.manage` (owner, principal) change them
 * with a fresh MFA sign-in; the change is recorded in the audit log.
 */
export function RetentionScreen() {
  const t = useTranslations("admin.retention");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(SETTINGS_MANAGE);
  const retention = useRetention(allowed);

  let body: ReactNode;
  if (!meLoaded) body = <LoadingState label={tc("loading")} />;
  else if (!allowed) {
    body = (
      <Alert tone="info" title={t("notAllowedTitle")}>
        {t("notAllowedBody")}
      </Alert>
    );
  } else if (retention.status === "loading") body = <LoadingState label={tc("loading")} />;
  else if (retention.status === "unavailable") {
    body = (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  } else if (retention.status === "error") {
    body = (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {retention.reason ? te(`load.${retention.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  } else {
    body = <RetentionEditor settings={retention.data} />;
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

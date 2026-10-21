"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { newIdempotencyKey, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount } from "@/lib/format";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import type { Loadable } from "@/lib/loadable";
import type { Locale } from "@/i18n/routing";
import {
  CONSENT_KEYS,
  CONSENT_READ,
  CONSENT_RECORD,
  SETTINGS_MANAGE,
  ifMatch,
  type Purpose,
  type PurposeList,
  type Suggestion,
} from "./types";

/** Purpose text as the API checks it (consents_schemas.py): label 2-120, description 10-1000. */
const labelEn = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(2, { error: "tooShort" })
  .max(120, { error: "tooLong" });
const labelTe = z.string().trim().max(120, { error: "tooLong" }).default("");
const description = z
  .string()
  .trim()
  .min(1, { error: "required" })
  .min(10, { error: "descriptionTooShort" })
  .max(1000, { error: "tooLong" });

export const purposeTextSchema = z.object({ label_en: labelEn, label_te: labelTe, description });

/** The school's own purpose: a short code the forms and exports use, never changed later. */
export const newPurposeSchema = purposeTextSchema.extend({
  key: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .regex(/^[a-z][a-z0-9_]{2,40}$/, { error: "purposeKey" }),
});

/** "This makes version N+1 of the form" applies only when families answered the current one. */
export function versionWarning(
  purpose: Pick<Purpose, "current_version_answered" | "form_version">,
) {
  return purpose.current_version_answered
    ? { next: purpose.form_version + 1, current: purpose.form_version }
    : null;
}

/**
 * Consent purposes (US-2601, FR-DPDP-020, ADR-0049): the optional things the school asks
 * families to agree to (photos on the website, the school's own WhatsApp group…). Suggested
 * purposes are a checklist; the school may add its own. Owners and principals
 * (`tenant.settings.manage`) change them with a fresh MFA sign-in; staff who record answers
 * see them read-only. Changing the text of a purpose that families answered makes the next
 * version of the form; earlier answers stay with the version they answered.
 */
export function PurposesScreen() {
  const t = useTranslations("consents.purposes");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const manage = can(SETTINGS_MANAGE);
  const allowed = can([CONSENT_READ, CONSENT_RECORD, SETTINGS_MANAGE]);
  const list = useQuery({
    queryKey: CONSENT_KEYS.purposes,
    queryFn: () => unwrap(api.GET("/api/v1/consent-purposes")),
    enabled: allowed,
    retry: false,
  });

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={manage ? <AddPurposeDialog /> : undefined}
    />
  );

  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {header}
      {!manage ? (
        <Alert tone="info" title={t("readOnlyTitle")}>
          {t("readOnlyBody")}
        </Alert>
      ) : null}
      {list.data ? <Suggestions data={list.data} manage={manage} /> : null}
      <section aria-labelledby="consent-purposes-list" className="space-y-3">
        <h2 id="consent-purposes-list" className="text-lg font-semibold text-ink">
          {t("listTitle")}
        </h2>
        <PurposeTable
          state={toLoadable({ ...list, data: list.data?.data ?? [] })}
          manage={manage}
        />
      </section>
    </div>
  );
}

/** Suggested purposes as a checklist: in use (ticked) or "Use this purpose". */
function Suggestions({ data, manage }: { data: PurposeList; manage: boolean }) {
  const t = useTranslations("consents.purposes");
  if (data.suggestions.length === 0) return null;
  return (
    <Card title={t("suggestionsTitle")} description={t("suggestionsDescription")}>
      <ul className="divide-y divide-border">
        {data.suggestions.map((suggestion) => (
          <SuggestionRow key={suggestion.key} suggestion={suggestion} manage={manage} />
        ))}
      </ul>
    </Card>
  );
}

function SuggestionRow({ suggestion, manage }: { suggestion: Suggestion; manage: boolean }) {
  const t = useTranslations("consents.purposes");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [key] = useState(newIdempotencyKey);

  async function enable() {
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.POST("/api/v1/consent-purposes", {
          headers: { "Idempotency-Key": key },
          body: { suggested_key: suggestion.key },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: CONSENT_KEYS.all });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <li className="grid gap-2 py-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-start sm:gap-4">
      <span
        aria-hidden="true"
        className={
          suggestion.enabled
            ? "mt-0.5 hidden size-5 items-center justify-center rounded-sm bg-success-soft text-success-ink sm:inline-flex"
            : "mt-0.5 hidden size-5 rounded-sm border border-border-strong sm:inline-block"
        }
      >
        {suggestion.enabled ? <Icon name="check" className="size-4" /> : null}
      </span>
      <div className="min-w-0 space-y-1">
        <p className="font-semibold text-ink break-anywhere">{suggestion.label_en}</p>
        <p className="text-sm text-ink-muted">{suggestion.description}</p>
        <ApiErrorAlert error={error} namespace="consents" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {suggestion.enabled ? (
          <Pill variant="done" size="sm">
            {t("inUse")}
          </Pill>
        ) : manage ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void enable()}
            disabled={pending}
            aria-disabled={pending || undefined}
            loading={pending}
          >
            {pending ? tc("working") : t("enable")}
            <span className="sr-only">: {suggestion.label_en}</span>
          </Button>
        ) : (
          <Pill variant="tag" size="sm" icon={null}>
            {t("notInUse")}
          </Pill>
        )}
      </div>
    </li>
  );
}

function PurposeTable({ state, manage }: { state: Loadable<Purpose[]>; manage: boolean }) {
  const t = useTranslations("consents.purposes");
  const locale = useLocale() as Locale;
  const columns: ColumnV2<Purpose>[] = [
    {
      key: "purpose",
      stack: "title",
      header: t("colPurpose"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold text-ink break-anywhere">{row.label_en}</span>
          <span className="font-mono text-xs text-ink-muted">{row.key}</span>
        </span>
      ),
    },
    {
      key: "about",
      stack: "wide",
      header: t("colAbout"),
      cell: (row) => <span className="block max-w-prose text-sm">{row.description}</span>,
    },
    {
      key: "version",
      header: t("colVersion"),
      numeric: true,
      cell: (row) => formatCount(row.form_version, locale),
    },
    {
      key: "answered",
      header: t("colAnswered"),
      numeric: true,
      cell: (row) => formatCount(row.answered, locale),
    },
    {
      key: "state",
      header: t("colState"),
      cell: (row) =>
        row.active ? (
          <Pill variant="done" size="sm">
            {t("active")}
          </Pill>
        ) : (
          <Pill variant="tag" size="sm" icon={null}>
            {t("inactive")}
          </Pill>
        ),
    },
    ...(manage
      ? [
          {
            key: "actions",
            stack: "actions" as const,
            header: t("colActions"),
            cell: (row: Purpose) => (
              <span className="relative z-[1] flex flex-wrap gap-2">
                <EditPurposeDialog purpose={row} />
                <SwitchPurposeDialog purpose={row} />
              </span>
            ),
          },
        ]
      : []),
  ];
  return (
    <DataTableV2
      id="consent-purposes"
      stacked
      caption={t("listTitle")}
      captionHidden
      columns={columns}
      state={state}
      rowKey={(row) => row.id}
      emptyTitle={t("emptyTitle")}
      emptyBody={manage ? t("emptyBody") : t("emptyBodyReadOnly")}
    />
  );
}

function AddPurposeDialog() {
  const t = useTranslations("consents.purposes");
  const telugu = useTeluguEnabled();
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("add")}
      triggerVariant="primary"
      title={t("addTitle")}
      description={t("addBody")}
      confirmLabel={t("addConfirm")}
      stepUp
      schema={newPurposeSchema}
      invalidate={[CONSENT_KEYS.all]}
      errorNamespace="consents"
      submit={(data, idempotencyKey) =>
        unwrap(
          api.POST("/api/v1/consent-purposes", {
            headers: { "Idempotency-Key": idempotencyKey },
            body: {
              key: data.key,
              label_en: data.label_en,
              label_te: telugu && data.label_te ? data.label_te : null,
              description: data.description,
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <TextField
            name="label_en"
            label={t("labelEn")}
            hint={t("labelEnHint")}
            error={errors.label_en}
            maxLength={120}
            autoComplete="off"
          />
          <TextField
            name="key"
            label={t("key")}
            hint={t("keyHint")}
            error={errors.key}
            maxLength={41}
            autoComplete="off"
            spellCheck={false}
            className="max-w-sm"
          />
          <TextAreaField
            name="description"
            label={t("descriptionLabel")}
            hint={t("descriptionHint")}
            error={errors.description}
            maxLength={1000}
            rows={4}
          />
          {telugu ? (
            <TextField
              name="label_te"
              label={t("label_te")}
              error={errors.label_te}
              maxLength={120}
              lang="te"
              autoComplete="off"
            />
          ) : null}
        </>
      )}
    </ActionDialog>
  );
}

function EditPurposeDialog({ purpose }: { purpose: Purpose }) {
  const t = useTranslations("consents.purposes");
  const telugu = useTeluguEnabled();
  const api = useBffClient("staff");
  const warning = versionWarning(purpose);
  return (
    <ActionDialog
      triggerLabel={t("edit")}
      triggerSize="sm"
      triggerVariant="secondary"
      triggerDescription={purpose.label_en}
      title={t("editTitle")}
      description={purpose.label_en}
      confirmLabel={t("saveChanges")}
      stepUp
      schema={purposeTextSchema}
      invalidate={[CONSENT_KEYS.all]}
      errorNamespace="consents"
      {...(warning ? { consequence: t("versionWarning", warning) } : {})}
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/consent-purposes/{purpose_id}", {
            params: { path: { purpose_id: purpose.id } },
            headers: { "If-Match": ifMatch(purpose.version) },
            body: {
              label_en: data.label_en,
              description: data.description,
              // With Telugu on, an emptied Telugu name is cleared; with it off the field is not
              // shown and the stored Telugu name is kept as it is (ADR-0036).
              ...(telugu && data.label_te ? { label_te: data.label_te } : {}),
              clear_label_te: telugu && !data.label_te && Boolean(purpose.label_te),
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          {warning ? (
            <Alert tone="warning" title={t("versionWarningTitle")}>
              {t("versionWarning", warning)}
            </Alert>
          ) : null}
          <TextField
            name="label_en"
            label={t("labelEn")}
            hint={t("labelEnHint")}
            error={errors.label_en}
            defaultValue={purpose.label_en}
            maxLength={120}
            autoComplete="off"
          />
          <TextAreaField
            name="description"
            label={t("descriptionLabel")}
            hint={t("descriptionHint")}
            error={errors.description}
            defaultValue={purpose.description}
            maxLength={1000}
            rows={4}
          />
          {telugu ? (
            <TextField
              name="label_te"
              label={t("label_te")}
              error={errors.label_te}
              defaultValue={purpose.label_te ?? ""}
              maxLength={120}
              lang="te"
              autoComplete="off"
            />
          ) : null}
        </>
      )}
    </ActionDialog>
  );
}

function SwitchPurposeDialog({ purpose }: { purpose: Purpose }) {
  const t = useTranslations("consents.purposes");
  const api = useBffClient("staff");
  const turningOff = purpose.active;
  return (
    <ActionDialog
      triggerLabel={turningOff ? t("switchOff") : t("switchOn")}
      triggerSize="sm"
      triggerVariant="ghost"
      triggerDescription={purpose.label_en}
      title={turningOff ? t("switchOffTitle") : t("switchOnTitle")}
      description={purpose.label_en}
      confirmLabel={turningOff ? t("switchOff") : t("switchOn")}
      confirmVariant={turningOff ? "danger" : "primary"}
      consequence={turningOff ? t("switchOffConsequence") : t("switchOnConsequence")}
      stepUp
      schema={z.object({})}
      invalidate={[CONSENT_KEYS.all]}
      errorNamespace="consents"
      submit={() =>
        unwrap(
          api.PATCH("/api/v1/consent-purposes/{purpose_id}", {
            params: { path: { purpose_id: purpose.id } },
            headers: { "If-Match": ifMatch(purpose.version) },
            body: { active: !turningOff, clear_label_te: false },
          }),
        )
      }
    />
  );
}

"use client";

import type { TenantProfile } from "@schoolos/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { Value } from "@/components/ui/Value";
import { SETTINGS_MANAGE, TENANT_KEY, ifMatch } from "@/features/settings/data";
import { useFormDirty } from "@/features/settings/useFormDirty";
import { Link } from "@/i18n/navigation";
import { ApiError, toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { STAFF_ME_KEY, useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { useApiForm } from "@/lib/forms";
import { optionalEmail, optionalPhone, requiredInt, text } from "@/lib/validation";
import * as z from "@/lib/zod";
import { PRIVACY_KEYS, PRIVACY_READ, type RecordClass } from "./types";

export const TARGET_MIN = 1;
export const TARGET_MAX = 7;

export const privacyContactSchema = z.object({
  name: text(200),
  phone: optionalPhone,
  email: optionalEmail,
  privacy_request_target_days: requiredInt(TARGET_MIN, TARGET_MAX),
});

/**
 * The school's privacy contact and request target (US-2609; FR-DPDP-004, FR-DPDP-006): name or
 * designation, phone, e-mail and the days to answer a request (1-7), saved with If-Match and a
 * fresh sign-in (`tenant.settings.manage`). Below, the record classes read-only: what the school
 * keeps, for how long and whether it can be erased.
 */
export function PrivacySettingsScreen() {
  const t = useTranslations("privacy.settings");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const mePending = useStaffMeQuery().isPending;
  const manage = can(SETTINGS_MANAGE);
  const tenant = useQuery({
    queryKey: TENANT_KEY,
    queryFn: () => unwrap(api.GET("/api/v1/tenant")),
    retry: false,
  });

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {tenant.isPending || mePending ? (
        <LoadingState label={tc("loading")} />
      ) : tenant.isError ? (
        <ApiErrorAlert error={tenant.error} />
      ) : manage ? (
        <ContactEditor tenant={tenant.data} />
      ) : (
        <ContactReadOnly tenant={tenant.data} />
      )}
      {can(PRIVACY_READ) ? <RecordClasses /> : null}
    </div>
  );
}

function ContactReadOnly({ tenant }: { tenant: TenantProfile }) {
  const t = useTranslations("privacy.settings");
  const contact = tenant.settings.privacy_contact ?? null;
  return (
    <Card title={t("contactTitle")} description={t("contactDescription")}>
      <Alert tone="info" className="mb-4">
        {t("readOnly")}
      </Alert>
      <dl className="grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-sm text-ink-muted">{t("name")}</dt>
          <dd>
            <Value>{contact?.name}</Value>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("phone")}</dt>
          <dd className="num">
            <Value>{contact?.phone}</Value>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("email")}</dt>
          <dd className="break-all">
            <Value>{contact?.email}</Value>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("targetDays")}</dt>
          <dd className="num">
            {t("days", { count: tenant.settings.privacy_request_target_days })}
          </dd>
        </div>
      </dl>
    </Card>
  );
}

function ContactEditor({ tenant }: { tenant: TenantProfile }) {
  const [generation, setGeneration] = useState(0);
  const [saved, setSaved] = useState(false);
  const queryClient = useQueryClient();
  return (
    <ContactForm
      key={`${tenant.version}:${generation}`}
      tenant={tenant}
      saved={saved}
      setSaved={setSaved}
      onDiscard={() => {
        setSaved(false);
        setGeneration((value) => value + 1);
      }}
      onReload={async () => {
        setSaved(false);
        await queryClient.invalidateQueries({ queryKey: TENANT_KEY });
        setGeneration((value) => value + 1);
      }}
    />
  );
}

function ContactForm({
  tenant,
  saved,
  setSaved,
  onDiscard,
  onReload,
}: {
  tenant: TenantProfile;
  saved: boolean;
  setSaved: (saved: boolean) => void;
  onDiscard: () => void;
  onReload: () => Promise<void>;
}) {
  const t = useTranslations("privacy.settings");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const base = useId();
  const id = (field: string) => `${base}-${field}`;
  const [attempt, setAttempt] = useState(0);
  const { ref, dirty, onChange } = useFormDirty();
  const contact = tenant.settings.privacy_contact ?? null;

  const form = useApiForm({
    schema: privacyContactSchema,
    invalidate: [TENANT_KEY],
    onInvalid: () => setAttempt((value) => value + 1),
    fieldMap: (field) => field.split(".").pop(),
    submit: (data) =>
      unwrap(
        api.PATCH("/api/v1/tenant", {
          headers: { "If-Match": ifMatch(tenant.version) },
          body: {
            privacy_contact: { name: data.name, phone: data.phone, email: data.email },
            privacy_request_target_days: data.privacy_request_target_days,
          },
        }),
      ),
    onSuccess: () => {
      setSaved(true);
      // The staff footer reads the contact from GET /me.
      void queryClient.invalidateQueries({ queryKey: STAFF_ME_KEY });
    },
  });
  const errors = form.errors;
  const stale = form.error instanceof ApiError && form.error.status === 412;
  const summary: SummaryError[] = (
    ["name", "phone", "email", "privacy_request_target_days"] as const
  ).flatMap((field) => (errors[field] ? [{ fieldId: id(field), message: errors[field] }] : []));

  return (
    <form
      ref={ref}
      noValidate
      onSubmit={form.onSubmit}
      onChange={() => {
        setSaved(false);
        onChange();
      }}
      className="space-y-5"
    >
      <ErrorSummary errors={summary} attempt={attempt} />
      <Card title={t("contactTitle")} description={t("contactDescription")}>
        <div className="grid gap-4 md:grid-cols-2">
          <TextField
            id={id("name")}
            name="name"
            label={t("name")}
            hint={t("nameHint")}
            error={errors.name}
            defaultValue={contact?.name ?? ""}
            maxLength={200}
            required
          />
          <TextField
            id={id("phone")}
            name="phone"
            label={t("phoneOptional")}
            error={errors.phone}
            defaultValue={contact?.phone ?? ""}
            inputMode="tel"
            maxLength={14}
            className="num"
          />
          <TextField
            id={id("email")}
            name="email"
            type="email"
            label={t("emailOptional")}
            error={errors.email}
            defaultValue={contact?.email ?? ""}
            maxLength={254}
          />
          <TextField
            id={id("privacy_request_target_days")}
            name="privacy_request_target_days"
            label={t("targetDays")}
            hint={t("targetHint", { min: TARGET_MIN, max: TARGET_MAX })}
            error={errors.privacy_request_target_days}
            defaultValue={String(tenant.settings.privacy_request_target_days)}
            inputMode="numeric"
            maxLength={1}
            className="num max-w-40"
            required
          />
        </div>
      </Card>
      <div className="space-y-3 print:hidden">
        <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
        <ApiErrorAlert error={form.error} namespace="privacy" />
        {stale ? (
          <Button variant="secondary" onClick={() => void onReload()}>
            {t("reload")}
          </Button>
        ) : null}
      </div>
      <SaveBar dirty={dirty} savedLabel={saved ? t("saved") : undefined}>
        <Button variant="secondary" disabled={!dirty || form.pending} onClick={onDiscard}>
          {t("discard")}
        </Button>
        <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
          {form.pending ? tc("working") : t("save")}
        </Button>
      </SaveBar>
    </form>
  );
}

function RecordClasses() {
  const t = useTranslations("privacy.recordClasses");
  const te = useTranslations("privacy.recordClasses.erasure");
  const api = useBffClient("staff");
  const classes = useQuery({
    queryKey: PRIVACY_KEYS.recordClasses,
    queryFn: () => unwrap(api.GET("/api/v1/privacy/record-classes")),
    retry: false,
  });
  const columns: ColumnV2<RecordClass>[] = [
    { key: "label", stack: "title", header: t("colKind"), cell: (row) => row.label },
    { key: "retention", header: t("colKept"), cell: (row) => row.retention },
    { key: "basis", stack: "wide", header: t("colWhy"), cell: (row) => row.basis },
    { key: "erasure", header: t("colErase"), cell: (row) => te(row.erasure) },
  ];
  return (
    <section aria-labelledby="privacy-record-classes" className="space-y-3">
      <h2 id="privacy-record-classes" className="text-lg font-semibold text-ink">
        {t("title")}
      </h2>
      <p className="text-sm text-ink-muted">
        {t("matches")}{" "}
        <Link
          href="/settings/retention"
          className="font-semibold text-primary underline-offset-4 hover:underline"
        >
          {t("retentionLink")}
        </Link>
      </p>
      <DataTableV2
        id="privacy-record-classes-table"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...classes, data: classes.data?.data ?? [] })}
        rowKey={(row) => row.key}
        emptyTitle={t("emptyTitle")}
        onRetry={() => void classes.refetch()}
      />
    </section>
  );
}

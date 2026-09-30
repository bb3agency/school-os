"use client";

import type { TenantProfile } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { Value } from "@/components/ui/Value";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useApiForm } from "@/lib/forms";

/** No control characters (the API refuses them; one line each, like a letterhead). */
const line = (max: number) =>
  z
    .string()
    .trim()
    .max(max, { error: "tooLong" })
    .regex(/^[^\u0000-\u001f\u007f]*$/, { error: "invalid" });

export const letterheadSchema = z.object({
  school_name_te: line(200),
  address_en: line(300),
  address_te: line(300),
  affiliation: line(200),
  place: line(80),
});

/** ADR-0036: while Telugu is switched off its fields are not shown and may be absent. */
const letterheadEnglishSchema = letterheadSchema.extend({
  school_name_te: line(200).optional(),
  address_te: line(300).optional(),
});

const FIELDS = ["school_name_te", "address_en", "address_te", "affiliation", "place"] as const;
const ENGLISH_FIELDS = FIELDS.filter((field) => !field.endsWith("_te"));
const MAX: Record<(typeof FIELDS)[number], number> = {
  school_name_te: 200,
  address_en: 300,
  address_te: 300,
  affiliation: 200,
  place: 80,
};

/**
 * Certificate letterhead (US-1108, FR-CERT-013): the Telugu school name, address in both
 * languages, recognition line and place printed on every certificate. The English name is the
 * school's name. While Telugu is switched off (ADR-0036) only the English fields are shown;
 * the stored Telugu ones are sent back unchanged. Holders of `tenant.settings.manage` save it
 * (If-Match; the API may ask for a fresh MFA sign-in); everyone else sees it read-only.
 */
export function LetterheadCard({
  tenant,
  manage,
  tenantKey,
}: {
  tenant: TenantProfile;
  manage: boolean;
  tenantKey: readonly unknown[];
}) {
  const t = useTranslations("certificates.letterhead");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const [saved, setSaved] = useState(false);
  const head = tenant.settings.certificate_letterhead;
  const telugu = useTeluguEnabled();
  const fields = telugu ? FIELDS : ENGLISH_FIELDS;
  const form = useApiForm({
    schema: telugu ? letterheadSchema : letterheadEnglishSchema,
    invalidate: [tenantKey],
    fieldMap: (field) => field.replace(/^certificate_letterhead\./, ""),
    submit: (data) => {
      setSaved(false);
      return unwrap(
        api.PATCH("/api/v1/tenant", {
          headers: { "If-Match": `W/"${tenant.version}"` },
          body: {
            certificate_letterhead: {
              ...data,
              school_name_te: telugu ? (data.school_name_te ?? "") : (head?.school_name_te ?? ""),
              address_te: telugu ? (data.address_te ?? "") : (head?.address_te ?? ""),
            },
          },
        }),
      );
    },
    onSuccess: () => setSaved(true),
  });
  if (!manage) {
    return (
      <Card title={t("title")} description={t("description")}>
        <p className="text-sm text-ink-muted">{t("nameEnNote", { name: tenant.name })}</p>
        <dl className="divide-y divide-border">
          {fields.map((field) => (
            <div key={field} className="grid gap-1 py-3 sm:grid-cols-[16rem_1fr] sm:gap-4">
              <dt className="text-sm text-ink-muted">{t(`fields.${field}`)}</dt>
              <dd className="font-medium break-words">
                <Value>{head?.[field] || null}</Value>
              </dd>
            </div>
          ))}
        </dl>
      </Card>
    );
  }
  return (
    <form noValidate onSubmit={form.onSubmit}>
      <Card title={t("title")} description={t("description")}>
        <div className="space-y-4">
          <p className="text-sm text-ink-muted">{t("nameEnNote", { name: tenant.name })}</p>
          <div className="grid gap-4 md:grid-cols-2">
            {fields.map((field) => (
              <TextField
                key={field}
                name={field}
                label={t(`fields.${field}`)}
                hint={t(`hints.${field}`)}
                defaultValue={head?.[field] ?? ""}
                maxLength={MAX[field]}
                error={form.errors[field]}
                autoComplete="off"
                {...(field.endsWith("_te") ? { lang: "te" } : {})}
              />
            ))}
          </div>
          <p className="text-sm text-ink-muted">{t("logoNote")}</p>
          <ApiErrorAlert error={form.error} namespace="schoolSettings" />
          {saved ? (
            <Alert tone="success" live>
              {t("saved")}
            </Alert>
          ) : null}
          <Button type="submit" disabled={form.pending}>
            {form.pending ? tc("working") : t("save")}
          </Button>
        </div>
      </Card>
    </form>
  );
}

"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { useRouter } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formList, useApiForm } from "@/lib/forms";
import { CategoryChecklist, CountFields } from "./fields";
import { istLocalInput, newBreachSchema } from "./schema";
import { BREACH_KEYS, BREACH_MANAGE } from "./types";

const FIELDS = [
  "noticed_at",
  "description",
  "categories",
  "approx_children",
  "approx_guardians",
  "approx_staff",
  "containment",
] as const;

export function breachFieldId(field: string): string {
  return `breach-${field}`;
}

/**
 * Record a breach (US-2701, FR-DPDP-030, ADR-0050): when the school noticed it (the three
 * clocks start there), what happened (encrypted, never logged), the kinds of data and
 * approximate numbers of people. Owners and principals only, with a fresh MFA sign-in.
 *
 * The origin is always "Our school": a notice from SchoolOS (processor_notice) is pulled into
 * the register automatically on the shared platform, and the API refuses one typed by hand
 * there (422 pulled_automatically), so the form does not offer it.
 */
export function NewBreachScreen() {
  const t = useTranslations("breaches");
  const tf = useTranslations("breaches.form");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const router = useRouter();
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const [attempt, setAttempt] = useState(0);
  const [now] = useState(() => istLocalInput());

  const form = useApiForm({
    schema: newBreachSchema,
    extra: (element) => ({ categories: formList(element, "categories") }),
    onInvalid: () => setAttempt((value) => value + 1),
    invalidate: [BREACH_KEYS.all],
    submit: (data, idempotencyKey) =>
      unwrap(
        api.POST("/api/v1/privacy/breaches", {
          headers: { "Idempotency-Key": idempotencyKey },
          body: { ...data, origin: "school" },
        }),
      ),
    onSuccess: (created) => router.push(`/privacy/breaches/${created.id}`),
  });

  const header = (
    <PageHeader
      title={tf("title")}
      description={tf("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: t("title"), href: "/privacy/breaches" },
        { label: tf("title") },
      ]}
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
  if (!can(BREACH_MANAGE)) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={tf("notAllowedTitle")}>
          {tf("notAllowedBody")}
        </Alert>
      </div>
    );
  }

  const summary = FIELDS.flatMap((field) => {
    const message = form.errors[field];
    return message ? [{ fieldId: breachFieldId(field), message }] : [];
  });

  return (
    <div className="space-y-5">
      {header}
      <form noValidate onSubmit={form.onSubmit} className="space-y-5">
        <ErrorSummary errors={summary} attempt={attempt} />
        <Card title={tf("whenTitle")}>
          <TextField
            id={breachFieldId("noticed_at")}
            name="noticed_at"
            type="datetime-local"
            label={tf("noticedAt")}
            hint={tf("noticedAtHint")}
            error={form.errors.noticed_at}
            defaultValue={now}
            max={now}
            className="max-w-xs"
          />
        </Card>
        <Card title={tf("whatTitle")}>
          <div className="space-y-5">
            <TextAreaField
              id={breachFieldId("description")}
              name="description"
              label={tf("descriptionLabel")}
              hint={tf("descriptionHint")}
              error={form.errors.description}
              maxLength={4000}
              rows={6}
            />
            <CategoryChecklist
              id={breachFieldId("categories")}
              error={form.errors.categories}
              selected={[]}
            />
            <CountFields
              idFor={breachFieldId}
              errors={form.errors}
              values={{ approx_children: 0, approx_guardians: 0, approx_staff: 0 }}
            />
            <TextAreaField
              id={breachFieldId("containment")}
              name="containment"
              label={tf("containmentLabel")}
              hint={tf("containmentHint")}
              error={form.errors.containment}
              maxLength={4000}
              rows={4}
            />
          </div>
        </Card>
        <ApiErrorAlert error={form.error} namespace="breaches" />
        <div className="space-y-3 border-t border-border pt-4">
          <p className="text-sm text-ink" id="breach-consequence">
            {tf("consequence")}
          </p>
          <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              type="submit"
              disabled={form.pending}
              aria-disabled={form.pending || undefined}
              aria-describedby="breach-consequence"
              loading={form.pending}
            >
              {form.pending ? tc("working") : tf("submit")}
            </Button>
          </div>
        </div>
      </form>
    </div>
  );
}

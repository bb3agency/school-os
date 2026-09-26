"use client";

import type { ProvisionResult } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SecretOnce } from "@/components/ui/SecretOnce";
import { SelectField } from "@/components/ui/Select";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { cn } from "@/lib/cn";
import { formValues, useApiForm, zodErrorKeys } from "@/lib/forms";
import { BillingAccountFields } from "./BillingAccountFields";
import { PK, planLabel, usePlanDirectory } from "./data";
import {
  PROVISION_STEPS,
  firstStepWith,
  provisionSchema,
  stepErrors,
  type ProvisionStep,
} from "./provision-schema";

const fieldId = (field: string) => `provision-${field.replace(/\./g, "-")}`;

/**
 * FR-PLT-001..003 provision wizard (docs/16 §5.4): school → deployment → owner → plan →
 * billing account → review. One native <form>; inactive steps are hidden (not removed) so
 * every value submits. "Next" validates only its step; submit validates everything, then
 * POST /platform/tenants with one Idempotency-Key per attempt (a retry cannot create a
 * second school). The API asks for step-up MFA (428) if the last sign-in is too old.
 * A dedicated school's heartbeat key is shown ONCE, then dropped from memory.
 */
export function ProvisionSchoolForm() {
  const t = useTranslations("platform.provision");
  const tv = useTranslations("validation");
  const tc = useTranslations("common");
  const tmode = useTranslations("deploymentMode");
  const api = useBffClient("operator");
  const { plans } = usePlanDirectory();

  const formRef = useRef<HTMLFormElement>(null);
  const stepHeadingRef = useRef<HTMLHeadingElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [stepOnlyErrors, setStepOnlyErrors] = useState<Record<string, string>>({});
  const [review, setReview] = useState<Record<string, string>>({});
  const [tier, setTier] = useState<"shared" | "dedicated">("shared");
  const [done, setDone] = useState<ProvisionResult | null>(null);
  const [secretShown, setSecretShown] = useState(false);
  const hasNavigated = useRef(false);

  const form = useApiForm({
    schema: provisionSchema,
    fieldMap: (field) => field,
    invalidate: [PK.tenants, PK.dashboard, PK.subscriptions, PK.deployments],
    submit: (data, key) =>
      unwrap(
        api.POST("/api/v1/platform/tenants", {
          params: { header: { "Idempotency-Key": key } },
          body: data,
        }),
      ),
    onSuccess: (result) => {
      setDone(result);
      setSecretShown(Boolean(result.heartbeat_key));
    },
  });

  const errors = { ...form.errors, ...stepOnlyErrors };
  const step: ProvisionStep = PROVISION_STEPS[stepIndex] ?? "school";
  const errorFields = Object.keys(errors);

  useEffect(() => {
    if (hasNavigated.current) stepHeadingRef.current?.focus();
  }, [stepIndex]);

  // After a failed submit (client or server), jump to the first step with an error.
  useEffect(() => {
    const target = firstStepWith(Object.keys(form.errors));
    if (target) {
      hasNavigated.current = true;
      setStepIndex(PROVISION_STEPS.indexOf(target));
      requestAnimationFrame(() => summaryRef.current?.focus());
    }
  }, [form.errors]);

  function goTo(index: number) {
    hasNavigated.current = true;
    setStepIndex(index);
  }

  function next() {
    const element = formRef.current;
    if (!element) return;
    const values = formValues(element);
    const result = provisionSchema.safeParse(values);
    const all = result.success ? {} : zodErrorKeys(result.error);
    const found = Object.fromEntries(
      Object.entries(stepErrors(step, all)).map(([field, key]) => [
        field,
        tv.has(key) ? tv(key) : tv("invalid"),
      ]),
    );
    setStepOnlyErrors(found);
    if (Object.keys(found).length > 0) {
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    if (PROVISION_STEPS[stepIndex + 1] === "review") setReview(values);
    goTo(stepIndex + 1);
  }

  function back() {
    setStepOnlyErrors({});
    goTo(Math.max(0, stepIndex - 1));
  }

  const stepLabels: Record<ProvisionStep, string> = {
    school: t("steps.school"),
    deployment: t("steps.deployment"),
    owner: t("steps.owner"),
    plan: t("steps.plan"),
    billing: t("steps.billing"),
    review: t("steps.review"),
  };

  if (done) {
    if (secretShown && done.heartbeat_key) {
      return (
        <div className="max-w-3xl space-y-4 rounded-lg border border-border bg-surface p-6">
          <h2 className="text-xl font-semibold">{t("heartbeatTitle")}</h2>
          <p className="text-sm">{t("heartbeatBody")}</p>
          <SecretOnce
            label={t("heartbeatKey")}
            secret={done.heartbeat_key}
            keyId={done.heartbeat_key_id ?? null}
            doneLabel={tc("continue")}
            onDone={() => {
              // Drop the key from memory: keep only the non-secret facts.
              setDone({ ...done, heartbeat_key: null });
              setSecretShown(false);
              form.reset();
            }}
          />
        </div>
      );
    }
    return (
      <div className="max-w-3xl space-y-4">
        <Alert tone="success" live title={t("doneTitle")}>
          <p>{done.tier === "dedicated" ? t("doneDedicated") : t("doneShared")}</p>
          <p>{t(`ownerInvite.${done.owner_invite}`)}</p>
        </Alert>
        <div className="flex flex-wrap gap-2">
          <ButtonLink href={`/platform/schools/${done.tenant_id}`}>{t("openSchool")}</ButtonLink>
          <ButtonLink href="/platform/schools" variant="secondary">
            {t("backToSchools")}
          </ButtonLink>
        </div>
      </div>
    );
  }

  const planOptions = plans
    .filter((plan) => plan.status === "published" && plan.tier === tier)
    .map((plan) => ({ value: plan.id, label: planLabel(plan) }));

  const reviewRows: Array<{ label: string; value: string }> = [
    { label: t("fields.schoolName"), value: review.school_name ?? "" },
    { label: t("fields.code"), value: review.code ?? "" },
    { label: t("fields.boards"), value: review.boards ?? "" },
    {
      label: t("fields.deploymentMode"),
      value: review.tier === "dedicated" || review.tier === "shared" ? tmode(review.tier) : "",
    },
    { label: t("fields.customDomain"), value: review.custom_domain ?? "" },
    { label: t("fields.ownerName"), value: review["owner.display_name"] ?? "" },
    { label: t("fields.ownerEmail"), value: review["owner.email"] ?? "" },
    { label: t("fields.ownerSubject"), value: review["owner.idp_subject"] ?? "" },
    {
      label: t("fields.plan"),
      value: plans.find((plan) => plan.id === review.plan_id)?.name ?? "",
    },
    {
      label: t("fields.startAs"),
      value: review.start_as === "active" ? t("fields.startActive") : t("fields.startTrial"),
    },
    { label: t("fields.priceOverride"), value: review.price_override_inr ?? "" },
    { label: t("fields.legalName"), value: review["billing_account.legal_name"] ?? "" },
    { label: t("fields.gstin"), value: (review["billing_account.gstin"] ?? "").toUpperCase() },
    { label: t("fields.billingEmail"), value: review["billing_account.billing_email"] ?? "" },
  ];

  return (
    <div className="max-w-3xl space-y-6">
      <ol aria-label={t("stepsLabel")} className="flex flex-wrap gap-2 text-sm">
        {PROVISION_STEPS.map((id, index) => (
          <li
            key={id}
            aria-current={index === stepIndex ? "step" : undefined}
            className={cn(
              "rounded-full border px-3 py-1",
              index === stepIndex
                ? "border-primary bg-primary font-semibold text-on-primary"
                : index < stepIndex
                  ? "border-primary text-primary"
                  : "border-border text-ink-muted",
            )}
          >
            {stepLabels[id]}
          </li>
        ))}
      </ol>

      {errorFields.length > 0 ? (
        <div ref={summaryRef} tabIndex={-1}>
          <Alert tone="danger" title={t("errorSummary", { count: errorFields.length })} live>
            <ul className="list-disc pl-5">
              {errorFields.map((field) => (
                <li key={field}>
                  <a href={`#${fieldId(field)}`} className="underline">
                    {errors[field]}
                  </a>
                </li>
              ))}
            </ul>
          </Alert>
        </div>
      ) : null}

      <form
        ref={formRef}
        onSubmit={(event) => {
          setStepOnlyErrors({});
          form.onSubmit(event);
        }}
        noValidate
        className="space-y-6 rounded-lg border border-border bg-surface p-6"
      >
        <div className="space-y-1">
          <p className="text-sm text-ink-muted">
            {t("stepOf", { current: stepIndex + 1, total: PROVISION_STEPS.length })}
          </p>
          <h2 ref={stepHeadingRef} tabIndex={-1} className="text-xl font-semibold focus:outline-none">
            {stepLabels[step]}
          </h2>
        </div>

        <fieldset hidden={step !== "school"} className="space-y-4">
          <legend className="sr-only">{stepLabels.school}</legend>
          <TextField
            id={fieldId("school_name")}
            name="school_name"
            label={t("fields.schoolName")}
            hint={t("fields.schoolNameHint")}
            error={errors.school_name}
            autoComplete="organization"
            maxLength={200}
          />
          <TextField
            id={fieldId("code")}
            name="code"
            label={t("fields.code")}
            hint={t("fields.codeHint")}
            error={errors.code}
            autoComplete="off"
            spellCheck={false}
            maxLength={32}
          />
          <TextField
            id={fieldId("boards")}
            name="boards"
            label={t("fields.boards")}
            hint={t("fields.boardsHint")}
            error={errors.boards}
            autoComplete="off"
          />
        </fieldset>

        <fieldset hidden={step !== "deployment"} className="space-y-4">
          <legend className="sr-only">{stepLabels.deployment}</legend>
          <fieldset
            className="space-y-2"
            aria-describedby={errors.tier ? `${fieldId("tier")}-error` : undefined}
          >
            <legend className="text-sm font-semibold">{t("fields.deploymentMode")}</legend>
            {(["shared", "dedicated"] as const).map((mode) => (
              <div key={mode} className="flex items-start gap-3 rounded-md border border-border p-3">
                <input
                  type="radio"
                  id={mode === "shared" ? fieldId("tier") : `${fieldId("tier")}-${mode}`}
                  name="tier"
                  value={mode}
                  checked={tier === mode}
                  onChange={() => setTier(mode)}
                  aria-describedby={`${fieldId("tier")}-${mode}-hint`}
                  className="mt-1 size-4 accent-primary"
                />
                <div>
                  <label
                    htmlFor={mode === "shared" ? fieldId("tier") : `${fieldId("tier")}-${mode}`}
                    className="font-semibold"
                  >
                    {tmode(mode)}
                  </label>
                  <p id={`${fieldId("tier")}-${mode}-hint`} className="text-sm text-ink-muted">
                    {mode === "shared" ? t("fields.sharedHint") : t("fields.dedicatedHint")}
                  </p>
                </div>
              </div>
            ))}
          </fieldset>
          <TextField
            id={fieldId("custom_domain")}
            name="custom_domain"
            label={t("fields.customDomain")}
            hint={t("fields.customDomainHint")}
            error={errors.custom_domain}
            autoComplete="off"
            spellCheck={false}
            disabled={tier !== "dedicated"}
          />
        </fieldset>

        <fieldset hidden={step !== "owner"} className="space-y-4">
          <legend className="sr-only">{stepLabels.owner}</legend>
          <p className="text-sm text-ink-muted">
            {tier === "dedicated" ? t("fields.ownerHintDedicated") : t("fields.ownerHint")}
          </p>
          <TextField
            id={fieldId("owner.display_name")}
            name="owner.display_name"
            label={t("fields.ownerName")}
            error={errors["owner.display_name"]}
            autoComplete="off"
            maxLength={200}
          />
          <TextField
            id={fieldId("owner.email")}
            name="owner.email"
            type="email"
            label={t("fields.ownerEmail")}
            hint={t("fields.ownerEmailHint")}
            error={errors["owner.email"]}
            autoComplete="off"
          />
          <TextField
            id={fieldId("owner.idp_subject")}
            name="owner.idp_subject"
            label={t("fields.ownerSubject")}
            hint={t("fields.ownerSubjectHint")}
            error={errors["owner.idp_subject"]}
            autoComplete="off"
            spellCheck={false}
            maxLength={255}
          />
          <SelectField
            id={fieldId("owner.language")}
            name="owner.language"
            label={t("fields.ownerLanguage")}
            defaultValue="en"
            options={[
              { value: "en", label: "English" },
              { value: "te", label: "తెలుగు" },
            ]}
          />
        </fieldset>

        <fieldset hidden={step !== "plan"} className="space-y-4">
          <legend className="sr-only">{stepLabels.plan}</legend>
          <SelectField
            id={fieldId("plan_id")}
            name="plan_id"
            label={t("fields.plan")}
            hint={planOptions.length === 0 ? t("fields.noPlans") : t("fields.planHint")}
            error={errors.plan_id}
            placeholder={t("fields.planPlaceholder")}
            options={planOptions}
            defaultValue=""
          />
          <SelectField
            id={fieldId("start_as")}
            name="start_as"
            label={t("fields.startAs")}
            defaultValue="trial"
            options={[
              { value: "trial", label: t("fields.startTrial") },
              { value: "active", label: t("fields.startActive") },
            ]}
          />
          <TextField
            id={fieldId("price_override_inr")}
            name="price_override_inr"
            label={t("fields.priceOverride")}
            hint={t("fields.priceOverrideHint")}
            error={errors.price_override_inr}
            inputMode="decimal"
            autoComplete="off"
          />
          <TextAreaField
            id={fieldId("override_reason")}
            name="override_reason"
            label={t("fields.overrideReason")}
            error={errors.override_reason}
            maxLength={500}
            rows={2}
          />
        </fieldset>

        <fieldset hidden={step !== "billing"} className="space-y-4">
          <legend className="sr-only">{stepLabels.billing}</legend>
          <BillingAccountFields
            errors={errors}
            prefix="billing_account."
            idPrefix="provision-billing_account"
          />
        </fieldset>

        {step === "review" ? (
          <section className="space-y-3">
            <p>{t("reviewIntro")}</p>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
              {reviewRows.map((row) => (
                <div key={row.label} className="contents">
                  <dt className="text-ink-muted">{row.label}</dt>
                  <dd className="font-semibold break-words">{row.value || t("notProvided")}</dd>
                </div>
              ))}
            </dl>
            <p className="text-sm text-ink-muted">{tc("stepUpNote")}</p>
          </section>
        ) : null}

        <ApiErrorAlert error={form.error} />

        <div className="flex flex-wrap justify-between gap-2 border-t border-border pt-4">
          <Button variant="secondary" onClick={back} disabled={stepIndex === 0 || form.pending}>
            {tc("back")}
          </Button>
          {step === "review" ? (
            <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
              {form.pending ? t("submitting") : t("submit")}
            </Button>
          ) : (
            <Button onClick={next}>{tc("next")}</Button>
          )}
        </div>
      </form>
    </div>
  );
}

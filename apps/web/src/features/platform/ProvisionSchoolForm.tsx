"use client";

import { useTranslations } from "next-intl";
import { useActionState, useEffect, useRef, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { cn } from "@/lib/cn";
import { provisionSchoolAction, type ProvisionState } from "./provision-actions";
import {
  PROVISION_FIELDS,
  PROVISION_STEPS,
  STEP_FIELDS,
  provisionInputFromFormData,
  validateProvision,
  type FieldErrors,
  type ProvisionField,
  type ProvisionStep,
} from "./provision-schema";

export interface PlanOption {
  key: string;
  name: string;
}

const fieldId = (field: ProvisionField) => `provision-${field}`;
const INITIAL_STATE: ProvisionState = { status: "idle" };

/**
 * FR-PLT-001 provision wizard: school → plan and deployment → owner invite → review.
 * One native <form>; inactive steps are hidden (not removed) so every value submits.
 * Each "Next" validates only that step with the shared zod schema; the server action
 * validates everything again.
 */
export function ProvisionSchoolForm({ plans }: { plans: readonly PlanOption[] }) {
  const t = useTranslations("platform.provision");
  const tv = useTranslations("platform.validation");
  const tc = useTranslations("common");
  const tmode = useTranslations("deploymentMode");

  const formRef = useRef<HTMLFormElement>(null);
  const stepHeadingRef = useRef<HTMLHeadingElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [clientErrors, setClientErrors] = useState<FieldErrors>({});
  const [review, setReview] = useState<Record<ProvisionField, string> | null>(null);
  const [state, formAction, pending] = useActionState(provisionSchoolAction, INITIAL_STATE);
  const hasNavigated = useRef(false);

  const step: ProvisionStep = PROVISION_STEPS[stepIndex] ?? "school";
  const errors: FieldErrors =
    state.status === "invalid" ? { ...state.errors, ...clientErrors } : clientErrors;
  const errorFields = PROVISION_FIELDS.filter((field) => errors[field]);

  useEffect(() => {
    // Move focus to the new step's heading after Next/Back (not on first render).
    if (hasNavigated.current) stepHeadingRef.current?.focus();
  }, [stepIndex]);

  function currentInput() {
    const form = formRef.current;
    return form ? provisionInputFromFormData(new FormData(form)) : null;
  }

  function stepErrors(target: ProvisionStep, all: FieldErrors): FieldErrors {
    const scoped: FieldErrors = {};
    for (const field of STEP_FIELDS[target]) {
      const key = all[field];
      if (key) scoped[field] = key;
    }
    return scoped;
  }

  function goTo(index: number) {
    hasNavigated.current = true;
    setStepIndex(index);
  }

  function next() {
    const input = currentInput();
    if (!input) return;
    const result = validateProvision(input);
    const found = result.ok ? {} : stepErrors(step, result.errors);
    setClientErrors(found);
    if (Object.keys(found).length > 0) {
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    if (PROVISION_STEPS[stepIndex + 1] === "review") setReview(input);
    goTo(stepIndex + 1);
  }

  function back() {
    setClientErrors({});
    goTo(Math.max(0, stepIndex - 1));
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    const input = currentInput();
    if (!input) return;
    const result = validateProvision(input);
    if (result.ok) {
      setClientErrors({});
      return; // let the server action run
    }
    event.preventDefault();
    setClientErrors(result.errors);
    const firstBadStep = PROVISION_STEPS.findIndex(
      (candidate) => Object.keys(stepErrors(candidate, result.errors)).length > 0,
    );
    goTo(firstBadStep === -1 ? 0 : firstBadStep);
  }

  const errorOf = (field: ProvisionField) => {
    const key = errors[field];
    return key ? tv(key) : undefined;
  };

  const stepLabels: Record<ProvisionStep, string> = {
    school: t("steps.school"),
    plan: t("steps.plan"),
    owner: t("steps.owner"),
    review: t("steps.review"),
  };

  const planOptions = plans.map((plan) => ({ value: plan.key, label: plan.name }));
  const reviewRows: Array<{ field: ProvisionField; label: string; value: string }> = review
    ? [
        { field: "schoolName", label: t("fields.schoolName"), value: review.schoolName },
        { field: "legalName", label: t("fields.legalName"), value: review.legalName },
        { field: "stateCode", label: t("fields.stateCode"), value: review.stateCode },
        { field: "billingEmail", label: t("fields.billingEmail"), value: review.billingEmail },
        { field: "gstin", label: t("fields.gstin"), value: review.gstin },
        {
          field: "planKey",
          label: t("fields.plan"),
          value: plans.find((plan) => plan.key === review.planKey)?.name ?? review.planKey,
        },
        {
          field: "deploymentMode",
          label: t("fields.deploymentMode"),
          value:
            review.deploymentMode === "shared" || review.deploymentMode === "dedicated"
              ? tmode(review.deploymentMode)
              : "",
        },
        { field: "customDomain", label: t("fields.customDomain"), value: review.customDomain },
        { field: "ownerName", label: t("fields.ownerName"), value: review.ownerName },
        { field: "ownerEmail", label: t("fields.ownerEmail"), value: review.ownerEmail },
      ]
    : [];

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
                ? "border-primary bg-primary text-on-primary font-semibold"
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
                    {errorOf(field)}
                  </a>
                </li>
              ))}
            </ul>
          </Alert>
        </div>
      ) : null}

      {state.status === "not_connected" ? (
        <Alert tone="warning" live>
          {t("notConnected")}
        </Alert>
      ) : null}

      <form
        ref={formRef}
        action={formAction}
        onSubmit={onSubmit}
        noValidate
        className="space-y-6 rounded-lg border border-border bg-surface p-6"
      >
        <div className="space-y-1">
          <p className="text-sm text-ink-muted">
            {t("stepOf", { current: stepIndex + 1, total: PROVISION_STEPS.length })}
          </p>
          <h2
            ref={stepHeadingRef}
            tabIndex={-1}
            className="text-xl font-semibold focus:outline-none"
          >
            {stepLabels[step]}
          </h2>
        </div>

        <fieldset hidden={step !== "school"} className="space-y-4">
          <legend className="sr-only">{stepLabels.school}</legend>
          <TextField
            id={fieldId("schoolName")}
            name="schoolName"
            label={t("fields.schoolName")}
            hint={t("fields.schoolNameHint")}
            error={errorOf("schoolName")}
            autoComplete="organization"
          />
          <TextField
            id={fieldId("legalName")}
            name="legalName"
            label={t("fields.legalName")}
            error={errorOf("legalName")}
            autoComplete="off"
          />
          <div className="grid gap-4 md:grid-cols-2">
            <TextField
              id={fieldId("stateCode")}
              name="stateCode"
              label={t("fields.stateCode")}
              hint={t("fields.stateCodeHint")}
              error={errorOf("stateCode")}
              inputMode="numeric"
              maxLength={2}
              defaultValue="37"
              autoComplete="off"
            />
            <TextField
              id={fieldId("gstin")}
              name="gstin"
              label={t("fields.gstin")}
              hint={t("fields.gstinHint")}
              error={errorOf("gstin")}
              maxLength={15}
              autoComplete="off"
            />
          </div>
          <TextField
            id={fieldId("billingEmail")}
            name="billingEmail"
            type="email"
            label={t("fields.billingEmail")}
            error={errorOf("billingEmail")}
            autoComplete="email"
          />
        </fieldset>

        <fieldset hidden={step !== "plan"} className="space-y-4">
          <legend className="sr-only">{stepLabels.plan}</legend>
          <SelectField
            id={fieldId("planKey")}
            name="planKey"
            label={t("fields.plan")}
            hint={planOptions.length === 0 ? t("fields.noPlans") : undefined}
            error={errorOf("planKey")}
            placeholder={t("fields.planPlaceholder")}
            options={planOptions}
            defaultValue=""
          />
          <fieldset
            className="space-y-2"
            aria-describedby={
              errors.deploymentMode ? `${fieldId("deploymentMode")}-error` : undefined
            }
          >
            <legend className="text-sm font-semibold">{t("fields.deploymentMode")}</legend>
            {(["shared", "dedicated"] as const).map((mode) => (
              <div
                key={mode}
                className="flex items-start gap-3 rounded-md border border-border p-3"
              >
                <input
                  type="radio"
                  id={
                    mode === "shared"
                      ? fieldId("deploymentMode")
                      : `${fieldId("deploymentMode")}-${mode}`
                  }
                  name="deploymentMode"
                  value={mode}
                  defaultChecked={mode === "shared"}
                  aria-describedby={`${fieldId("deploymentMode")}-${mode}-hint`}
                  className="mt-1 size-4 accent-primary"
                />
                <div>
                  <label
                    htmlFor={
                      mode === "shared"
                        ? fieldId("deploymentMode")
                        : `${fieldId("deploymentMode")}-${mode}`
                    }
                    className="font-semibold"
                  >
                    {tmode(mode)}
                  </label>
                  <p
                    id={`${fieldId("deploymentMode")}-${mode}-hint`}
                    className="text-sm text-ink-muted"
                  >
                    {mode === "shared" ? t("fields.sharedHint") : t("fields.dedicatedHint")}
                  </p>
                </div>
              </div>
            ))}
            {errors.deploymentMode ? (
              <p
                id={`${fieldId("deploymentMode")}-error`}
                className="text-sm font-semibold text-danger"
              >
                {errorOf("deploymentMode")}
              </p>
            ) : null}
          </fieldset>
          <TextField
            id={fieldId("customDomain")}
            name="customDomain"
            label={t("fields.customDomain")}
            hint={t("fields.customDomainHint")}
            error={errorOf("customDomain")}
            autoComplete="off"
            spellCheck={false}
          />
        </fieldset>

        <fieldset hidden={step !== "owner"} className="space-y-4">
          <legend className="sr-only">{stepLabels.owner}</legend>
          <p className="text-sm text-ink-muted">{t("fields.ownerHint")}</p>
          <TextField
            id={fieldId("ownerName")}
            name="ownerName"
            label={t("fields.ownerName")}
            error={errorOf("ownerName")}
            autoComplete="name"
          />
          <TextField
            id={fieldId("ownerEmail")}
            name="ownerEmail"
            type="email"
            label={t("fields.ownerEmail")}
            error={errorOf("ownerEmail")}
            autoComplete="email"
          />
        </fieldset>

        {step === "review" ? (
          <section className="space-y-3">
            <p>{t("reviewIntro")}</p>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
              {reviewRows.map((row) => (
                <div key={row.field} className="contents">
                  <dt className="text-ink-muted">{row.label}</dt>
                  <dd className="font-semibold break-words">{row.value || t("notProvided")}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        <div className="flex flex-wrap justify-between gap-2 border-t border-border pt-4">
          <Button variant="secondary" onClick={back} disabled={stepIndex === 0 || pending}>
            {tc("back")}
          </Button>
          {step === "review" ? (
            <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
              {pending ? t("submitting") : t("submit")}
            </Button>
          ) : (
            <Button onClick={next}>{tc("next")}</Button>
          )}
        </div>
      </form>
    </div>
  );
}

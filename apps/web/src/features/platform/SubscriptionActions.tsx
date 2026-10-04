"use client";

import type { Subscription } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import { checkbox, localDateTime, money, reason, uuid } from "@/lib/validation";
import {
  PK,
  planLabel,
  readyOr,
  useAiBundles,
  useCan,
  useOperatorMe,
  usePlanDirectory,
} from "./data";

const reasonSchema = z.object({ reason });
const changePlanSchema = z.object({ plan_id: uuid });
const bundleSchema = z.object({ ai_bundle_id: uuid });
const extendSchema = z.object({ trial_ends_at: localDateTime });
/**
 * Negotiated price (FR-PLT-013, docs/16 §5.7): rupees before GST for each billing period, two
 * decimals at most, above zero. It replaces the plan's base price on invoices made from now
 * on; the reason (10–500 characters) is required with it, as on the API.
 */
const overrideSchema = z.object({
  price_override_inr: money.refine((value) => Number(value) > 0, {
    error: "invalidPositiveAmount",
  }),
  reason,
});
/**
 * Billing suspension (FR-PLT-014, docs/16 §9): past-due only, after the grace period, with a
 * reason; inside a protected board-exam window only a platform owner may approve it.
 */
const suspendSchema = z.object({ reason, exam_window_override: checkbox });
const INVALIDATE = [PK.subscriptions, PK.tenants, PK.dashboard] as const;

/**
 * FR-PLT-012..014 (docs/16 §5.7): activate a trial, extend it, change plan (from the next
 * period; no proration in M0), cancel (trials end now, paid plans at period end) and
 * reactivate. All need `platform.subscriptions.manage` with step-up MFA.
 */
export function SubscriptionActions({
  subscription,
  label,
}: {
  subscription: Subscription;
  /** Row description for screen readers, e.g. the school's name. */
  label: string;
}) {
  const t = useTranslations("platform.subscriptions");
  const tv = useTranslations("common");
  const api = useBffClient("operator");
  const can = useCan();
  const { plans } = usePlanDirectory();
  const bundles = readyOr(useAiBundles(), []);
  const locale = useLocale();
  // Only a platform owner can approve a suspension inside a board-exam window (§9.3).
  const owner = useOperatorMe()?.roles.includes("platform_owner") ?? false;
  if (!can("platform.subscriptions.manage")) return null;

  const path = { sub_id: subscription.id };
  const current = plans.find((plan) => plan.id === subscription.plan_id);
  const choices = plans.filter(
    (plan) => plan.status === "published" && (!current || plan.tier === current.tier),
  );
  const status = subscription.status;
  const monthly = !current || current.billing_period === "monthly";

  return (
    <div className="relative flex flex-wrap gap-2">
      {status === "trial" ? (
        <ActionDialog
          triggerLabel={t("activate")}
          triggerSize="sm"
          triggerDescription={label}
          title={t("activateTitle")}
          description={t("activateBody")}
          confirmLabel={t("activate")}
          stepUp
          schema={z.object({})}
          invalidate={INVALIDATE}
          submit={() =>
            unwrap(
              api.POST("/api/v1/platform/subscriptions/{sub_id}/activate", {
                params: { path },
              }),
            )
          }
        />
      ) : null}
      {status === "trial" ? (
        <ActionDialog
          triggerLabel={t("extendTrial")}
          triggerSize="sm"
          triggerDescription={label}
          title={t("extendTrialTitle")}
          confirmLabel={t("extendTrial")}
          stepUp
          schema={extendSchema}
          invalidate={INVALIDATE}
          submit={(data) =>
            unwrap(
              api.POST("/api/v1/platform/subscriptions/{sub_id}/extend-trial", {
                params: { path },
                body: { trial_ends_at: data.trial_ends_at },
              }),
            )
          }
        >
          {(errors) => (
            <TextField
              name="trial_ends_at"
              type="datetime-local"
              label={t("trialEndsAt")}
              hint={tv("istHint")}
              error={errors.trial_ends_at}
              required
            />
          )}
        </ActionDialog>
      ) : null}
      {status !== "cancelled" ? (
        <ActionDialog
          triggerLabel={t("changePlan")}
          triggerSize="sm"
          triggerDescription={label}
          title={t("changePlanTitle")}
          description={t("changePlanBody")}
          confirmLabel={t("changePlan")}
          stepUp
          schema={changePlanSchema}
          invalidate={INVALIDATE}
          submit={(data) =>
            unwrap(
              api.POST("/api/v1/platform/subscriptions/{sub_id}/change-plan", {
                params: { path },
                body: { plan_id: data.plan_id },
              }),
            )
          }
        >
          {(errors) => (
            <SelectField
              name="plan_id"
              label={t("newPlan")}
              placeholder={tv("chooseOne")}
              error={errors.plan_id}
              hint={choices.length === 0 ? t("noPublishedPlans") : undefined}
              defaultValue=""
              options={choices.map((plan) => ({
                value: plan.id,
                label: planLabel(plan),
                disabled: plan.id === subscription.plan_id,
              }))}
            />
          )}
        </ActionDialog>
      ) : null}
      {status !== "cancelled" && monthly ? (
        <ActionDialog
          triggerLabel={t("chooseAiBundle")}
          triggerSize="sm"
          triggerDescription={label}
          title={t("chooseAiBundleTitle")}
          description={t("chooseAiBundleBody")}
          confirmLabel={t("chooseAiBundle")}
          stepUp
          schema={bundleSchema}
          invalidate={INVALIDATE}
          submit={(data) =>
            unwrap(
              api.PUT("/api/v1/platform/subscriptions/{sub_id}/ai-bundle", {
                params: { path },
                body: { ai_bundle_id: data.ai_bundle_id },
              }),
            )
          }
        >
          {(errors) => (
            <SelectField
              name="ai_bundle_id"
              label={t("aiBundle")}
              placeholder={tv("chooseOne")}
              error={errors.ai_bundle_id}
              defaultValue={subscription.ai_bundle_id ?? ""}
              options={bundles
                .filter((bundle) => bundle.status === "published")
                .map((bundle) => ({
                  value: bundle.id,
                  label: t("aiBundleOption", {
                    name: bundle.name,
                    answers: formatCount(bundle.included_answers, locale) ?? "",
                    price: formatInr(bundle.price_inr, locale) ?? "",
                    rate: formatInr(bundle.overage_rate_inr, locale) ?? "",
                  }),
                }))}
            />
          )}
        </ActionDialog>
      ) : null}
      {status !== "cancelled" && subscription.ai_bundle_id ? (
        <ActionDialog
          triggerLabel={t("removeAiBundle")}
          triggerVariant="ghost"
          triggerSize="sm"
          triggerDescription={label}
          title={t("removeAiBundleTitle")}
          description={t("removeAiBundleBody")}
          confirmLabel={t("removeAiBundle")}
          stepUp
          schema={z.object({})}
          invalidate={INVALIDATE}
          submit={() =>
            unwrap(
              api.DELETE("/api/v1/platform/subscriptions/{sub_id}/ai-bundle", {
                params: { path },
              }),
            )
          }
        />
      ) : null}
      {status !== "cancelled" ? (
        <ActionDialog
          triggerLabel={
            subscription.price_override_inr === null ? t("setOverride") : t("changeOverride")
          }
          triggerSize="sm"
          triggerDescription={label}
          title={t("setOverrideTitle")}
          description={t("overrideBody")}
          confirmLabel={t("setOverride")}
          consequence={t("overrideConsequence")}
          stepUp
          schema={overrideSchema}
          invalidate={INVALIDATE}
          submit={(data) =>
            unwrap(
              api.PUT("/api/v1/platform/subscriptions/{sub_id}/price-override", {
                params: { path },
                body: data,
              }),
            )
          }
        >
          {(errors) => (
            <>
              <TextField
                name="price_override_inr"
                label={t("overrideAmount")}
                hint={t("overrideAmountHint")}
                inputMode="decimal"
                error={errors.price_override_inr}
                defaultValue={subscription.price_override_inr ?? ""}
                required
              />
              <ReasonField error={errors.reason} />
            </>
          )}
        </ActionDialog>
      ) : null}
      {status !== "cancelled" && subscription.price_override_inr !== null ? (
        <ActionDialog
          triggerLabel={t("removeOverride")}
          triggerVariant="ghost"
          triggerSize="sm"
          triggerDescription={label}
          title={t("removeOverrideTitle")}
          description={t("removeOverrideBody")}
          confirmLabel={t("removeOverride")}
          confirmVariant="danger"
          stepUp
          schema={z.object({})}
          invalidate={INVALIDATE}
          submit={() =>
            unwrap(
              api.DELETE("/api/v1/platform/subscriptions/{sub_id}/price-override", {
                params: { path },
              }),
            )
          }
        />
      ) : null}
      {status === "past_due" ? (
        <ActionDialog
          triggerLabel={t("suspend")}
          triggerVariant="danger"
          triggerSize="sm"
          triggerDescription={label}
          title={t("suspendTitle")}
          description={t("suspendBody", {
            date: formatDate(subscription.grace_ends_on) ?? "",
          })}
          confirmLabel={t("suspend")}
          confirmVariant="danger"
          consequence={
            current?.tier === "dedicated"
              ? t("suspendDedicatedConsequence")
              : current?.tier === "shared"
                ? t("suspendSharedConsequence")
                : t("suspendUnknownConsequence")
          }
          stepUp
          schema={suspendSchema}
          invalidate={INVALIDATE}
          submit={(data) =>
            unwrap(
              api.POST("/api/v1/platform/subscriptions/{sub_id}/suspend", {
                params: { path },
                body: data,
              }),
            )
          }
        >
          {(errors) => (
            <>
              <ReasonField error={errors.reason} />
              {owner ? (
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    name="exam_window_override"
                    className="mt-1 size-4 accent-primary"
                  />
                  {t("examWindowOverride")}
                </label>
              ) : null}
            </>
          )}
        </ActionDialog>
      ) : null}
      {status === "suspended" ? (
        <ActionDialog
          triggerLabel={t("reactivate")}
          triggerSize="sm"
          triggerDescription={label}
          title={t("reactivateTitle")}
          description={t("reactivateBody")}
          confirmLabel={t("reactivate")}
          stepUp
          schema={z.object({})}
          invalidate={INVALIDATE}
          submit={() =>
            unwrap(
              api.POST("/api/v1/platform/subscriptions/{sub_id}/reactivate", {
                params: { path },
              }),
            )
          }
        />
      ) : null}
      {status !== "cancelled" && !subscription.cancel_at_period_end ? (
        <ActionDialog
          triggerLabel={t("cancel")}
          triggerVariant="danger"
          triggerSize="sm"
          triggerDescription={label}
          title={t("cancelTitle")}
          description={status === "trial" ? t("cancelTrialBody") : t("cancelBody")}
          confirmLabel={t("cancel")}
          confirmVariant="danger"
          stepUp
          schema={reasonSchema}
          invalidate={INVALIDATE}
          submit={(data) =>
            unwrap(
              api.POST("/api/v1/platform/subscriptions/{sub_id}/cancel", {
                params: { path },
                body: { reason: data.reason },
              }),
            )
          }
        >
          {(errors) => <ReasonField error={errors.reason} />}
        </ActionDialog>
      ) : null}
    </div>
  );
}

/** Reason textarea used by every "reason required" action (10–500 characters). */
export function ReasonField({ error, label }: { error?: string | undefined; label?: string }) {
  const t = useTranslations("common");
  return (
    <TextAreaField
      name="reason"
      label={label ?? t("reason")}
      hint={t("reasonHint")}
      error={error}
      maxLength={500}
      rows={3}
      required
    />
  );
}

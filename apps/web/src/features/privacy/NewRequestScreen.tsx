"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useRef, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { todayInIndia } from "@/features/change-requests/dates";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { apiFieldErrors } from "@/lib/api-errors";
import { ApiError, newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import { formValues } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import * as z from "@/lib/zod";
import { SubjectPicker, type SubjectKind } from "./SubjectPicker";
import {
  PRIVACY_KEYS,
  PRIVACY_MANAGE,
  RELATIONSHIPS,
  REQUEST_KINDS,
  VERIFICATION_METHODS,
  isRequestKind,
  letterHref,
  type PrivacyRequest,
  type PrivacyRequestCreate,
  type RequestKind,
} from "./types";

export const REQUEST_TEXT_MAX = 4000;
const NAME_MAX = 200;
const NOT_CHECKED = "not_checked";

/**
 * Client checks for the "Log a request" form (FR-DPDP-005). Messages are keys under
 * `privacy.validation`; the API validates every value again.
 */
export function buildRequestSchema(today = todayInIndia()) {
  return z
    .object({
      kind: z.string().default(""),
      subject_kind: z.string().default("student"),
      student_id: z.string().default(""),
      guardian_id: z.string().default(""),
      membership_id: z.string().default(""),
      requester_relationship: z.string().default(""),
      requester_name: z.string().trim().max(NAME_MAX, { error: "tooLong" }).default(""),
      received_on: z.string().trim().default(""),
      request_text: z.string().trim().max(REQUEST_TEXT_MAX, { error: "tooLong" }).default(""),
      verification_method: z.string().default(NOT_CHECKED),
      nominee_name: z.string().trim().max(NAME_MAX, { error: "tooLong" }).default(""),
      nominee_relationship: z.string().trim().max(100, { error: "tooLong" }).default(""),
      nominee_contact: z.string().trim().max(200, { error: "tooLong" }).default(""),
    })
    .superRefine((data, context) => {
      if (!isRequestKind(data.kind)) {
        context.addIssue({ code: "custom", path: ["kind"], message: "chooseKind" });
      }
      const subject =
        data.subject_kind === "guardian"
          ? data.guardian_id
          : data.subject_kind === "member"
            ? data.membership_id
            : data.student_id;
      if (!subject) {
        context.addIssue({ code: "custom", path: ["subject"], message: "chooseSubject" });
      }
      if (!(RELATIONSHIPS as readonly string[]).includes(data.requester_relationship)) {
        context.addIssue({
          code: "custom",
          path: ["requester_relationship"],
          message: "chooseRelationship",
        });
      }
      const iso = data.received_on ? typedDateToIso(data.received_on) : null;
      if (!data.received_on) {
        context.addIssue({ code: "custom", path: ["received_on"], message: "dateRequired" });
      } else if (!iso) {
        context.addIssue({ code: "custom", path: ["received_on"], message: "invalidDate" });
      } else if (iso > today) {
        context.addIssue({ code: "custom", path: ["received_on"], message: "dateInFuture" });
      }
      // Invariant 4: no Aadhaar number is ever stored, even inside what the parent wrote.
      if (containsFullAadhaar(data.request_text)) {
        context.addIssue({ code: "custom", path: ["request_text"], message: "noAadhaar" });
      }
      if (data.kind === "nomination" && !data.nominee_name) {
        context.addIssue({ code: "custom", path: ["nominee_name"], message: "nomineeRequired" });
      }
    });
}

export type RequestForm = z.output<ReturnType<typeof buildRequestSchema>>;

/** The form's values → the API body (only the chosen subject; empty texts left out). */
export function toCreateBody(data: RequestForm): PrivacyRequestCreate {
  const kind = REQUEST_KINDS.find((value) => value === data.kind) ?? "other";
  const relationship = RELATIONSHIPS.find((value) => value === data.requester_relationship);
  const method = VERIFICATION_METHODS.find((value) => value === data.verification_method);
  const subject =
    data.subject_kind === "guardian"
      ? { guardian_id: data.guardian_id }
      : data.subject_kind === "member"
        ? { membership_id: data.membership_id }
        : { student_id: data.student_id };
  return {
    kind,
    subject,
    requester_relationship: relationship ?? "other",
    received_on: typedDateToIso(data.received_on),
    ...(data.requester_name ? { requester_name: data.requester_name } : {}),
    ...(data.request_text ? { request_text: data.request_text } : {}),
    ...(method ? { verification_method: method } : {}),
    ...(kind === "nomination"
      ? {
          nominee_name: data.nominee_name || null,
          nominee_relationship: data.nominee_relationship || null,
          nominee_contact: data.nominee_contact || null,
        }
      : {}),
  };
}

/** Server field path → form field (the subject's ids all point at the picker). */
function formField(field: string): string {
  const last = field.split(".").pop() ?? field;
  return ["student_id", "guardian_id", "membership_id", "subject"].includes(last)
    ? "subject"
    : last;
}

/**
 * Log a request (US-2602; FR-DPDP-005): what kind, who it is about, who asked, when, what they
 * asked (never logged) and how the office checked who they are. Saving shows the reference and
 * the acknowledgement letter.
 */
export function NewRequestScreen() {
  const t = useTranslations("privacy.new");
  const tr = useTranslations("privacy.requests");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const mePending = useStaffMeQuery().isPending;
  const [created, setCreated] = useState<PrivacyRequest | null>(null);
  const [generation, setGeneration] = useState(0);

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tr("title"), href: "/privacy/requests" },
        { label: t("title") },
      ]}
    />
  );

  if (!mePending && !can(PRIVACY_MANAGE)) {
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
      {created ? (
        <Alert tone="success" live title={t("savedTitle", { reference: created.reference })}>
          <p>{t("savedBody")}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <a
              href={letterHref(created.id, "acknowledgement")}
              target="_blank"
              rel="noopener"
              className="inline-flex min-h-10 items-center rounded-md border border-border-soft bg-surface px-3 font-semibold text-primary underline-offset-4 hover:underline"
            >
              {t("printAcknowledgement")}
            </a>
            <ButtonLink href={`/privacy/requests/${created.id}`} variant="secondary">
              {t("openRequest")}
            </ButtonLink>
            <Button
              variant="ghost"
              onClick={() => {
                setCreated(null);
                setGeneration((value) => value + 1);
              }}
            >
              {t("logAnother")}
            </Button>
          </div>
        </Alert>
      ) : (
        <RequestForm key={generation} onCreated={setCreated} />
      )}
    </div>
  );
}

function RequestForm({ onCreated }: { onCreated: (request: PrivacyRequest) => void }) {
  const t = useTranslations("privacy.new");
  const tk = useTranslations("privacy.kinds");
  const trel = useTranslations("privacy.relationships");
  const tm = useTranslations("privacy.verification");
  const tv = useTranslations("privacy.validation");
  const tf = useTranslations("errors.field");
  const tc = useTranslations("common");
  const dates = useDateInput();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const base = useId();
  const [kind, setKind] = useState<RequestKind | "">("");
  const [subjectKind, setSubjectKind] = useState<SubjectKind>("student");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const key = useRef(newIdempotencyKey());
  const busy = useRef(false);
  const id = (field: string) => `${base}-${field}`;
  const kindLabel = (value: RequestKind) => tk(`${value}.label`);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current) return;
    const form = event.currentTarget;
    const parsed = buildRequestSchema().safeParse({
      ...formValues(form),
      subject_kind: subjectKind,
    });
    if (!parsed.success) {
      const next: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const field = issue.path.map(String).join(".") || "form";
        if (!(field in next)) {
          next[field] = translateOr(tv, issue.message, "invalid", dates.hint("2026-10-21"));
        }
      }
      setErrors(next);
      setError(undefined);
      setAttempt((value) => value + 1);
      return;
    }
    setErrors({});
    setError(undefined);
    setPending(true);
    busy.current = true;
    try {
      const request = await unwrap(
        api.POST("/api/v1/privacy/requests", {
          headers: { "Idempotency-Key": key.current },
          body: toCreateBody(parsed.data),
        }),
      );
      key.current = newIdempotencyKey();
      await queryClient.invalidateQueries({ queryKey: PRIVACY_KEYS.all });
      onCreated(request);
    } catch (failure) {
      const mapped: Record<string, string> = {};
      let unmapped = apiFieldErrors(failure).length === 0;
      for (const { field, key: messageKey } of apiFieldErrors(failure)) {
        const target = formField(field);
        if (target === "subject" || form.elements.namedItem(target)) {
          if (!(target in mapped)) mapped[target] = translateOr(tf, messageKey, "invalid");
        } else {
          unmapped = true;
        }
      }
      setErrors(mapped);
      if (Object.keys(mapped).length > 0) setAttempt((value) => value + 1);
      if (failure instanceof ApiError && failure.status < 500) key.current = newIdempotencyKey();
      if (unmapped) setError(failure);
    } finally {
      busy.current = false;
      setPending(false);
    }
  }

  // Radio "cards": the native input stays (keyboard, form value); the label gets an outline,
  // and a blue fill when checked (`:has()`, Baseline).
  const card =
    "flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-border-soft bg-surface p-3 has-checked:border-primary has-checked:bg-primary-soft";

  return (
    <form noValidate onSubmit={(event) => void onSubmit(event)} className="space-y-6">
      <ErrorSummary
        attempt={attempt}
        errors={Object.entries(errors).map(([field, message]) => ({
          fieldId: field === "kind" ? `${id("kind")}-${REQUEST_KINDS[0]}` : id(field),
          message,
        }))}
      />

      <fieldset
        className="space-y-2"
        aria-describedby={errors.kind ? `${id("kind")}-error` : undefined}
      >
        <legend className="text-sm font-semibold text-ink">{t("kind")}</legend>
        <div className="grid gap-2 md:grid-cols-2">
          {REQUEST_KINDS.map((value) => (
            <label key={value} className={card}>
              <input
                id={`${id("kind")}-${value}`}
                type="radio"
                name="kind"
                value={value}
                checked={kind === value}
                onChange={() => setKind(value)}
                className="mt-1 size-4 shrink-0 accent-primary"
              />
              <span className="min-w-0 font-semibold text-ink">
                {kindLabel(value)}
                <span className="block text-sm font-normal text-ink-muted">
                  {tk(`${value}.hint`)}
                </span>
              </span>
            </label>
          ))}
        </div>
        {errors.kind ? (
          <p id={`${id("kind")}-error`} className="text-sm font-semibold text-danger">
            {errors.kind}
          </p>
        ) : null}
      </fieldset>

      <SubjectPicker
        kind={subjectKind}
        onKindChange={setSubjectKind}
        fieldId={id("subject")}
        error={errors.subject}
      />

      <div className="grid gap-4 md:grid-cols-2">
        <SelectField
          id={id("requester_relationship")}
          name="requester_relationship"
          label={t("relationship")}
          error={errors.requester_relationship}
          defaultValue=""
          placeholder={tc("chooseOne")}
          options={RELATIONSHIPS.map((value) => ({ value, label: trel(value) }))}
        />
        <TextField
          id={id("requester_name")}
          name="requester_name"
          label={t("requesterName")}
          hint={t("requesterNameHint")}
          error={errors.requester_name}
          maxLength={NAME_MAX}
          autoComplete="off"
        />
        <TextField
          id={id("received_on")}
          name="received_on"
          label={t("receivedOn")}
          hint={tc("dateHintFormat", dates.hint("2026-10-21"))}
          error={errors.received_on}
          defaultValue={dates.fromIso(todayInIndia())}
          inputMode="numeric"
          placeholder={dates.placeholder}
          autoComplete="off"
          className="num"
        />
      </div>

      <TextAreaField
        id={id("request_text")}
        name="request_text"
        label={t("requestText")}
        hint={t("requestTextHint")}
        error={errors.request_text}
        maxLength={REQUEST_TEXT_MAX}
        rows={4}
      />

      {kind === "nomination" ? (
        <div className="grid gap-4 md:grid-cols-3">
          <TextField
            id={id("nominee_name")}
            name="nominee_name"
            label={t("nomineeName")}
            error={errors.nominee_name}
            maxLength={NAME_MAX}
            autoComplete="off"
          />
          <TextField
            id={id("nominee_relationship")}
            name="nominee_relationship"
            label={t("nomineeRelationship")}
            error={errors.nominee_relationship}
            maxLength={100}
            autoComplete="off"
          />
          <TextField
            id={id("nominee_contact")}
            name="nominee_contact"
            label={t("nomineeContact")}
            error={errors.nominee_contact}
            maxLength={200}
            autoComplete="off"
          />
        </div>
      ) : null}

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-ink">{t("verification")}</legend>
        <p className="text-sm text-ink-muted">{t("verificationHint")}</p>
        <div className="grid gap-2 md:grid-cols-2">
          {[
            { value: NOT_CHECKED, label: tm("notChecked") },
            ...VERIFICATION_METHODS.map((value) => ({ value, label: tm(value) })),
          ].map((option) => (
            <label key={option.value} className={card}>
              <input
                type="radio"
                name="verification_method"
                value={option.value}
                defaultChecked={option.value === NOT_CHECKED}
                className="mt-1 size-4 shrink-0 accent-primary"
              />
              <span className="font-semibold text-ink">{option.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <ApiErrorAlert error={error} namespace="privacy" />
      <div className="flex justify-end">
        <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
          {pending ? tc("working") : t("submit")}
        </Button>
      </div>
    </form>
  );
}

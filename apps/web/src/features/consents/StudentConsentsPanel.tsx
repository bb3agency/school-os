"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useRef, useState, type FormEvent } from "react";
import * as z from "@/lib/zod";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Field, TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { ConsentStatusPill } from "@/features/apaar/parts";
import { todayInIndia } from "@/features/change-requests/dates";
import {
  checkEvidence,
  EVIDENCE_ACCEPT,
  StorageUploadError,
  uploadEvidence,
} from "@/features/change-requests/upload";
import { PdfJobButton } from "@/features/exports/PdfJobButton";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { apiFieldErrors } from "@/lib/api-errors";
import { ApiError, newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { formValues } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import { AnswerPill } from "./parts";
import {
  APAAR_READ,
  CONSENT_KEYS,
  CONSENT_READ,
  CONSENT_RECORD,
  FORM_LANGUAGES,
  RELATIONSHIPS,
  formsHref,
  ifMatch,
  isDecision,
  nextDecisions,
  type ConsentEntry,
  type ConsentInput,
  type ConsentView,
  type DecisionStatus,
  type PurposeState,
} from "./types";

const NOTE_MAX = 500;

/**
 * Client checks mirroring the API (FR-DPDP-021); message keys under `consents.validation`. A
 * decision other than "pending" needs who decided and the date on the form; "given" needs the
 * signed form (checked where the file is chosen). A note that looks like an Aadhaar number is
 * refused.
 */
export function buildDecisionSchema(today = todayInIndia()) {
  return z
    .object({
      purpose_id: z.string().min(1, { error: "choosePurpose" }),
      status: z.string().refine(isDecision, { error: "chooseStatus" }),
      relationship: z.string().default(""),
      guardian_id: z.string().default(""),
      decided_on: z.string().trim().default(""),
      form_language: z.string().default(""),
      note: z.string().trim().max(NOTE_MAX, { error: "noteTooLong" }).default(""),
    })
    .superRefine((data, context) => {
      if (containsFullAadhaar(data.note)) {
        context.addIssue({ code: "custom", path: ["note"], message: "noAadhaar" });
      }
      if (data.status === "pending") return;
      if (!data.relationship && !data.guardian_id) {
        context.addIssue({ code: "custom", path: ["relationship"], message: "chooseRelationship" });
      }
      const iso = data.decided_on ? typedDateToIso(data.decided_on) : null;
      if (!data.decided_on) {
        context.addIssue({ code: "custom", path: ["decided_on"], message: "dateRequired" });
      } else if (!iso) {
        context.addIssue({ code: "custom", path: ["decided_on"], message: "invalidDate" });
      } else if (iso > today) {
        context.addIssue({ code: "custom", path: ["decided_on"], message: "dateInFuture" });
      }
    });
}

function fieldId(field: string): string {
  return `purpose-consent-${field}`;
}

/** Server field → form field. */
function formField(field: string): string {
  const name = field.split(".").pop() ?? field;
  return name === "evidence_document_id" ? "evidence" : name;
}

/**
 * The student record's Consents tab (US-2604, FR-DPDP-025, ADR-0049): the one read-only view
 * of everything the family decided (the school's optional purposes, the APAAR decision and
 * each guardian's WhatsApp opt-ins, each kept in its own register), recording a decision on a
 * purpose with the signed form, every decision recorded, and the printable form.
 */
export function StudentConsentsPanel({ studentId }: { studentId: string }) {
  const t = useTranslations("consents.student");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const view = useQuery({
    queryKey: CONSENT_KEYS.view(studentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}/consent-view", {
          params: { path: { student_id: studentId } },
        }),
      ),
    enabled: can(CONSENT_READ),
    retry: false,
  });
  const register = useQuery({
    queryKey: CONSENT_KEYS.student(studentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}/consents", {
          params: { path: { student_id: studentId } },
        }),
      ),
    enabled: can([CONSENT_READ, CONSENT_RECORD]),
    retry: false,
  });

  if (register.isPending) return <LoadingState label={tc("loading")} />;
  if (!register.data) return <ApiErrorAlert error={register.error} namespace="consents" />;
  const purposes = register.data.purposes;
  const history = purposes
    .flatMap((purpose) =>
      (purpose.history ?? []).map((entry) => ({ entry, label: purpose.label_en })),
    )
    .sort((a, b) => b.entry.recorded_at.localeCompare(a.entry.recorded_at));

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start">
        <PurposesCard purposes={purposes} studentId={studentId} />
        <div className="space-y-4">
          {view.data ? <OtherRegisters view={view.data} studentId={studentId} /> : null}
          {view.error ? <ApiErrorAlert error={view.error} namespace="consents" /> : null}
        </div>
      </div>
      {can(CONSENT_RECORD) && purposes.some((purpose) => purpose.active) ? (
        <RecordDecision
          studentId={studentId}
          purposes={purposes.filter((purpose) => purpose.active)}
          version={register.data.version}
        />
      ) : null}
      <History rows={history} />
      <p className="text-sm text-ink-muted">{t("refusalNote")}</p>
    </div>
  );
}

function PurposesCard({ purposes, studentId }: { purposes: PurposeState[]; studentId: string }) {
  const t = useTranslations("consents.student");
  const locale = useLocale() as Locale;
  const columns: ColumnV2<PurposeState>[] = [
    {
      key: "purpose",
      stack: "title",
      header: t("colPurpose"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold text-ink break-anywhere">{row.label_en}</span>
          {!row.active ? <span className="text-xs text-ink-muted">{t("switchedOff")}</span> : null}
        </span>
      ),
    },
    {
      key: "answer",
      header: t("colAnswer"),
      cell: (row) => <AnswerPill answer={row.answer} />,
    },
    {
      key: "decided",
      header: t("colDecidedOn"),
      cell: (row) => (
        <span className="num whitespace-nowrap">
          <Value>{row.current?.decided_on ? formatDate(row.current.decided_on) : null}</Value>
        </span>
      ),
    },
    {
      key: "version",
      header: t("colFormVersion"),
      numeric: true,
      cell: (row) =>
        row.current
          ? t("formVersion", { version: formatCount(row.current.form_version, locale) ?? "" })
          : null,
    },
  ];
  return (
    <Card
      title={t("purposesTitle")}
      description={t("purposesDescription")}
      actions={
        <span className="flex flex-wrap items-start gap-2" data-print="hide">
          <a
            href={formsHref({ studentId })}
            target="_blank"
            rel="noopener"
            className="inline-flex min-h-10 items-center text-sm font-semibold text-primary underline-offset-4 hover:underline pointer-coarse:min-h-11"
          >
            {t("printForm")}
          </a>
          <PdfJobButton
            label={t("pdfForm")}
            request={{
              kind: "consent_forms",
              body: { student_id: studentId, only_unanswered: false },
            }}
          />
        </span>
      }
    >
      <DataTableV2
        id="student-consents"
        stacked
        caption={t("purposesTitle")}
        captionHidden
        columns={columns}
        state={{ status: "ready", data: purposes }}
        rowKey={(row) => row.purpose_id}
        emptyTitle={t("noPurposesTitle")}
        emptyBody={t("noPurposesBody")}
      />
    </Card>
  );
}

/** APAAR and WhatsApp, read-only here: each is changed on its own screen. */
function OtherRegisters({ view, studentId }: { view: ConsentView; studentId: string }) {
  const t = useTranslations("consents.student");
  const tm = useTranslations("messaging.optins");
  const tr = useTranslations("apaar.relationships");
  const can = useStaffCan();
  return (
    <>
      {view.apaar ? (
        <Card title={t("apaarTitle")} headingLevel={3}>
          <div className="space-y-2 text-sm">
            <p className="flex flex-wrap items-center gap-2">
              <ConsentStatusPill status={view.apaar.status} />
              {view.apaar.decided_on ? (
                <span className="num">
                  {t("decidedOn", { date: formatDate(view.apaar.decided_on) ?? "" })}
                </span>
              ) : null}
            </p>
            <p>{view.apaar.has_form ? t("apaarFormOnFile") : t("apaarNoForm")}</p>
            {can(APAAR_READ) ? (
              <Link
                href={`/apaar/students/${encodeURIComponent(studentId)}`}
                className="inline-flex min-h-6 items-center font-semibold text-primary underline-offset-4 hover:underline"
              >
                {t("apaarLink")}
              </Link>
            ) : null}
          </div>
        </Card>
      ) : null}
      {view.whatsapp ? (
        <Card title={t("whatsappTitle")} headingLevel={3}>
          {!view.whatsapp.available ? (
            <p className="text-sm text-ink-muted">{t("whatsappOff")}</p>
          ) : view.whatsapp.guardians.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("whatsappNoGuardians")}</p>
          ) : (
            <ul className="divide-y divide-border text-sm">
              {view.whatsapp.guardians.map((guardian) => (
                <li key={guardian.guardian_id} className="space-y-1.5 py-3 first:pt-0">
                  <p className="font-semibold text-ink break-anywhere">
                    <Value>{guardian.full_name}</Value>
                    {guardian.relationship ? (
                      <span className="font-normal text-ink-muted">
                        {" · "}
                        {translateOr(tr, guardian.relationship, "guardian")}
                      </span>
                    ) : null}
                  </p>
                  <ul className="flex flex-wrap gap-2">
                    {guardian.categories.map((category) => (
                      <li key={category.category}>
                        <Pill
                          size="sm"
                          variant={category.status === "opted_in" ? "positive" : "tag"}
                          icon={null}
                        >
                          {translateOr(tm, `categories.${category.category}`, "category")}
                          {": "}
                          {translateOr(tm, `states.${category.status}`, "states.not_asked")}
                        </Pill>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-sm text-ink-muted">{t("whatsappHint")}</p>
        </Card>
      ) : null}
    </>
  );
}

interface HistoryRow {
  entry: ConsentEntry;
  label: string;
}

function History({ rows }: { rows: HistoryRow[] }) {
  const t = useTranslations("consents.student");
  const ta = useTranslations("apaar.relationships");
  const locale = useLocale() as Locale;
  const columns: ColumnV2<HistoryRow>[] = [
    {
      key: "recorded",
      stack: "field",
      header: t("colRecorded"),
      cell: (row) => (
        <span className="num text-xs whitespace-nowrap text-ink-muted">
          {formatDateTime(row.entry.recorded_at)}
        </span>
      ),
    },
    {
      key: "purpose",
      stack: "title",
      header: t("colPurpose"),
      cell: (row) => <span className="font-semibold break-anywhere">{row.label}</span>,
    },
    {
      key: "decision",
      header: t("colAnswer"),
      cell: (row) => <AnswerPill answer={row.entry.status} />,
    },
    {
      key: "who",
      header: t("colWho"),
      cell: (row) => (row.entry.relationship ? ta(row.entry.relationship) : <Value>{null}</Value>),
    },
    {
      key: "date",
      header: t("colDecidedOn"),
      cell: (row) => (
        <span className="num">
          <Value>{row.entry.decided_on ? formatDate(row.entry.decided_on) : null}</Value>
        </span>
      ),
    },
    {
      key: "version",
      header: t("colFormVersion"),
      numeric: true,
      cell: (row) => formatCount(row.entry.form_version, locale),
    },
    {
      key: "form",
      header: t("colForm"),
      cell: (row) => (row.entry.evidence_document_id ? t("formOnFile") : t("noForm")),
    },
    {
      key: "by",
      header: t("colRecordedBy"),
      cell: (row) => <Value>{row.entry.recorded_by_name}</Value>,
    },
    {
      key: "note",
      stack: "wide",
      header: t("colNote"),
      cell: (row) => <Value>{row.entry.note}</Value>,
    },
  ];
  return (
    <section aria-labelledby="purpose-consent-history" className="space-y-3">
      <h2 id="purpose-consent-history" className="text-lg font-semibold text-ink">
        {t("historyTitle")}
      </h2>
      <DataTableV2
        id="student-consent-history"
        stacked
        caption={t("historyTitle")}
        captionHidden
        columns={columns}
        state={{ status: "ready", data: rows }}
        rowKey={(row) => row.entry.id}
        emptyTitle={t("historyEmptyTitle")}
        emptyBody={t("historyEmptyBody")}
      />
    </section>
  );
}

type Step = "idle" | "presign" | "send" | "register" | "submit";

function RecordDecision({
  studentId,
  purposes,
  version,
}: {
  studentId: string;
  purposes: PurposeState[];
  version: number;
}) {
  const t = useTranslations("consents.record");
  const tv = useTranslations("consents.validation");
  const tf = useTranslations("errors.field");
  const tc = useTranslations("common");
  const ta = useTranslations("apaar");
  const dates = useDateInput();
  const telugu = useTeluguEnabled();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [purposeId, setPurposeId] = useState(purposes[0]?.purpose_id ?? "");
  const purpose = purposes.find((item) => item.purpose_id === purposeId);
  const options = nextDecisions(purpose?.answer ?? "not_answered");
  const [chosen, setChosen] = useState<DecisionStatus | null>(null);
  const status: DecisionStatus =
    chosen && options.includes(chosen) ? chosen : (options[0] ?? "pending");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(undefined);
  const [step, setStep] = useState<Step>("idle");
  const [attempt, setAttempt] = useState(0);
  const [done, setDone] = useState(false);
  const keys = useRef({
    upload: newIdempotencyKey(),
    register: newIdempotencyKey(),
    submit: newIdempotencyKey(),
  });
  const guardians = useQuery({
    queryKey: ["staff", "students", studentId, "guardians"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}/guardians", {
          params: { path: { student_id: studentId } },
        }),
      ),
    retry: false,
  });

  function clientMessage(key: string): string {
    return translateOr(tv, key, "invalid", dates.hint("2026-07-15"));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formValues(form);
    const file =
      (form.elements.namedItem("evidence") as HTMLInputElement | null)?.files?.[0] ?? null;
    const parsed = buildDecisionSchema().safeParse({ ...raw, status, purpose_id: purposeId });
    const next: Record<string, string> = {};
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path.map(String).join(".") || "form";
        if (!(field in next)) next[field] = clientMessage(issue.message);
      }
    }
    // FR-DPDP-021: "Agreed" is recorded only with the signed form.
    if (status === "given" || file) {
      const problem = checkEvidence(file);
      if (problem)
        next.evidence = clientMessage(status === "given" && !file ? "formRequired" : problem);
    }
    setErrors(next);
    setError(undefined);
    setDone(false);
    if (!parsed.success || Object.keys(next).length > 0) {
      setAttempt((value) => value + 1);
      return;
    }
    const data = parsed.data;
    try {
      let documentId: string | null = null;
      if (file) {
        documentId = await uploadEvidence(
          api,
          file,
          t("formTitle"),
          { upload: keys.current.upload, register: keys.current.register },
          setStep,
        );
      }
      setStep("submit");
      const relationship = RELATIONSHIPS.find((value) => value === data.relationship) ?? null;
      const language = FORM_LANGUAGES.find((value) => value === data.form_language) ?? null;
      const decided = status !== "pending";
      const body: ConsentInput = {
        purpose_id: data.purpose_id,
        status,
        relationship: decided ? relationship : null,
        guardian_id: decided && data.guardian_id ? data.guardian_id : null,
        decided_on:
          decided && data.decided_on ? (typedDateToIso(data.decided_on) ?? data.decided_on) : null,
        form_language: decided ? language : null,
        evidence_document_id: documentId,
        note: data.note ? data.note : null,
      };
      await unwrap(
        api.POST("/api/v1/students/{student_id}/consents", {
          params: { path: { student_id: studentId } },
          headers: { "Idempotency-Key": keys.current.submit, "If-Match": ifMatch(version) },
          body,
        }),
      );
      keys.current = {
        upload: newIdempotencyKey(),
        register: newIdempotencyKey(),
        submit: newIdempotencyKey(),
      };
      form.reset();
      setChosen(null);
      setDone(true);
      await queryClient.invalidateQueries({ queryKey: CONSENT_KEYS.all });
    } catch (failure) {
      if (failure instanceof StorageUploadError) {
        keys.current = {
          ...keys.current,
          upload: newIdempotencyKey(),
          register: newIdempotencyKey(),
        };
        setErrors({ evidence: clientMessage("uploadFailed") });
        setAttempt((value) => value + 1);
        return;
      }
      const mapped: Record<string, string> = {};
      let unmapped = apiFieldErrors(failure).length === 0;
      for (const { field, key } of apiFieldErrors(failure)) {
        const target = formField(field);
        if (form.elements.namedItem(target)) {
          if (!(target in mapped)) mapped[target] = translateOr(tf, key, "invalid");
        } else {
          unmapped = true;
        }
      }
      setErrors(mapped);
      if (Object.keys(mapped).length > 0) setAttempt((value) => value + 1);
      if (failure instanceof ApiError && failure.status < 500) {
        keys.current = { ...keys.current, submit: newIdempotencyKey() };
      }
      if (unmapped) setError(failure);
    } finally {
      setStep("idle");
    }
  }

  const busy = step !== "idle";
  const guardianOptions = (guardians.data ?? []).map((guardian) => ({
    value: guardian.id,
    label: `${guardian.full_name} · ${translateOr(ta, `relationships.${guardian.relationship}`, "relationships.guardian")}`,
  }));

  return (
    <Card title={t("title")} description={t("description")}>
      {done ? (
        <Alert tone="success" live title={t("savedTitle")}>
          {t("savedBody")}
        </Alert>
      ) : null}
      <form noValidate onSubmit={(event) => void onSubmit(event)} className="space-y-5">
        <ErrorSummary
          attempt={attempt}
          errors={Object.entries(errors).map(([field, message]) => ({
            fieldId: fieldId(field),
            message,
          }))}
        />
        <SelectField
          id={fieldId("purpose_id")}
          name="purpose_id"
          label={t("purpose")}
          error={errors.purpose_id}
          value={purposeId}
          onChange={(event) => {
            setPurposeId(event.currentTarget.value);
            setChosen(null);
          }}
          options={purposes.map((item) => ({ value: item.purpose_id, label: item.label_en }))}
          className="max-w-xl"
        />
        {purpose ? (
          <p className="flex flex-wrap items-center gap-2 text-sm">
            {t("currentAnswer")} <AnswerPill answer={purpose.answer} />
          </p>
        ) : null}
        <SegmentedControl
          legend={t("decision")}
          legendVisible
          value={status}
          onValueChange={(value) => {
            if (isDecision(value)) setChosen(value);
          }}
          options={options.map((value) => ({ value, label: t(`decisions.${value}`) }))}
        />
        {status === "refused" || status === "withdrawn" ? (
          <Alert tone="info">{t("refuseNote")}</Alert>
        ) : null}
        {status !== "pending" ? (
          <div className="grid gap-4 xl:grid-cols-2">
            {guardianOptions.length > 0 ? (
              <SelectField
                id={fieldId("guardian_id")}
                name="guardian_id"
                label={t("guardian")}
                hint={t("guardianHint")}
                error={errors.guardian_id}
                defaultValue=""
                placeholder={t("guardianNone")}
                options={guardianOptions}
              />
            ) : null}
            <SelectField
              id={fieldId("relationship")}
              name="relationship"
              label={t("relationship")}
              error={errors.relationship}
              defaultValue=""
              placeholder={tc("chooseOne")}
              options={RELATIONSHIPS.map((value) => ({
                value,
                label: ta(`relationships.${value}`),
              }))}
            />
            <TextField
              id={fieldId("decided_on")}
              name="decided_on"
              label={t("decidedOn")}
              hint={tc("dateHintFormat", dates.hint("2026-07-15"))}
              error={errors.decided_on}
              inputMode="numeric"
              placeholder={dates.placeholder}
              autoComplete="off"
            />
            {/* Forms print in Telugu only while Telugu is switched on (ADR-0036). */}
            {telugu ? (
              <SelectField
                id={fieldId("form_language")}
                name="form_language"
                label={t("formLanguage")}
                defaultValue=""
                placeholder={tc("chooseOne")}
                options={FORM_LANGUAGES.map((value) => ({
                  value,
                  label: ta(`settings.languages.${value}`),
                }))}
              />
            ) : null}
          </div>
        ) : null}
        {status !== "pending" ? (
          <Field
            id={fieldId("evidence")}
            label={status === "given" ? t("signedForm") : t("signedFormOptional")}
            hint={t("signedFormHint")}
            error={errors.evidence}
          >
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                name="evidence"
                type="file"
                accept={EVIDENCE_ACCEPT}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                className="block w-full text-sm file:mr-3 file:min-h-10 file:rounded-md file:border file:border-border-strong file:bg-surface file:px-3 file:py-2 file:font-semibold"
              />
            )}
          </Field>
        ) : null}
        <TextAreaField
          id={fieldId("note")}
          name="note"
          label={t("note")}
          hint={t("noteHint")}
          error={errors.note}
          maxLength={NOTE_MAX}
          rows={3}
        />
        <div role="status" aria-live="polite" className="text-sm">
          {busy ? t(`step.${step}`) : null}
        </div>
        <ApiErrorAlert error={error} namespace="consents" />
        <div className="flex justify-end">
          <Button type="submit" disabled={busy} aria-disabled={busy || undefined}>
            {busy ? tc("working") : t("submit")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

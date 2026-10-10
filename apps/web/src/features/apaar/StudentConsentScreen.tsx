"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRef, useState, type FormEvent } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { todayInIndia } from "@/features/change-requests/dates";
import {
  checkEvidence,
  EVIDENCE_ACCEPT,
  StorageUploadError,
  uploadEvidence,
} from "@/features/change-requests/upload";
import { containsFullAadhaar } from "@/features/students/aadhaar";
import { apiFieldErrors } from "@/lib/api-errors";
import { ApiError, newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import { formatDate, formatDateTime } from "@/lib/format";
import { formValues } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import { ConsentStatusPill } from "./parts";
import {
  APAAR_KEYS,
  APAAR_READ,
  APAAR_RECORD,
  FORM_LANGUAGES,
  RELATIONSHIPS,
  isConsentStatus,
  nextStatuses,
  studentFormHref,
  type ConsentEntry,
  type ConsentInput,
  type ConsentStatus,
} from "./types";

const NOTE_MAX = 500;
const EARLIEST = "2023-10-01";

/**
 * Client checks mirroring the API (FR-APC-001); message keys under `apaar.validation`. A decision
 * other than "pending" needs who decided and the date on the form; "given" needs the signed
 * form (checked where the file is chosen). A note that looks like an Aadhaar number is refused.
 */
export function buildConsentSchema(today = todayInIndia()) {
  return z
    .object({
      status: z.string().refine(isConsentStatus, { error: "chooseStatus" }),
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
      } else if (iso < EARLIEST) {
        context.addIssue({ code: "custom", path: ["decided_on"], message: "dateTooEarly" });
      }
    });
}

/** Server field → form field. */
function formField(field: string): string {
  const name = field.split(".").pop() ?? field;
  return name === "evidence_document_id" ? "evidence" : name;
}

/**
 * One student's APAAR consent (US-1901, US-1902): the current state, every recorded decision
 * (history is never overwritten), the printable form, and recording the parent's decision with
 * the signed form uploaded as evidence. "Refused" is a full answer, never "missing".
 */
export function StudentConsentScreen({ studentId }: { studentId: string }) {
  const t = useTranslations("apaar.student");
  const ta = useTranslations("apaar");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const crumbs = [{ label: ta("title"), href: "/apaar" }, { label: t("title") }];

  const consent = useQuery({
    queryKey: APAAR_KEYS.student(studentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}/apaar-consent", {
          params: { path: { student_id: studentId } },
        }),
      ),
    retry: false,
  });
  const student = useQuery({
    queryKey: ["staff", "students", studentId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}", { params: { path: { student_id: studentId } } }),
      ),
    staleTime: 60_000,
    retry: false,
  });

  if (consent.isPending) return <LoadingState label={tc("loading")} />;
  if (!consent.data) {
    return (
      <div className="space-y-6">
        <PageHeader breadcrumb={crumbs} title={t("title")} />
        <ApiErrorAlert error={consent.error} />
      </div>
    );
  }
  const data = consent.data;
  const name = student.data?.canonical.full_name?.value ?? null;
  const subtitle = [name, student.data?.admission_no, student.data?.enrollment?.label]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="space-y-6">
      <PageHeader breadcrumb={crumbs} title={t("title")} description={subtitle || undefined} />
      <Card title={t("currentTitle")}>
        <div className="flex flex-wrap items-center gap-3">
          <ConsentStatusPill status={data.status} />
          {data.current?.decided_on ? (
            <span className="text-sm">
              {t("decidedOn", { date: formatDate(data.current.decided_on) ?? "" })}
            </span>
          ) : null}
        </div>
        {data.status === "refused" || data.status === "withdrawn" ? (
          <p className="mt-3 text-sm">{t("refusedNote")}</p>
        ) : null}
        {can([APAAR_READ, APAAR_RECORD]) ? (
          <p className="mt-4 flex flex-wrap gap-x-5 gap-y-2" data-print="hide">
            <a
              href={studentFormHref(studentId)}
              target="_blank"
              rel="noopener"
              className="font-semibold text-primary underline-offset-4 hover:underline"
            >
              {t("printForm")}
            </a>
            {FORM_LANGUAGES.map((language) => (
              <a
                key={language}
                href={studentFormHref(studentId, language)}
                target="_blank"
                rel="noopener"
                lang={language}
                className="text-primary underline-offset-4 hover:underline"
              >
                {t(`printIn.${language}`)}
              </a>
            ))}
          </p>
        ) : null}
      </Card>

      {can(APAAR_RECORD) ? (
        <RecordDecision studentId={studentId} current={data.status} version={data.version} />
      ) : null}

      <History entries={data.history} />
    </div>
  );
}

function History({ entries }: { entries: readonly ConsentEntry[] }) {
  const t = useTranslations("apaar.student");
  const ta = useTranslations("apaar");
  const columns: Column<ConsentEntry>[] = [
    {
      key: "recorded",
      stack: "field",
      header: t("colRecorded"),
      cell: (row) => (
        <span className="font-mono text-xs whitespace-nowrap text-ink-muted">
          {formatDateTime(row.recorded_at)}
        </span>
      ),
    },
    {
      key: "status",
      stack: "title",
      header: t("colDecision"),
      cell: (row) => <ConsentStatusPill status={row.status} />,
    },
    {
      key: "who",
      header: t("colWho"),
      cell: (row) =>
        row.relationship ? ta(`relationships.${row.relationship}`) : <Value>{null}</Value>,
    },
    {
      key: "date",
      header: t("colDate"),
      cell: (row) => <Value>{row.decided_on ? formatDate(row.decided_on) : null}</Value>,
    },
    {
      key: "form",
      header: t("colForm"),
      cell: (row) => (row.evidence_document_id ? ta("formOnFile") : ta("noForm")),
    },
    {
      key: "by",
      header: t("colRecordedBy"),
      cell: (row) => <Value>{row.recorded_by_name}</Value>,
    },
    {
      key: "note",
      stack: "wide",
      header: t("colNote"),
      cell: (row) => <Value>{row.note}</Value>,
    },
  ];
  return (
    <section aria-labelledby="consent-history" className="space-y-3">
      <h2 id="consent-history" className="text-lg font-semibold text-ink">
        {t("historyTitle")}
      </h2>
      <DataTable
        stacked
        caption={t("historyTitle")}
        captionHidden
        columns={columns}
        state={{ status: "ready", data: [...entries] }}
        rowKey={(row) => row.id}
        emptyTitle={t("historyEmptyTitle")}
        emptyBody={t("historyEmptyBody")}
      />
    </section>
  );
}

type Step = "idle" | "presign" | "send" | "register" | "submit";

function RecordDecision({
  studentId,
  current,
  version,
}: {
  studentId: string;
  current: ConsentStatus;
  version: number;
}) {
  const t = useTranslations("apaar.record");
  const ta = useTranslations("apaar");
  const tv = useTranslations("apaar.validation");
  const tf = useTranslations("errors.field");
  const tc = useTranslations("common");
  const dates = useDateInput();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const options = nextStatuses(current);
  const [status, setStatus] = useState<ConsentStatus>(options[0] ?? "given");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(undefined);
  const [step, setStep] = useState<Step>("idle");
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
    const parsed = buildConsentSchema().safeParse({ ...raw, status });
    const next: Record<string, string> = {};
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path.map(String).join(".") || "form";
        if (!(field in next)) next[field] = clientMessage(issue.message);
      }
    }
    // PRV-021: consent is recorded only with the signed form.
    if (status === "given" || file) {
      const problem = checkEvidence(file);
      if (problem)
        next.evidence = clientMessage(status === "given" && !file ? "formRequired" : problem);
    }
    setErrors(next);
    setError(undefined);
    setDone(false);
    if (!parsed.success || Object.keys(next).length > 0) {
      requestAnimationFrame(() =>
        form.querySelector<HTMLElement>("[aria-invalid='true']")?.focus(),
      );
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
      const body: ConsentInput = {
        status,
        relationship: status === "pending" ? null : relationship,
        guardian_id: status !== "pending" && data.guardian_id ? data.guardian_id : null,
        decided_on:
          status !== "pending" && data.decided_on
            ? (typedDateToIso(data.decided_on) ?? data.decided_on)
            : null,
        form_language: status === "pending" ? null : language,
        evidence_document_id: documentId,
        note: data.note ? data.note : null,
      };
      await unwrap(
        api.POST("/api/v1/students/{student_id}/apaar-consent", {
          params: { path: { student_id: studentId } },
          headers: { "Idempotency-Key": keys.current.submit, "If-Match": `W/"${version}"` },
          body,
        }),
      );
      keys.current = {
        upload: newIdempotencyKey(),
        register: newIdempotencyKey(),
        submit: newIdempotencyKey(),
      };
      form.reset();
      setDone(true);
      await queryClient.invalidateQueries({ queryKey: APAAR_KEYS.all });
    } catch (failure) {
      if (failure instanceof StorageUploadError) {
        keys.current = {
          ...keys.current,
          upload: newIdempotencyKey(),
          register: newIdempotencyKey(),
        };
        setErrors({ evidence: clientMessage("uploadFailed") });
        return;
      }
      const mapped: Record<string, string> = {};
      let unmapped = true;
      for (const { field, key } of apiFieldErrors(failure)) {
        const target = formField(field);
        if (form.elements.namedItem(target)) {
          unmapped = false;
          if (!(target in mapped)) mapped[target] = translateOr(tf, key, "invalid");
        }
      }
      setErrors(mapped);
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
    label: `${guardian.full_name} · ${ta(`relationships.${guardian.relationship === "father" || guardian.relationship === "mother" ? guardian.relationship : "guardian"}`)}`,
  }));

  return (
    <Card title={t("title")} description={t("description")}>
      {done ? (
        <Alert tone="success" live title={t("savedTitle")}>
          {t("savedBody")}
        </Alert>
      ) : null}
      <form noValidate onSubmit={(event) => void onSubmit(event)} className="space-y-5">
        <SegmentedControl
          legend={t("decision")}
          legendVisible
          value={status}
          onValueChange={(value) => {
            if (isConsentStatus(value)) setStatus(value);
          }}
          options={options.map((value) => ({ value, label: t(`decisions.${value}`) }))}
        />
        {status === "refused" ? <Alert tone="info">{t("refuseNote")}</Alert> : null}
        {status !== "pending" ? (
          <div className="grid gap-4 md:grid-cols-2">
            {guardianOptions.length > 0 ? (
              <SelectField
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
              name="decided_on"
              label={t("decidedOn")}
              hint={tc("dateHintFormat", dates.hint("2026-07-15"))}
              error={errors.decided_on}
              inputMode="numeric"
              placeholder={dates.placeholder}
              autoComplete="off"
            />
            <SelectField
              name="form_language"
              label={t("formLanguage")}
              defaultValue=""
              placeholder={tc("chooseOne")}
              options={FORM_LANGUAGES.map((value) => ({
                value,
                label: ta(`settings.languages.${value}`),
              }))}
            />
          </div>
        ) : null}
        {status !== "pending" ? (
          <Field
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
                className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-border-strong file:bg-surface file:px-3 file:py-2 file:font-semibold"
              />
            )}
          </Field>
        ) : null}
        <TextAreaField
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
        <ApiErrorAlert error={error} namespace="apaar" />
        <div className="flex justify-end">
          <Button type="submit" disabled={busy} aria-disabled={busy || undefined}>
            {busy ? tc("working") : t("submit")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

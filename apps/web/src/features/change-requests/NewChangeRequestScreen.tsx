"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useRef, useState, type FormEvent } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input, TextAreaField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DQ_KEYS, useAttributes } from "@/features/findings/data";
import { isSourceKey, SOURCES, type AttributeDef, type SourceKey } from "@/features/findings/types";
import { Link } from "@/i18n/navigation";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { apiFieldErrors } from "@/lib/api-errors";
import { ApiError, newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { formValues } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import { UUID_PATTERN } from "@/lib/validation";
import { CR_KEYS } from "./ChangeRequestsScreen";
import { displayDateToIso, todayInIndia } from "./dates";
import type { NewRequestParams } from "./filters";
import { DisplayValue } from "./parts";
import { CR_REQUEST, TEXT_MAX, TEXT_MIN, type ChangeRequest, type StudentSummary } from "./types";
import {
  checkEvidence,
  EVIDENCE_ACCEPT,
  StorageUploadError,
  uploadEvidence,
  type UploadStep,
} from "./upload";

const GENDERS = ["female", "male", "transgender"] as const;
/** Same limit as the text inputs; the API allows up to 1000 characters. */
const VALUE_MAX = 200;

/**
 * Client checks mirroring the API (docs/09 change requests); message keys live under
 * `changeRequests.validation`. Dates are typed DD/MM/YYYY and sent as YYYY-MM-DD. Text that
 * looks like a full Aadhaar number is refused before it is sent (invariant 4).
 */
export function buildRequestSchema(attribute: AttributeDef | undefined, today = todayInIndia()) {
  return z
    .object({
      student_id: z.string().trim().regex(UUID_PATTERN, { error: "chooseStudent" }),
      attribute_key: z.string().trim().min(1, { error: "chooseField" }),
      target_source: z
        .string()
        .refine((value) => isSourceKey(value), { error: "chooseSource" })
        .transform((value) => value as SourceKey),
      new_value: z.string().trim().max(VALUE_MAX, { error: "tooLong" }),
      reason: z
        .string()
        .trim()
        .min(1, { error: "reasonRequired" })
        .min(TEXT_MIN, { error: "reasonTooShort" })
        .max(TEXT_MAX, { error: "reasonTooLong" }),
    })
    .superRefine((data, context) => {
      if (containsAadhaarNumber(data.reason)) {
        context.addIssue({ code: "custom", path: ["reason"], message: "noAadhaar" });
      }
      if (data.new_value === "") {
        context.addIssue({ code: "custom", path: ["new_value"], message: "valueRequired" });
        return;
      }
      if (containsAadhaarNumber(data.new_value)) {
        context.addIssue({ code: "custom", path: ["new_value"], message: "noAadhaar" });
        return;
      }
      if (attribute?.data_type === "date") {
        const iso = displayDateToIso(data.new_value);
        if (!iso) {
          context.addIssue({ code: "custom", path: ["new_value"], message: "invalidDate" });
        } else if (iso > today) {
          context.addIssue({ code: "custom", path: ["new_value"], message: "dateInFuture" });
        }
      }
    })
    .transform((data) => ({
      ...data,
      new_value:
        attribute?.data_type === "date"
          ? (displayDateToIso(data.new_value) ?? data.new_value)
          : data.new_value,
    }));
}

type Step = "idle" | UploadStep | "submit";
type EvidenceMode = "upload" | "existing";

function studentLine(
  student: Pick<StudentSummary, "display_name" | "admission_no" | "class_section">,
) {
  return [student.display_name, student.admission_no, student.class_section]
    .filter(Boolean)
    .join(" · ");
}

/** Server field (dotted body path) → form field. */
function formField(field: string): string {
  const name = field.split(".").pop() ?? field;
  if (name === "evidence_document_id") return "evidence";
  if (name === "new_value_date" || name === "value") return "new_value";
  if (name === "source") return "target_source";
  return name;
}

/**
 * New correction request (US-601 AC1, FR-CR-001, BR-04): choose the student and identity field,
 * give the new value, the source it corrects, a reason and an evidence document (uploaded now
 * through the documents flow, or one already uploaded as evidence). Nothing changes on the
 * student's record until someone else approves it (invariant 6, FR-CR-002).
 */
export function NewChangeRequestScreen({ params }: { params: NewRequestParams }) {
  const t = useTranslations("changeRequests.new");
  const tv = useTranslations("changeRequests.validation");
  const tfe = useTranslations("changeRequests.fieldErrors");
  const tsrc = useTranslations("findings.sources");
  const tcr = useTranslations("changeRequests");
  const tc = useTranslations("common");
  const locale = useLocale();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const me = useStaffMeQuery();
  const attributes = useAttributes();
  const searchId = useId();
  const [studentId, setStudentId] = useState<string | null>(params.studentId);
  const [search, setSearch] = useState("");
  const [searchTerm, setSearchTerm] = useState<string | null>(null);
  const [attributeKey, setAttributeKey] = useState<string>(params.attributeKey ?? "");
  const [evidenceMode, setEvidenceMode] = useState<EvidenceMode>("upload");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(undefined);
  const [step, setStep] = useState<Step>("idle");
  const [created, setCreated] = useState<ChangeRequest | null>(null);
  const keys = useRef({
    upload: newIdempotencyKey(),
    register: newIdempotencyKey(),
    submit: newIdempotencyKey(),
  });
  const uploaded = useRef<{ file: File; documentId: string } | null>(null);
  const permissions = me.data?.permissions ?? [];
  const canRequest = permissions.includes(CR_REQUEST);
  const canReadDocuments = permissions.includes("document.read");

  const identity = (attributes.data ?? [])
    .filter((item) => item.is_identity)
    .sort((a, b) => a.sort_order - b.sort_order);
  const attribute = identity.find((item) => item.key === attributeKey);
  const sources = (attribute?.allowed_sources ?? [...SOURCES]).filter(isSourceKey);

  const student = useQuery({
    queryKey: ["staff", "change-requests", "student", studentId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}", {
          params: { path: { student_id: studentId as string } },
        }),
      ),
    enabled: studentId !== null,
    staleTime: 60_000,
    retry: false,
  });
  const results = useQuery({
    queryKey: ["staff", "change-requests", "student-search", searchTerm],
    queryFn: async () =>
      (
        await unwrap(
          api.GET("/api/v1/students", {
            params: { query: { query: searchTerm as string, limit: 20 } },
          }),
        )
      ).data,
    enabled: searchTerm !== null && searchTerm.length >= 2,
    retry: false,
  });
  const documents = useQuery({
    queryKey: ["staff", "change-requests", "evidence-documents"],
    queryFn: async () =>
      (
        await unwrap(
          api.GET("/api/v1/documents", {
            params: { query: { purpose: "evidence", status: "active", limit: 50 } },
          }),
        )
      ).data.filter(
        (item) =>
          item.current_version?.status !== "quarantined" &&
          item.current_version?.status !== "failed",
      ),
    enabled: canReadDocuments && evidenceMode === "existing",
    retry: false,
  });

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!canRequest) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="warning" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const currentValue = attribute ? student.data?.canonical[attribute.key] : undefined;
  const selectedLabel = student.data
    ? [
        student.data.canonical.full_name?.value,
        student.data.admission_no,
        student.data.enrollment?.label,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  const fieldLabelOf = (item: AttributeDef) =>
    locale === "te" && item.label_te ? item.label_te : item.label_en;

  function clientMessage(key: string): string {
    return translateOr(tv, key, "invalid");
  }

  function serverMessage(key: string): string {
    return translateOr(tfe, key, "invalid");
  }

  function resetKeys(which: "all" | "document" | "submit") {
    if (which === "all" || which === "document") {
      keys.current = {
        ...keys.current,
        upload: newIdempotencyKey(),
        register: newIdempotencyKey(),
      };
    }
    if (which === "all" || which === "submit") {
      keys.current = { ...keys.current, submit: newIdempotencyKey() };
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formValues(form);
    const file =
      (form.elements.namedItem("evidence") as HTMLInputElement | null)?.files?.[0] ?? null;
    const existing = raw.evidence_document_id ?? "";
    const parsed = buildRequestSchema(attribute).safeParse({
      ...raw,
      student_id: studentId ?? "",
    });
    const next: Record<string, string> = {};
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path.map(String).join(".") || "form";
        if (!(field in next)) next[field] = clientMessage(issue.message);
      }
    }
    // BR-04: an identity change always needs an evidence document.
    const fileProblem = evidenceMode === "upload" ? checkEvidence(file) : null;
    if (fileProblem) next.evidence = clientMessage(fileProblem);
    if (evidenceMode === "existing" && !UUID_PATTERN.test(existing)) {
      next.evidence_document_id = clientMessage("chooseDocument");
    }
    setErrors(next);
    setError(undefined);
    if (!parsed.success || Object.keys(next).length > 0) {
      requestAnimationFrame(() =>
        form.querySelector<HTMLElement>("[aria-invalid='true']")?.focus(),
      );
      return;
    }
    const data = parsed.data;
    try {
      let documentId: string | null = evidenceMode === "existing" ? existing : null;
      if (!documentId && file) {
        documentId = uploaded.current?.file === file ? uploaded.current.documentId : null;
        if (!documentId) {
          documentId = await uploadEvidence(
            api,
            file,
            t("evidenceDocumentTitle", {
              field: attribute ? fieldLabelOf(attribute) : data.attribute_key,
            }),
            { upload: keys.current.upload, register: keys.current.register },
            setStep,
          );
          uploaded.current = { file, documentId };
        }
      }
      if (!documentId) return;
      setStep("submit");
      const result = await unwrap(
        api.POST("/api/v1/change-requests", {
          headers: { "Idempotency-Key": keys.current.submit },
          body: {
            student_id: data.student_id,
            attribute_key: data.attribute_key,
            target_source: data.target_source,
            reason: data.reason,
            evidence_document_id: documentId,
            ...(attribute?.data_type === "date"
              ? { new_value_date: data.new_value }
              : { new_value: data.new_value }),
          },
        }),
      );
      resetKeys("all");
      uploaded.current = null;
      setCreated(result);
      form.reset();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: CR_KEYS.all }),
        queryClient.invalidateQueries({ queryKey: DQ_KEYS.findings }),
      ]);
    } catch (failure) {
      if (failure instanceof StorageUploadError) {
        // A fresh presigned form next time (the old one may have expired).
        resetKeys("document");
        setErrors({ evidence: clientMessage("uploadFailed") });
        return;
      }
      const fields = apiFieldErrors(failure);
      const mapped: Record<string, string> = {};
      let unmapped = fields.length === 0;
      for (const { field, key } of fields) {
        let target = formField(field);
        if (target === "evidence" && evidenceMode === "existing") target = "evidence_document_id";
        if (form.elements.namedItem(target)) {
          if (!(target in mapped)) mapped[target] = serverMessage(key);
        } else {
          unmapped = true;
        }
      }
      setErrors(mapped);
      if (failure instanceof ApiError && failure.status < 500) {
        resetKeys("submit");
        if (mapped.evidence) {
          uploaded.current = null;
          resetKeys("document");
        }
      }
      if (unmapped || !(failure instanceof ApiError)) setError(failure);
    } finally {
      setStep("idle");
    }
  }

  const busy = step !== "idle";

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      {created ? (
        <Alert tone="success" live title={t("createdTitle")}>
          <p>{t("createdBody")}</p>
          <p className="mt-2 flex flex-wrap gap-x-4">
            <Link href={`/change-requests/${created.id}`} className="font-semibold underline">
              {t("openCreated")}
            </Link>
            {params.findingId ? (
              <Link href={`/findings/${params.findingId}`} className="underline">
                {t("backToFinding")}
              </Link>
            ) : null}
          </p>
        </Alert>
      ) : null}
      <Alert tone="info">{t("makerChecker")}</Alert>
      <form noValidate onSubmit={(event) => void onSubmit(event)} className="space-y-6">
        <Card title={t("studentTitle")}>
          {studentId ? (
            <div className="space-y-3">
              {student.isPending ? (
                <LoadingState label={tc("loading")} rows={1} />
              ) : (
                <p className="font-semibold">{selectedLabel ?? t("studentUnknown")}</p>
              )}
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setStudentId(null);
                  setSearchTerm(null);
                }}
              >
                {t("changeStudent")}
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-end gap-3">
                <Field
                  label={t("search")}
                  hint={t("searchHint")}
                  error={errors.student_id}
                  id={searchId}
                  className="min-w-64 flex-1"
                >
                  {({ id, describedBy, invalid }) => (
                    <Input
                      id={id}
                      type="search"
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          setSearchTerm(search.trim());
                        }
                      }}
                      autoComplete="off"
                      aria-describedby={describedBy}
                      aria-invalid={invalid || undefined}
                    />
                  )}
                </Field>
                <Button variant="secondary" onClick={() => setSearchTerm(search.trim())}>
                  {t("find")}
                </Button>
              </div>
              <div role="status" aria-live="polite">
                {searchTerm !== null && searchTerm.length < 2 ? (
                  <p className="text-sm">{t("searchTooShort")}</p>
                ) : results.isFetching ? (
                  <p className="text-sm">{tc("loading")}</p>
                ) : results.data ? (
                  <p className="text-sm">{t("resultsCount", { count: results.data.length })}</p>
                ) : null}
              </div>
              {results.data && results.data.length > 0 ? (
                <fieldset>
                  <legend className="sr-only">{t("chooseStudent")}</legend>
                  <ul className="divide-y divide-border rounded-md border border-border">
                    {results.data.map((item) => (
                      <li key={item.id}>
                        <label className="flex min-h-10 cursor-pointer items-center gap-3 px-3 py-2 hover:bg-surface-muted">
                          <input
                            type="radio"
                            name="student_choice"
                            value={item.id}
                            className="size-4"
                            onChange={() => setStudentId(item.id)}
                          />
                          <span>{studentLine(item)}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </fieldset>
              ) : null}
            </div>
          )}
        </Card>

        <Card title={t("changeTitle")}>
          <div className="grid gap-4 md:grid-cols-2">
            <SelectField
              name="attribute_key"
              label={t("field")}
              hint={t("fieldHint")}
              error={errors.attribute_key}
              value={attributeKey}
              onChange={(event) => setAttributeKey(event.target.value)}
              placeholder={tc("chooseOne")}
              options={identity.map((item) => ({ value: item.key, label: fieldLabelOf(item) }))}
            />
            <SelectField
              key={attributeKey}
              name="target_source"
              label={t("source")}
              hint={t("sourceHint")}
              error={errors.target_source}
              defaultValue={sources.includes("admission_register") ? "admission_register" : ""}
              placeholder={tc("chooseOne")}
              options={sources.map((source) => ({ value: source, label: tsrc(source) }))}
            />
          </div>
          {currentValue ? (
            <p className="mt-4 text-sm">
              {t("currentValue")}{" "}
              <DisplayValue
                value={currentValue.value}
                masked={currentValue.masked}
                attributeKey={attribute?.key ?? ""}
              />
            </p>
          ) : null}
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            {attribute?.data_type === "enum" ? (
              <SelectField
                key={`value-${attributeKey}`}
                name="new_value"
                label={t("newValue")}
                error={errors.new_value}
                defaultValue=""
                placeholder={tc("chooseOne")}
                options={(attribute.allowed_values ?? []).map((value) => {
                  const gender = GENDERS.find((item) => item === value);
                  return { value, label: gender ? tcr(`genderValues.${gender}`) : value };
                })}
              />
            ) : (
              <Field
                key={`value-${attributeKey}`}
                label={t("newValue")}
                hint={attribute?.data_type === "date" ? tc("dateHint") : t("newTextHint")}
                error={errors.new_value}
              >
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    name="new_value"
                    inputMode={attribute?.data_type === "date" ? "numeric" : undefined}
                    maxLength={VALUE_MAX}
                    autoComplete="off"
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                  />
                )}
              </Field>
            )}
          </div>
          <TextAreaField
            className="mt-4"
            name="reason"
            label={t("reason")}
            hint={t("reasonHint")}
            error={errors.reason}
            maxLength={TEXT_MAX}
            rows={4}
          />
        </Card>

        <Card title={t("evidenceCardTitle")} description={t("evidenceBody")}>
          <div className="space-y-4">
            {canReadDocuments ? (
              <fieldset className="space-y-1">
                <legend className="text-sm font-semibold text-ink">{t("evidenceHow")}</legend>
                <div className="flex flex-wrap gap-x-6">
                  {(["upload", "existing"] as const).map((mode) => (
                    <label key={mode} className="inline-flex min-h-8 items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name="evidence_mode"
                        value={mode}
                        checked={evidenceMode === mode}
                        onChange={() => setEvidenceMode(mode)}
                        className="size-4"
                      />
                      {mode === "upload" ? t("evidenceUpload") : t("evidenceExisting")}
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : null}
            {evidenceMode === "upload" ? (
              <Field label={t("evidenceFile")} hint={t("evidenceHint")} error={errors.evidence}>
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
            ) : (
              <SelectField
                name="evidence_document_id"
                label={t("evidenceDocument")}
                hint={
                  documents.data && documents.data.length === 0
                    ? t("evidenceNoDocuments")
                    : t("evidenceDocumentHint")
                }
                error={errors.evidence_document_id}
                defaultValue=""
                placeholder={documents.isPending ? tc("loading") : tc("chooseOne")}
                options={(documents.data ?? []).map((item) => ({
                  value: item.id,
                  label: `${item.title} · ${formatDate(item.created_at) ?? ""}`,
                }))}
              />
            )}
          </div>
        </Card>

        <div role="status" aria-live="polite" className="text-sm">
          {busy ? t(`step.${step}`) : null}
        </div>
        <ApiErrorAlert error={error} namespace="changeRequests" />
        <div className="flex flex-wrap justify-end gap-3">
          <Link href="/change-requests" className="self-center text-primary underline">
            {tc("cancel")}
          </Link>
          <Button type="submit" disabled={busy} aria-disabled={busy || undefined}>
            {busy ? tc("working") : t("submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}

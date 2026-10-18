"use client";

import { useTranslations } from "next-intl";
import { useRef, useState } from "react";
import * as z from "@/lib/zod";
import { Alert } from "@/components/ui/Alert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { Link, useRouter } from "@/i18n/navigation";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useDateInput } from "@/lib/date-format";
import { useApiForm } from "@/lib/forms";
import { containsFullAadhaar } from "./aadhaar";
import { toIsoDate } from "./dates";
import { GuardedTextField } from "./fields";
import { PERM, useStaffPermissions, type Permissions } from "./me";
import { ProblemAlert } from "./ProblemAlert";
import { PenCheck } from "./PenCheck";
import { penDigits, useSchoolStructure, useSectionOptions } from "./StudentList";
import { STUDENT_STATUSES, VALUE_SOURCES, type Student, type ValueInput } from "./types";

/** Fields in the order of the admission register (PRD §8: tab order follows the paper form). */
export const CREATE_FIELDS = [
  "admission_no",
  "full_name",
  "dob",
  "gender",
  "father_name",
  "mother_name",
] as const;
type CreateField = (typeof CREATE_FIELDS)[number];

const GENDERS = ["female", "male", "transgender"] as const;
/** Where an incoming child's PEN is read from (udise_pen sources; tc_incoming first). */
export const PEN_SOURCES = ["tc_incoming", "udise_plus", "manual_entry"] as const;
export const ADMISSION_KINDS = ["new", "transfer_in"] as const;

const noAadhaar = (value: string) => !containsFullAadhaar(value);
const optionalName = z
  .string()
  .trim()
  .max(120, { error: "tooLong" })
  .refine(noAadhaar, { error: "invalid" });

export const createStudentSchema = z
  .object({
    source: z.enum(VALUE_SOURCES, { error: "chooseOption" }),
    admission_no: z
      .string()
      .trim()
      .max(32, { error: "tooLong" })
      .refine((value) => value === "" || /^[A-Za-z0-9][A-Za-z0-9/._-]{0,31}$/.test(value), {
        error: "invalid",
      }),
    full_name: z
      .string()
      .trim()
      .min(1, { error: "required" })
      .max(120, { error: "tooLong" })
      .refine(noAadhaar, { error: "invalid" }),
    dob: z
      .string()
      .trim()
      .refine((value) => value === "" || toIsoDate(value) !== null, { error: "invalid" })
      .transform((value) => (value === "" ? "" : (toIsoDate(value) ?? ""))),
    gender: z.union([z.enum(GENDERS), z.literal("")], { error: "chooseOption" }),
    father_name: optionalName,
    mother_name: optionalName,
    section_id: z.string().trim(),
    roll_no: z.string().trim().max(16, { error: "tooLong" }),
    status: z.enum(STUDENT_STATUSES, { error: "chooseOption" }),
    // FR-STU-017/018 (ADR-0039): the UDISE+ PEN, 11 digits, and where it was read from.
    udise_pen: z
      .string()
      .trim()
      .refine((value) => value === "" || penDigits(value) !== null, { error: "penInvalid" })
      .transform((value) => (value === "" ? "" : (penDigits(value) ?? "")))
      .default(""),
    pen_source: z.enum(PEN_SOURCES, { error: "chooseOption" }).default("tc_incoming"),
    admission_kind: z.enum(ADMISSION_KINDS, { error: "chooseOption" }).default("new"),
  })
  .refine((data) => data.admission_kind !== "transfer_in" || data.udise_pen !== "", {
    path: ["udise_pen"],
    error: "penRequiredForTransfer",
  });
export type CreateStudentInput = z.output<typeof createStudentSchema>;

/** Form values → POST /students body: one value per filled field, all from the chosen source;
 * the PEN from its own source (the TC, UDISE+ or the office). */
export function createBody(data: CreateStudentInput) {
  const values: ValueInput[] = CREATE_FIELDS.filter((key) => data[key] !== "").map((key) => ({
    attribute_key: key,
    source: data.source,
    value: data[key],
  }));
  if (data.udise_pen) {
    values.push({ attribute_key: "udise_pen", source: data.pen_source, value: data.udise_pen });
  }
  return {
    values,
    status: data.status,
    admission_kind: data.admission_kind,
    ...(data.section_id ? { section_id: data.section_id } : {}),
    ...(data.section_id && data.roll_no ? { roll_no: data.roll_no } : {}),
  };
}

/** The record named by a `national_id_in_use` field error (FR-STU-018), if the API gave it. */
export function existingRecordOf(failure: unknown): string | null {
  if (!(failure instanceof ApiError)) return null;
  const errors: unknown = failure.problem.errors;
  if (!Array.isArray(errors)) return null;
  for (const item of errors as unknown[]) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as { code?: unknown; student_id?: unknown };
    if (entry.code === "national_id_in_use" && typeof entry.student_id === "string") {
      return entry.student_id;
    }
  }
  return null;
}

export interface CreateStudentFormProps {
  permissions: Permissions;
  onCreated?: (student: Student) => void;
}

/** US-301 / FR-STU-001..003: add a student with first values, each with its source. */
export function CreateStudentForm({ permissions, onCreated }: CreateStudentFormProps) {
  const t = useTranslations("students.create");
  const ts = useTranslations("students");
  const dates = useDateInput();
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const sent = useRef<string[]>([]);
  const [pen, setPen] = useState("");
  const [clash, setClash] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const form = useApiForm({
    schema: createStudentSchema,
    invalidate: [["staff", "students"]],
    submit: async (data, key) => {
      const body = createBody(data);
      sent.current = body.values.map((value) => value.attribute_key);
      setClash(null);
      try {
        return await unwrap(
          api.POST("/api/v1/students", { headers: { "Idempotency-Key": key }, body }),
        );
      } catch (failure) {
        // FR-STU-018: the number is already on a record of this school; offer to open it.
        setClash(existingRecordOf(failure));
        throw failure;
      }
    },
    // `values.2.value` → the form field of the third value sent.
    fieldMap: (field) => {
      const match = /^values\.(\d+)\./.exec(field);
      if (match) return sent.current[Number(match[1])];
      return field.split(".").pop();
    },
    onInvalid: () => setAttempt((value) => value + 1),
    onSuccess: (student) => {
      setDirty(false);
      onCreated?.(student);
    },
  });

  if (permissions.loaded && !permissions.has(PERM.create)) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const dateHint = ts("dateHint", dates.hint("2012-03-14"));
  const err = (field: CreateField) =>
    form.errors[field] && field === "dob"
      ? ts("dateInvalid", dates.hint("2012-03-14"))
      : form.errors[field];
  const id = (name: string) => `create-student-${name}`;
  const summary = Object.keys(form.errors)
    .filter((name) => name in FIELD_ORDER && form.errors[name])
    .sort((a, b) => (FIELD_ORDER[a] ?? 0) - (FIELD_ORDER[b] ?? 0))
    .map((name) => ({
      fieldId: id(name),
      message: (CREATE_FIELDS as readonly string[]).includes(name)
        ? (err(name as CreateField) ?? "")
        : (form.errors[name] ?? ""),
    }));

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: ts("list.crumbHome"), href: "/" },
          { label: ts("list.title"), href: "/students" },
          { label: t("title") },
        ]}
      />
      <Alert tone="warning" title={ts("aadhaarWarningTitle")}>
        {ts("aadhaarWarningBody")}
      </Alert>
      <form
        noValidate
        onSubmit={form.onSubmit}
        onChange={() => setDirty(true)}
        className="space-y-4"
      >
        <ErrorSummary errors={summary} attempt={attempt} />
        <Card title={t("detailsLegend")}>
          <div className="space-y-4">
            <SelectField
              id={id("source")}
              name="source"
              label={t("source")}
              hint={t("sourceHint")}
              defaultValue="admission_register"
              error={form.errors.source}
              options={VALUE_SOURCES.map((value) => ({ value, label: ts(`sources.${value}`) }))}
            />
            <div className="grid gap-4 md:grid-cols-2">
              <GuardedTextField
                id={id("admission_no")}
                name="admission_no"
                label={t("admissionNo")}
                hint={t("admissionNoHint")}
                error={err("admission_no")}
                maxLength={32}
                autoComplete="off"
                spellCheck={false}
              />
              <GuardedTextField
                id={id("full_name")}
                name="full_name"
                label={t("fullName")}
                hint={t("fullNameHint")}
                error={err("full_name")}
                maxLength={120}
                autoComplete="off"
                required
                aria-required="true"
              />
              <GuardedTextField
                id={id("dob")}
                name="dob"
                label={t("dob")}
                hint={dateHint}
                error={err("dob")}
                inputMode="numeric"
                placeholder={dates.placeholder}
                autoComplete="off"
              />
              <SelectField
                id={id("gender")}
                name="gender"
                label={t("gender")}
                placeholder={tc("chooseOne")}
                error={form.errors.gender}
                options={GENDERS.map((value) => ({ value, label: ts(`enumValues.${value}`) }))}
              />
              <GuardedTextField
                id={id("father_name")}
                name="father_name"
                label={t("fatherName")}
                error={err("father_name")}
                maxLength={120}
                autoComplete="off"
              />
              <GuardedTextField
                id={id("mother_name")}
                name="mother_name"
                label={t("motherName")}
                error={err("mother_name")}
                maxLength={120}
                autoComplete="off"
              />
            </div>
          </div>
        </Card>
        <Card title={t("udiseLegend")}>
          <div className="space-y-4">
            <SegmentedControl
              name="admission_kind"
              legend={t("admissionKind")}
              legendVisible
              size="sm"
              defaultValue="new"
              options={ADMISSION_KINDS.map((value) => ({
                value,
                label: t(`admissionKinds.${value}`),
              }))}
            />
            <p className="text-sm text-ink-muted">{t("transferHint")}</p>
            <div className="grid gap-4 md:grid-cols-2">
              <GuardedTextField
                id={id("udise_pen")}
                name="udise_pen"
                label={t("pen")}
                hint={t("penHint")}
                error={form.errors.udise_pen}
                value={pen}
                onChange={(event) => setPen(event.currentTarget.value)}
                maxLength={15}
                inputMode="numeric"
                autoComplete="off"
                spellCheck={false}
              />
              <SelectField
                id={id("pen_source")}
                name="pen_source"
                label={t("penSource")}
                defaultValue="tc_incoming"
                error={form.errors.pen_source}
                options={PEN_SOURCES.map((value) => ({ value, label: ts(`sources.${value}`) }))}
              />
            </div>
            <PenCheck pen={pen} />
            {clash ? (
              <Alert tone="warning" title={t("clashTitle")}>
                <p>{t("clashBody")}</p>
                <Link href={`/students/${clash}`} className="font-semibold underline">
                  {t("openExisting")}
                </Link>
              </Alert>
            ) : null}
          </div>
        </Card>
        <Card title={t("classLegend")}>
          <div className="grid gap-4 md:grid-cols-2">
            <SelectField
              id={id("section_id")}
              name="section_id"
              label={t("section")}
              hint={t("sectionHint")}
              placeholder={t("noSection")}
              error={form.errors.section_id}
              options={sections}
            />
            <GuardedTextField
              id={id("roll_no")}
              name="roll_no"
              label={t("rollNo")}
              error={form.errors.roll_no}
              maxLength={16}
              autoComplete="off"
            />
            <SelectField
              id={id("status")}
              name="status"
              label={t("status")}
              hint={t("statusHint")}
              defaultValue="active"
              error={form.errors.status}
              options={STUDENT_STATUSES.map((value) => ({ value, label: ts(`status.${value}`) }))}
            />
          </div>
        </Card>
        <ProblemAlert error={form.error} namespace="students.errors" />
        <SaveBar dirty={dirty}>
          <ButtonLink href="/students" variant="secondary">
            {tc("cancel")}
          </ButtonLink>
          <Button type="submit" disabled={form.pending}>
            {form.pending ? tc("working") : t("submit")}
          </Button>
        </SaveBar>
      </form>
    </div>
  );
}

/** Form order of the fields, for the error summary (the same order as on the page). */
const FIELD_ORDER: Record<string, number> = Object.fromEntries(
  ["source", ...CREATE_FIELDS, "udise_pen", "pen_source", "section_id", "roll_no", "status"].map(
    (name, index) => [name, index],
  ),
);

export function CreateStudentScreen() {
  const permissions = useStaffPermissions();
  const router = useRouter();
  return (
    <CreateStudentForm
      permissions={permissions}
      onCreated={(student) => router.push(`/students/${student.id}`)}
    />
  );
}

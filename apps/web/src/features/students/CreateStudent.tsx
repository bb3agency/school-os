"use client";

import { useTranslations } from "next-intl";
import { useRef } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useApiForm } from "@/lib/forms";
import { containsFullAadhaar } from "./aadhaar";
import { toIsoDate } from "./dates";
import { GuardedTextField } from "./fields";
import { PERM, useStaffPermissions, type Permissions } from "./me";
import { ProblemAlert } from "./ProblemAlert";
import { useSchoolStructure, useSectionOptions } from "./StudentList";
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

const noAadhaar = (value: string) => !containsFullAadhaar(value);
const optionalName = z
  .string()
  .trim()
  .max(120, { error: "tooLong" })
  .refine(noAadhaar, { error: "invalid" });

export const createStudentSchema = z.object({
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
});
export type CreateStudentInput = z.output<typeof createStudentSchema>;

/** Form values → POST /students body: one value per filled field, all from the chosen source. */
export function createBody(data: CreateStudentInput) {
  const values: ValueInput[] = CREATE_FIELDS.filter((key) => data[key] !== "").map((key) => ({
    attribute_key: key,
    source: data.source,
    value: data[key],
  }));
  return {
    values,
    status: data.status,
    ...(data.section_id ? { section_id: data.section_id } : {}),
    ...(data.section_id && data.roll_no ? { roll_no: data.roll_no } : {}),
  };
}

export interface CreateStudentFormProps {
  permissions: Permissions;
  onCreated?: (student: Student) => void;
}

/** US-301 / FR-STU-001..003: add a student with first values, each with its source. */
export function CreateStudentForm({ permissions, onCreated }: CreateStudentFormProps) {
  const t = useTranslations("students.create");
  const ts = useTranslations("students");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const sent = useRef<string[]>([]);

  const form = useApiForm({
    schema: createStudentSchema,
    invalidate: [["staff", "students"]],
    submit: (data, key) => {
      const body = createBody(data);
      sent.current = body.values.map((value) => value.attribute_key);
      return unwrap(
        api.POST("/api/v1/students", { headers: { "Idempotency-Key": key }, body }),
      );
    },
    // `values.2.value` → the form field of the third value sent.
    fieldMap: (field) => {
      const match = /^values\.(\d+)\./.exec(field);
      if (match) return sent.current[Number(match[1])];
      return field.split(".").pop();
    },
    onSuccess: (student) => onCreated?.(student),
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

  const dateHint = ts("dateHint");
  const err = (field: CreateField) =>
    form.errors[field] && field === "dob" ? ts("dateInvalid") : form.errors[field];

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <Alert tone="warning" title={ts("aadhaarWarningTitle")}>
        {ts("aadhaarWarningBody")}
      </Alert>
      <Card>
        <form noValidate onSubmit={form.onSubmit} className="space-y-6">
          <SelectField
            name="source"
            label={t("source")}
            hint={t("sourceHint")}
            defaultValue="admission_register"
            error={form.errors.source}
            options={VALUE_SOURCES.map((value) => ({ value, label: ts(`sources.${value}`) }))}
          />
          <fieldset className="grid gap-4 md:grid-cols-2">
            <legend className="mb-2 text-lg font-semibold">{t("detailsLegend")}</legend>
            <GuardedTextField
              name="admission_no"
              label={t("admissionNo")}
              hint={t("admissionNoHint")}
              error={err("admission_no")}
              maxLength={32}
              autoComplete="off"
              spellCheck={false}
            />
            <GuardedTextField
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
              name="dob"
              label={t("dob")}
              hint={dateHint}
              error={err("dob")}
              inputMode="numeric"
              placeholder="DD/MM/YYYY"
              autoComplete="off"
            />
            <SelectField
              name="gender"
              label={t("gender")}
              placeholder={tc("chooseOne")}
              error={form.errors.gender}
              options={GENDERS.map((value) => ({ value, label: ts(`enumValues.${value}`) }))}
            />
            <GuardedTextField
              name="father_name"
              label={t("fatherName")}
              error={err("father_name")}
              maxLength={120}
              autoComplete="off"
            />
            <GuardedTextField
              name="mother_name"
              label={t("motherName")}
              error={err("mother_name")}
              maxLength={120}
              autoComplete="off"
            />
          </fieldset>
          <fieldset className="grid gap-4 md:grid-cols-2">
            <legend className="mb-2 text-lg font-semibold">{t("classLegend")}</legend>
            <SelectField
              name="section_id"
              label={t("section")}
              hint={t("sectionHint")}
              placeholder={t("noSection")}
              error={form.errors.section_id}
              options={sections}
            />
            <GuardedTextField
              name="roll_no"
              label={t("rollNo")}
              error={form.errors.roll_no}
              maxLength={16}
              autoComplete="off"
            />
            <SelectField
              name="status"
              label={t("status")}
              hint={t("statusHint")}
              defaultValue="active"
              error={form.errors.status}
              options={STUDENT_STATUSES.map((value) => ({ value, label: ts(`status.${value}`) }))}
            />
          </fieldset>
          <ProblemAlert error={form.error} namespace="students.errors" />
          <div className="flex flex-wrap justify-end gap-2">
            <Link
              href="/students"
              className="inline-flex min-h-10 items-center rounded-md px-4 py-2 text-sm font-semibold text-primary underline"
            >
              {tc("cancel")}
            </Link>
            <Button type="submit" disabled={form.pending}>
              {form.pending ? tc("working") : t("submit")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

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

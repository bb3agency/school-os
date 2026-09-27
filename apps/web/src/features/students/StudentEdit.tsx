"use client";

import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/Select";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { UUID_PATTERN } from "@/lib/validation";
import { containsFullAadhaar } from "./aadhaar";
import { toIsoDate } from "./dates";
import { GuardedTextField } from "./fields";
import { FormDialog } from "./FormDialog";
import { useSchoolStructure, useSectionOptions } from "./StudentList";
import { STUDENT_STATUSES, type Guardian, type Student } from "./types";

/**
 * Direct edits of a student's record that are not identity fields (US-301, FR-STU-004/005):
 * record status, class and section, parents and guardians. Identity fields (name, date of
 * birth, admission number…) are never edited here: they go through a correction request with
 * evidence (invariant 6, FR-CR-001), linked from the details table.
 */

/** Query keys refreshed after an edit (the student page and every student list). */
const STUDENTS_KEY = ["staff", "students"] as const;
export const guardiansKey = (studentId: string) =>
  ["staff", "students", studentId, "guardians"] as const;

/** The API's ETag for a version (docs/09 §2: `W/"3"`). */
export function etag(version: number): string {
  return `W/"${version}"`;
}

const noAadhaar = (value: string) => !containsFullAadhaar(value);

/** True for a stale If-Match (412) or a state conflict the latest data would explain. */
export function isStaleConflict(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return (
    error.status === 412 ||
    (error.status === 409 && (error.code === "conflict" || error.code === "version_conflict"))
  );
}

/**
 * Shown under a version conflict: reload the record (the dialog stays open, so the clerk can
 * check what changed and submit again with the new version).
 */
export function ReloadAction({ keys }: { keys: readonly QueryKey[] }) {
  const t = useTranslations("students.edit");
  const queryClient = useQueryClient();
  const [state, setState] = useState<"idle" | "loading" | "done">("idle");
  if (state === "done") {
    return (
      <p role="status" className="font-semibold">
        {t("reloaded")}
      </p>
    );
  }
  return (
    <Button
      size="sm"
      variant="secondary"
      disabled={state === "loading"}
      onClick={() => {
        setState("loading");
        void Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })))
          .catch(() => undefined)
          .then(() => setState("done"));
      }}
    >
      {t("reload")}
    </Button>
  );
}

/** A FormDialog `problemAction` that offers "Reload" under a version conflict. */
export function reloadOnConflict(keys: readonly QueryKey[]) {
  function ReloadOnConflict(error: unknown) {
    return isStaleConflict(error) ? <ReloadAction keys={keys} /> : null;
  }
  return ReloadOnConflict;
}

/* ------------------------------------------------------------------ record status */

const statusSchema = z.object({ status: z.enum(STUDENT_STATUSES, { error: "chooseOption" }) });

/** PATCH /students/{id} with If-Match (the only field it takes is `status`). */
export function StatusDialog({ student }: { student: Student }) {
  const t = useTranslations("students.edit");
  const ts = useTranslations("students");
  const api = useBffClient("staff");
  const keys = [STUDENTS_KEY];
  return (
    <FormDialog
      triggerLabel={t("statusOpen")}
      title={t("statusTitle")}
      description={t("statusDescription")}
      confirmLabel={t("statusSubmit")}
      problems="students.errors"
      problemAction={reloadOnConflict(keys)}
      schema={statusSchema}
      invalidate={keys}
      submit={(data) =>
        unwrap(
          api.PATCH("/api/v1/students/{student_id}", {
            params: { path: { student_id: student.id } },
            headers: { "If-Match": etag(student.version) },
            body: { status: data.status },
          }),
        )
      }
    >
      {(errors) => (
        <SelectField
          name="status"
          label={t("status")}
          hint={t("statusHint")}
          defaultValue={student.status}
          error={errors.status}
          options={STUDENT_STATUSES.map((value) => ({ value, label: ts(`status.${value}`) }))}
        />
      )}
    </FormDialog>
  );
}

/* ------------------------------------------------------------------ enrolment */

export const enrolmentSchema = z.object({
  section_id: z.string().trim().regex(UUID_PATTERN, { error: "chooseOption" }),
  roll_no: z
    .string()
    .trim()
    .max(16, { error: "tooLong" })
    .refine(noAadhaar, { error: "invalid" })
    .transform((value) => (value === "" ? null : value)),
  started_on: z
    .string()
    .trim()
    .refine((value) => value === "" || toIsoDate(value) !== null, { error: "invalid" })
    .transform((value) => (value === "" ? null : toIsoDate(value))),
});

/**
 * POST /students/{id}/enrollments: put the student in a section of the current year. An
 * active enrolment in that year ends as "transferred" (FR-STU-005); the same section again
 * is refused (409 `already_enrolled`).
 */
export function EnrolmentDialog({ student }: { student: Student }) {
  const t = useTranslations("students.edit");
  const ts = useTranslations("students");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  return (
    <FormDialog
      triggerLabel={student.enrollment ? t("enrolOpen") : t("enrolOpenNew")}
      title={t("enrolTitle")}
      description={t("enrolDescription")}
      confirmLabel={t("enrolSubmit")}
      problems="students.errors"
      schema={enrolmentSchema}
      invalidate={[STUDENTS_KEY]}
      submit={(data, idempotencyKey) =>
        unwrap(
          api.POST("/api/v1/students/{student_id}/enrollments", {
            params: { path: { student_id: student.id } },
            headers: { "Idempotency-Key": idempotencyKey },
            body: {
              section_id: data.section_id,
              ...(data.roll_no ? { roll_no: data.roll_no } : {}),
              ...(data.started_on ? { started_on: data.started_on } : {}),
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="section_id"
            label={t("section")}
            hint={t("sectionHint")}
            placeholder={tc("chooseOne")}
            error={errors.section_id}
            options={sections}
          />
          <GuardedTextField
            name="roll_no"
            label={t("rollNo")}
            error={errors.roll_no}
            maxLength={16}
            autoComplete="off"
          />
          <GuardedTextField
            name="started_on"
            label={t("startedOn")}
            hint={t("startedOnHint")}
            error={errors.started_on ? ts("dateInvalid") : undefined}
            inputMode="numeric"
            placeholder="DD/MM/YYYY"
            autoComplete="off"
          />
        </>
      )}
    </FormDialog>
  );
}

/* ------------------------------------------------------------------ guardians */

const RELATIONSHIPS = ["father", "mother", "guardian"] as const;
type Relationship = (typeof RELATIONSHIPS)[number];
const checkbox = z
  .string()
  .optional()
  .transform((value) => value === "on");

/** 10–13 digits, optionally with +, spaces or hyphens (the API allows 6–20 characters). */
function validPhone(value: string): boolean {
  if (!/^\+?[0-9][0-9 -]*$/.test(value)) return false;
  const digits = value.replace(/\D/g, "").length;
  return digits >= 10 && digits <= 13 && value.length <= 20;
}

export const guardianSchema = z.object({
  full_name: z
    .string()
    .trim()
    .min(1, { error: "required" })
    .max(120, { error: "tooLong" })
    .refine(noAadhaar, { error: "invalid" }),
  relationship: z.enum(RELATIONSHIPS, { error: "chooseOption" }),
  phone: z
    .string()
    .trim()
    .refine(noAadhaar, { error: "invalid" })
    .refine((value) => value === "" || validPhone(value), { error: "invalidPhone" }),
  address: z.string().trim().max(500, { error: "tooLong" }).refine(noAadhaar, { error: "invalid" }),
  is_primary: checkbox,
  clear_phone: checkbox,
  clear_address: checkbox,
});
export type GuardianInput = z.output<typeof guardianSchema>;

/** POST body: empty optional fields are left out. */
export function guardianCreateBody(data: GuardianInput) {
  return {
    full_name: data.full_name.trim(),
    relationship: data.relationship,
    ...(data.phone ? { phone: data.phone } : {}),
    ...(data.address ? { address: data.address } : {}),
    is_primary: data.is_primary,
  };
}

/**
 * PATCH body with only what changed. Phone and address are restricted (C3) and never
 * pre-filled: empty means "keep", and the "remove" boxes send `null` (docs/09).
 */
export function guardianPatchBody(current: Guardian, data: GuardianInput) {
  const body: {
    full_name?: string;
    relationship?: Relationship;
    phone?: string | null;
    address?: string | null;
    is_primary?: boolean;
  } = {};
  if (data.full_name !== current.full_name) body.full_name = data.full_name;
  if (data.relationship !== current.relationship) body.relationship = data.relationship;
  if (data.clear_phone) body.phone = null;
  else if (data.phone) body.phone = data.phone;
  if (data.clear_address) body.address = null;
  else if (data.address) body.address = data.address;
  if (data.is_primary !== current.is_primary) body.is_primary = data.is_primary;
  return body;
}

/**
 * Remove a parent/guardian from this student (DELETE with the guardian's ETag), after a
 * confirmation that says what happens: a guardian no other student is linked to is deleted
 * with their phone number and address; one shared with a sibling stays for the sibling.
 */
export function RemoveGuardianDialog({
  studentId,
  guardian,
}: {
  studentId: string;
  guardian: Guardian;
}) {
  const t = useTranslations("students.guardians");
  const api = useBffClient("staff");
  const keys = [guardiansKey(studentId)];
  return (
    <FormDialog
      triggerLabel={
        <>
          {t("remove")}
          <span className="sr-only">: {guardian.full_name}</span>
        </>
      }
      triggerVariant="ghost"
      triggerSize="sm"
      title={t("removeTitle", { name: guardian.full_name })}
      description={t("removeBody")}
      confirmLabel={t("removeSubmit")}
      confirmVariant="danger"
      problems="students.errors"
      problemAction={reloadOnConflict(keys)}
      schema={z.object({})}
      invalidate={[STUDENTS_KEY]}
      submit={() =>
        unwrap(
          api.DELETE("/api/v1/students/{student_id}/guardians/{guardian_id}", {
            params: { path: { student_id: studentId, guardian_id: guardian.id } },
            headers: { "If-Match": etag(guardian.version) },
          }),
        )
      }
    >
      {() => <p className="text-sm">{t("removeNote")}</p>}
    </FormDialog>
  );
}

/** Add a parent/guardian (POST, Idempotency-Key) or edit one (PATCH, the guardian's ETag). */
export function GuardianDialog({
  studentId,
  guardian,
}: {
  studentId: string;
  guardian?: Guardian | undefined;
}) {
  const t = useTranslations("students.guardians");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const keys = [guardiansKey(studentId)];
  const editing = guardian !== undefined;
  const relationship = (value: Relationship) => t(`relationship.${value}`);

  return (
    <FormDialog
      triggerLabel={
        editing ? (
          <>
            {t("edit")}
            <span className="sr-only">: {guardian.full_name}</span>
          </>
        ) : (
          t("add")
        )
      }
      triggerVariant={editing ? "ghost" : "secondary"}
      triggerSize={editing ? "sm" : "md"}
      title={editing ? t("editTitle") : t("addTitle")}
      description={t("formDescription")}
      confirmLabel={editing ? t("editSubmit") : t("addSubmit")}
      problems="students.errors"
      problemAction={reloadOnConflict(keys)}
      schema={guardianSchema}
      invalidate={keys}
      submit={(data, idempotencyKey) => {
        if (!editing) {
          return unwrap(
            api.POST("/api/v1/students/{student_id}/guardians", {
              params: { path: { student_id: studentId } },
              headers: { "Idempotency-Key": idempotencyKey },
              body: guardianCreateBody(data),
            }),
          );
        }
        const body = guardianPatchBody(guardian, data);
        if (Object.keys(body).length === 0) return Promise.resolve(guardian);
        return unwrap(
          api.PATCH("/api/v1/students/{student_id}/guardians/{guardian_id}", {
            params: { path: { student_id: studentId, guardian_id: guardian.id } },
            headers: { "If-Match": etag(guardian.version) },
            body,
          }),
        );
      }}
    >
      {(errors) => (
        <>
          <GuardedTextField
            name="full_name"
            label={t("fullName")}
            error={errors.full_name}
            defaultValue={guardian?.full_name ?? ""}
            maxLength={120}
            autoComplete="off"
            required
            aria-required="true"
          />
          <SelectField
            name="relationship"
            label={t("relationshipLabel")}
            placeholder={tc("chooseOne")}
            defaultValue={guardian?.relationship ?? ""}
            error={errors.relationship}
            options={RELATIONSHIPS.map((value) => ({ value, label: relationship(value) }))}
          />
          <GuardedTextField
            name="phone"
            label={t("phone")}
            hint={editing && guardian.has_phone ? t("keepHint") : t("phoneHint")}
            error={errors.phone}
            maxLength={20}
            inputMode="tel"
            autoComplete="off"
          />
          {editing && guardian.has_phone ? (
            <label className="flex min-h-8 items-start gap-2 text-sm">
              <input type="checkbox" name="clear_phone" value="on" className="mt-0.5 size-4" />
              <span>{t("clearPhone")}</span>
            </label>
          ) : null}
          <GuardedTextField
            name="address"
            label={t("address")}
            {...(editing && guardian.has_address ? { hint: t("keepHint") } : {})}
            error={errors.address}
            maxLength={500}
            autoComplete="off"
          />
          {editing && guardian.has_address ? (
            <label className="flex min-h-8 items-start gap-2 text-sm">
              <input type="checkbox" name="clear_address" value="on" className="mt-0.5 size-4" />
              <span>{t("clearAddress")}</span>
            </label>
          ) : null}
          <label className="flex min-h-8 items-start gap-2 text-sm">
            <input
              type="checkbox"
              name="is_primary"
              value="on"
              defaultChecked={guardian?.is_primary ?? false}
              className="mt-0.5 size-4"
            />
            <span>{t("isPrimary")}</span>
          </label>
        </>
      )}
    </FormDialog>
  );
}

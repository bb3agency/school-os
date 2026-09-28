"use client";

import type { components } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useId } from "react";
import { z } from "zod";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useDateInput } from "@/lib/date-format";
import { formatDate } from "@/lib/format";
import { classLabel } from "@/lib/school-class";
import { containsFullAadhaar } from "./aadhaar";
import { toIsoDate } from "./dates";
import { GuardedTextField } from "./fields";
import { FormDialog } from "./FormDialog";
import { PERM, type Permissions } from "./me";
import { EnrolmentDialog, etag, isStaleConflict, ReloadAction } from "./StudentEdit";
import { useSchoolStructure } from "./StudentList";
import { problemCode } from "./ProblemAlert";
import type { Student } from "./types";

/**
 * A student's enrolments (US-301, FR-STU-005): every year, active or closed, newest first
 * (GET /students/{id}/enrollments). With `student.update_nonidentity` a clerk corrects the
 * roll number, moves an active enrolment to another section of the same class and year, or
 * ends it as completed or transferred. Each change sends the enrolment's ETag (If-Match); the
 * API audits it. Moving to another class is a new enrolment ("Change class or section").
 */

type Enrolment = components["schemas"]["EnrollmentOut"];
type Structure = ReturnType<typeof useSchoolStructure>;

export const enrolmentsKey = (studentId: string) =>
  ["staff", "students", studentId, "enrollments"] as const;
/** Everything about students (the page, lists, enrolments): refreshed after a change. */
const STUDENTS_KEY = ["staff", "students"] as const;

const ENROLMENT_STATUSES = ["active", "completed", "transferred"] as const;
type EnrolmentStatus = (typeof ENROLMENT_STATUSES)[number];
const END_STATUSES = ["completed", "transferred"] as const;

function isEnrolmentStatus(value: string): value is EnrolmentStatus {
  return (ENROLMENT_STATUSES as readonly string[]).includes(value);
}

const statusTone: Record<EnrolmentStatus, BadgeTone> = {
  active: "success",
  completed: "neutral",
  transferred: "info",
};

/* ------------------------------------------------------------------ forms */

export const enrolmentPatchSchema = z.object({
  roll_no: z
    .string()
    .trim()
    .max(16, { error: "tooLong" })
    .refine((value) => !containsFullAadhaar(value), { error: "invalid" }),
  section_id: z.string().trim().optional().default(""),
});
export type EnrolmentPatchInput = z.output<typeof enrolmentPatchSchema>;

/** PATCH body with only what changed: an emptied roll number is `null` (cleared). */
export function enrolmentPatchBody(current: Enrolment, data: EnrolmentPatchInput) {
  const body: { roll_no?: string | null; section_id?: string } = {};
  const roll = data.roll_no === "" ? null : data.roll_no;
  if (roll !== current.roll_no) body.roll_no = roll;
  if (data.section_id && data.section_id !== current.section_id) body.section_id = data.section_id;
  return body;
}

export const endEnrolmentSchema = z.object({
  status: z.enum(END_STATUSES, { error: "chooseOption" }),
  ended_on: z
    .string()
    .trim()
    .refine((value) => value === "" || toIsoDate(value) !== null, { error: "invalid" })
    .transform((value) => (value === "" ? null : toIsoDate(value))),
});

/** 412 and a closed enrolment both mean "your page is out of date": offer a reload. */
function reloadAction(error: unknown) {
  return isStaleConflict(error) || problemCode(error) === "enrollment_not_active" ? (
    <ReloadAction keys={[STUDENTS_KEY]} />
  ) : null;
}

/* ------------------------------------------------------------------ labels */

function useEnrolmentLabels(structure: Structure) {
  const locale = useLocale();
  const years = structure.years.status === "ready" ? structure.years.data : [];
  const classes = structure.classes.status === "ready" ? structure.classes.data : [];
  const sections = structure.sections.status === "ready" ? structure.sections.data : [];
  const sectionLabel = (sectionId: string): string | null => {
    const section = sections.find((item) => item.id === sectionId);
    if (!section) return null;
    const parent = classes.find((item) => item.id === section.class_id);
    return `${parent ? classLabel(parent, locale) : ""} · ${section.name}`;
  };
  const yearLabel = (yearId: string): string | null =>
    years.find((item) => item.id === yearId)?.label ?? null;
  /** Other sections of the same class and year (never archived ones), for a move. */
  const siblings = (enrolment: Enrolment) => {
    const current = sections.find((item) => item.id === enrolment.section_id);
    if (!current) return [];
    return sections
      .filter(
        (item) =>
          item.class_id === current.class_id &&
          item.academic_year_id === current.academic_year_id &&
          (item.id === current.id || !item.archived_at),
      )
      .map((item) => ({ value: item.id, label: sectionLabel(item.id) ?? item.name }))
      .sort((a, b) => a.label.localeCompare(b.label));
  };
  return { sectionLabel, yearLabel, siblings };
}

/* ------------------------------------------------------------------ dialogs */

function CorrectEnrolmentDialog({
  studentId,
  enrolment,
  rowLabel,
  options,
}: {
  studentId: string;
  enrolment: Enrolment;
  rowLabel: string;
  options: readonly { value: string; label: string }[];
}) {
  const t = useTranslations("students.enrolments");
  const api = useBffClient("staff");
  const canMove = enrolment.status === "active" && options.length > 1;
  return (
    <FormDialog
      triggerLabel={
        <>
          {t("edit")}
          <span className="sr-only">: {rowLabel}</span>
        </>
      }
      triggerVariant="ghost"
      triggerSize="sm"
      title={t("editTitle")}
      description={canMove ? t("editDescription") : t("editDescriptionClosed")}
      confirmLabel={t("editSubmit")}
      problems="students.errors"
      problemAction={reloadAction}
      schema={enrolmentPatchSchema}
      invalidate={[STUDENTS_KEY]}
      submit={(data) => {
        const body = enrolmentPatchBody(enrolment, data);
        if (Object.keys(body).length === 0) return Promise.resolve(enrolment);
        return unwrap(
          api.PATCH("/api/v1/students/{student_id}/enrollments/{enrollment_id}", {
            params: { path: { student_id: studentId, enrollment_id: enrolment.id } },
            headers: { "If-Match": etag(enrolment.version) },
            body,
          }),
        );
      }}
    >
      {(errors) => (
        <>
          {canMove ? (
            <SelectField
              name="section_id"
              label={t("section")}
              hint={t("sectionHint")}
              defaultValue={enrolment.section_id}
              error={errors.section_id}
              options={options}
            />
          ) : null}
          <GuardedTextField
            name="roll_no"
            label={t("rollNo")}
            hint={t("rollNoHint")}
            defaultValue={enrolment.roll_no ?? ""}
            error={errors.roll_no}
            maxLength={16}
            autoComplete="off"
          />
        </>
      )}
    </FormDialog>
  );
}

function EndEnrolmentDialog({
  studentId,
  enrolment,
  rowLabel,
}: {
  studentId: string;
  enrolment: Enrolment;
  rowLabel: string;
}) {
  const t = useTranslations("students.enrolments");
  const ts = useTranslations("students");
  const dates = useDateInput();
  const api = useBffClient("staff");
  const legendId = useId();
  return (
    <FormDialog
      triggerLabel={
        <>
          {t("end")}
          <span className="sr-only">: {rowLabel}</span>
        </>
      }
      triggerVariant="ghost"
      triggerSize="sm"
      title={t("endTitle")}
      description={t("endDescription")}
      confirmLabel={t("endSubmit")}
      confirmVariant="danger"
      problems="students.errors"
      problemAction={reloadAction}
      schema={endEnrolmentSchema}
      invalidate={[STUDENTS_KEY]}
      submit={(data) =>
        unwrap(
          api.POST("/api/v1/students/{student_id}/enrollments/{enrollment_id}/end", {
            params: { path: { student_id: studentId, enrollment_id: enrolment.id } },
            headers: { "If-Match": etag(enrolment.version) },
            body: { status: data.status, ...(data.ended_on ? { ended_on: data.ended_on } : {}) },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <p className="text-sm font-semibold">{rowLabel}</p>
          <fieldset className="space-y-2" aria-labelledby={legendId}>
            <legend id={legendId} className="text-sm font-semibold">
              {t("endStatusLegend")}
            </legend>
            {END_STATUSES.map((value) => (
              <div key={value} className="flex min-h-8 items-start gap-2 text-sm">
                <input
                  id={`${legendId}-${value}`}
                  type="radio"
                  name="status"
                  value={value}
                  defaultChecked={value === "completed"}
                  aria-describedby={`${legendId}-${value}-hint`}
                  className="mt-0.5 size-4"
                />
                <div>
                  <label htmlFor={`${legendId}-${value}`}>{t(`endStatus.${value}`)}</label>
                  <p id={`${legendId}-${value}-hint`} className="text-xs text-ink-muted">
                    {t(`endStatusHint.${value}`)}
                  </p>
                </div>
              </div>
            ))}
            {errors.status ? (
              <p className="text-sm font-semibold text-danger">{errors.status}</p>
            ) : null}
          </fieldset>
          <GuardedTextField
            name="ended_on"
            label={t("endedOn")}
            hint={t("endedOnHint", dates.hint("2012-03-14"))}
            error={errors.ended_on ? ts("dateInvalid", dates.hint("2012-03-14")) : undefined}
            inputMode="numeric"
            placeholder={dates.placeholder}
            autoComplete="off"
          />
          <p className="text-sm text-ink-muted">{t("endNote")}</p>
        </>
      )}
    </FormDialog>
  );
}

/* ------------------------------------------------------------------ card */

export function EnrolmentsCard({
  student,
  permissions,
}: {
  student: Student;
  permissions: Permissions;
}) {
  const t = useTranslations("students.enrolments");
  const td = useTranslations("students.detail");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const canEdit = permissions.has(PERM.updateNonIdentity);
  const structure = useSchoolStructure();
  const labels = useEnrolmentLabels(structure);
  const enrolments = useApiQuery(enrolmentsKey(student.id), () =>
    unwrap(
      api.GET("/api/v1/students/{student_id}/enrollments", {
        params: { path: { student_id: student.id } },
      }),
    ),
  );

  const rowLabel = (row: Enrolment) =>
    [labels.yearLabel(row.academic_year_id), labels.sectionLabel(row.section_id)]
      .filter(Boolean)
      .join(", ");

  const columns: Column<Enrolment>[] = [
    {
      key: "year",
      header: t("colYear"),
      cell: (row) => <Value>{labels.yearLabel(row.academic_year_id)}</Value>,
    },
    {
      key: "section",
      header: t("colSection"),
      cell: (row) => labels.sectionLabel(row.section_id) ?? t("unknownSection"),
    },
    { key: "roll", header: t("colRollNo"), cell: (row) => <Value>{row.roll_no}</Value> },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) =>
        isEnrolmentStatus(row.status) ? (
          <Badge tone={statusTone[row.status]}>{t(`status.${row.status}`)}</Badge>
        ) : (
          <Badge tone="neutral">{row.status}</Badge>
        ),
    },
    {
      key: "dates",
      header: t("colDates"),
      cell: (row) =>
        t("dates", {
          start: formatDate(row.started_on) ?? "—",
          end: row.ended_on ? (formatDate(row.ended_on) ?? "—") : t("ongoing"),
        }),
    },
    ...(canEdit
      ? [
          {
            key: "actions",
            header: <span className="sr-only">{tc("actions")}</span>,
            cell: (row: Enrolment) => (
              <span className="flex flex-wrap gap-1" data-print="hide">
                <CorrectEnrolmentDialog
                  studentId={student.id}
                  enrolment={row}
                  rowLabel={rowLabel(row)}
                  options={labels.siblings(row)}
                />
                {row.status === "active" ? (
                  <EndEnrolmentDialog
                    studentId={student.id}
                    enrolment={row}
                    rowLabel={rowLabel(row)}
                  />
                ) : null}
              </span>
            ),
          },
        ]
      : []),
  ];

  return (
    <Card
      title={td("classTitle")}
      description={t("description")}
      actions={canEdit ? <EnrolmentDialog student={student} /> : null}
    >
      <div className="space-y-4">
        {student.enrollment ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-ink-muted">{td("classSection")}</dt>
            <dd className="font-semibold">{student.enrollment.label}</dd>
            <dt className="text-ink-muted">{td("rollNo")}</dt>
            <dd>
              <Value>{student.enrollment.roll_no ?? null}</Value>
            </dd>
          </dl>
        ) : (
          <p className="text-sm text-ink-muted">{td("notEnrolled")}</p>
        )}
        <DataTable
          caption={t("table")}
          columns={columns}
          state={enrolments}
          rowKey={(row) => row.id}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
      </div>
    </Card>
  );
}

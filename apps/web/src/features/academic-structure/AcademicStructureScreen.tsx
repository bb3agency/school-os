"use client";

import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { Select, SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import type { FieldErrors } from "@/lib/forms";
import {
  ALL_STRUCTURE_KEYS,
  STRUCTURE_MANAGE,
  classCreateSchema,
  classEditSchema,
  classLabel,
  ifMatch,
  nextSortOrder,
  pickYear,
  sectionCreateSchema,
  sectionEditSchema,
  useStructureLists,
  useYearSections,
  withFreshOnConflict,
  yearCreateSchema,
  yearEditSchema,
} from "./data";

/**
 * School structure (US-202, FR-TEN-010): academic years, classes and sections, with add and
 * edit for holders of `tenant.structure.manage` (AC3: others see the lists only). Every edit
 * sends If-Match with the version it was read at; a 412 reloads the lists and says so.
 * The API has no archive or delete for years, classes or sections, so none is offered.
 */
export function AcademicStructureScreen() {
  const ts = useTranslations("school.structure");
  const t = useTranslations("academicStructure");
  const can = useStaffCan();
  const manage = can(STRUCTURE_MANAGE);
  const { years, classes } = useStructureLists();
  const [chosenYear, setChosenYear] = useState<string | null>(null);
  const yearId = years.status === "ready" ? pickYear(years.data, chosenYear) : null;
  const sections = useYearSections(yearId);
  // The read-only note waits for /me so it never flashes for managers.
  const meLoaded = useStaffMe() !== undefined;

  return (
    <div className="space-y-6">
      <PageHeader title={ts("title")} description={ts("description")} />
      {!manage && meLoaded ? <Alert tone="info">{t("readOnlyNote")}</Alert> : null}
      <YearsCard years={years} manage={manage} />
      <ClassesCard classes={classes} sections={sections} manage={manage} />
      <SectionsCard
        years={years}
        classes={classes}
        sections={sections}
        yearId={yearId}
        onYear={setChosenYear}
        manage={manage}
      />
    </div>
  );
}

type Lists = ReturnType<typeof useStructureLists>;

/* ------------------------------------------------------------------------ academic years */

function YearsCard({ years, manage }: { years: Lists["years"]; manage: boolean }) {
  const ts = useTranslations("school.structure");
  const t = useTranslations("academicStructure");
  const tc = useTranslations("common");
  const columns: Column<AcademicYear>[] = [
    { key: "label", header: ts("years.colYear"), cell: (row) => row.label },
    {
      key: "starts",
      header: ts("years.colStarts"),
      cell: (row) => <Value>{formatDate(row.starts_on)}</Value>,
    },
    {
      key: "ends",
      header: ts("years.colEnds"),
      cell: (row) => <Value>{formatDate(row.ends_on)}</Value>,
    },
    {
      key: "current",
      header: ts("years.colCurrent"),
      cell: (row) =>
        row.is_current ? (
          <Badge tone="success">{ts("currentBadge")}</Badge>
        ) : manage ? (
          <MakeCurrentDialog year={row} />
        ) : (
          <span className="text-ink-muted">{tc("no")}</span>
        ),
    },
    ...(manage
      ? [
          {
            key: "actions",
            header: t("colActions"),
            cell: (row: AcademicYear) => <EditYearDialog year={row} />,
          },
        ]
      : []),
  ];
  return (
    <Card title={ts("years.title")} actions={manage ? <AddYearDialog /> : undefined}>
      <DataTable
        caption={ts("years.title")}
        captionHidden
        columns={columns}
        state={years}
        rowKey={(row) => row.id}
        emptyTitle={ts("years.emptyTitle")}
        emptyBody={ts("years.emptyBody")}
      />
    </Card>
  );
}

function YearFields({ errors, year }: { errors: FieldErrors; year?: AcademicYear }) {
  const t = useTranslations("academicStructure.years");
  return (
    <>
      <TextField
        name="label"
        label={t("labelField")}
        hint={t("labelHint")}
        error={errors.label}
        defaultValue={year?.label}
        autoComplete="off"
        inputMode="numeric"
        maxLength={7}
        required
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          name="starts_on"
          type="date"
          label={t("startsField")}
          error={errors.starts_on}
          defaultValue={year?.starts_on}
          required
        />
        <TextField
          name="ends_on"
          type="date"
          label={t("endsField")}
          error={errors.ends_on}
          defaultValue={year?.ends_on}
          required
        />
      </div>
    </>
  );
}

function AddYearDialog() {
  const t = useTranslations("academicStructure.years");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("add")}
      triggerVariant="primary"
      title={t("add")}
      description={t("addDescription")}
      confirmLabel={t("addConfirm")}
      schema={yearCreateSchema}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={(data, key) =>
        unwrap(
          api.POST("/api/v1/academic-years", {
            headers: { "Idempotency-Key": key },
            body: {
              label: data.label,
              starts_on: data.starts_on,
              ends_on: data.ends_on,
              is_current: data.is_current,
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <YearFields errors={errors} />
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" name="is_current" className="mt-1 size-4 accent-primary" />
            <span>
              {t("makeCurrentField")}
              <span className="block text-ink-muted">{t("makeCurrentHint")}</span>
            </span>
          </label>
        </>
      )}
    </ActionDialog>
  );
}

function EditYearDialog({ year }: { year: AcademicYear }) {
  const t = useTranslations("academicStructure");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  return (
    <ActionDialog
      triggerLabel={t("edit")}
      triggerSize="sm"
      triggerDescription={t("years.editDescribe", { label: year.label })}
      title={t("years.editTitle", { label: year.label })}
      confirmLabel={t("save")}
      schema={yearEditSchema}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={(data) =>
        withFreshOnConflict(queryClient, () =>
          unwrap(
            api.PATCH("/api/v1/academic-years/{year_id}", {
              params: { path: { year_id: year.id } },
              headers: { "If-Match": ifMatch(year.version) },
              body: { label: data.label, starts_on: data.starts_on, ends_on: data.ends_on },
            }),
          ),
        )
      }
    >
      {(errors) => <YearFields errors={errors} year={year} />}
    </ActionDialog>
  );
}

function MakeCurrentDialog({ year }: { year: AcademicYear }) {
  const t = useTranslations("academicStructure.years");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  return (
    <ActionDialog
      triggerLabel={t("makeCurrent")}
      triggerSize="sm"
      triggerDescription={t("makeCurrentDescribe", { label: year.label })}
      title={t("makeCurrentTitle", { label: year.label })}
      description={t("makeCurrentBody", { label: year.label })}
      confirmLabel={t("makeCurrent")}
      schema={z.object({})}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={() =>
        withFreshOnConflict(queryClient, () =>
          unwrap(
            api.POST("/api/v1/academic-years/{year_id}/make-current", {
              params: { path: { year_id: year.id } },
              headers: { "If-Match": ifMatch(year.version) },
            }),
          ),
        )
      }
    />
  );
}

/* ------------------------------------------------------------------------------- classes */

function ClassesCard({
  classes,
  sections,
  manage,
}: {
  classes: Lists["classes"];
  sections: ReturnType<typeof useYearSections>;
  manage: boolean;
}) {
  const ts = useTranslations("school.structure");
  const t = useTranslations("academicStructure");
  const locale = useLocale();
  const count = (classId: string): string | null =>
    sections.status === "ready"
      ? String(sections.data.filter((row) => row.class_id === classId).length)
      : null;
  const columns: Column<SchoolClass>[] = [
    { key: "name", header: ts("classes.colName"), cell: (row) => classLabel(row, locale) },
    { key: "code", header: t("classes.colCode"), cell: (row) => row.code },
    { key: "order", header: ts("classes.colOrder"), cell: (row) => row.sort_order },
    {
      key: "sections",
      header: t("classes.colSectionsThisYear"),
      cell: (row) => <Value>{count(row.id)}</Value>,
    },
    ...(manage
      ? [
          {
            key: "actions",
            header: t("colActions"),
            cell: (row: SchoolClass) => <EditClassDialog schoolClass={row} />,
          },
        ]
      : []),
  ];
  return (
    <Card
      title={ts("classes.title")}
      actions={
        manage ? (
          <>
            <DefaultClassesDialog />
            <AddClassDialog
              nextOrder={classes.status === "ready" ? nextSortOrder(classes.data) : 0}
            />
          </>
        ) : undefined
      }
    >
      <DataTable
        caption={ts("classes.title")}
        captionHidden
        columns={columns}
        state={classes}
        rowKey={(row) => row.id}
        emptyTitle={ts("classes.emptyTitle")}
        emptyBody={ts("classes.emptyBody")}
      />
    </Card>
  );
}

function ClassNameFields({
  errors,
  schoolClass,
  nextOrder,
}: {
  errors: FieldErrors;
  schoolClass?: SchoolClass;
  nextOrder?: number;
}) {
  const t = useTranslations("academicStructure.classes");
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          name="display_en"
          label={t("enField")}
          error={errors.display_en}
          defaultValue={schoolClass?.display_en}
          maxLength={100}
          lang="en"
          required
        />
        <TextField
          name="display_te"
          label={t("teField")}
          error={errors.display_te}
          defaultValue={schoolClass?.display_te}
          maxLength={100}
          lang="te"
          required
        />
      </div>
      <TextField
        name="sort_order"
        label={t("orderField")}
        hint={t("orderHint")}
        error={errors.sort_order}
        defaultValue={String(schoolClass?.sort_order ?? nextOrder ?? 0)}
        inputMode="numeric"
        maxLength={5}
        required
      />
    </>
  );
}

function AddClassDialog({ nextOrder }: { nextOrder: number }) {
  const t = useTranslations("academicStructure.classes");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("add")}
      triggerVariant="primary"
      title={t("add")}
      description={t("addDescription")}
      confirmLabel={t("add")}
      schema={classCreateSchema}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={(data, key) =>
        unwrap(
          api.POST("/api/v1/classes", {
            headers: { "Idempotency-Key": key },
            body: {
              code: data.code,
              display_en: data.display_en,
              display_te: data.display_te,
              sort_order: data.sort_order,
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <TextField
            name="code"
            label={t("codeField")}
            hint={t("codeHint")}
            error={errors.code}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={16}
            className="max-w-48"
            required
          />
          <ClassNameFields errors={errors} nextOrder={nextOrder} />
        </>
      )}
    </ActionDialog>
  );
}

function EditClassDialog({ schoolClass }: { schoolClass: SchoolClass }) {
  const t = useTranslations("academicStructure");
  const locale = useLocale();
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const name = classLabel(schoolClass, locale);
  return (
    <ActionDialog
      triggerLabel={t("edit")}
      triggerSize="sm"
      triggerDescription={t("classes.editDescribe", { name })}
      title={t("classes.editTitle", { name })}
      description={t("classes.codeFixed", { code: schoolClass.code })}
      confirmLabel={t("save")}
      schema={classEditSchema}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={(data) =>
        withFreshOnConflict(queryClient, () =>
          unwrap(
            api.PATCH("/api/v1/classes/{class_id}", {
              params: { path: { class_id: schoolClass.id } },
              headers: { "If-Match": ifMatch(schoolClass.version) },
              body: {
                display_en: data.display_en,
                display_te: data.display_te,
                sort_order: data.sort_order,
              },
            }),
          ),
        )
      }
    >
      {(errors) => <ClassNameFields errors={errors} schoolClass={schoolClass} />}
    </ActionDialog>
  );
}

function DefaultClassesDialog() {
  const t = useTranslations("academicStructure.classes");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("defaults")}
      title={t("defaultsTitle")}
      description={t("defaultsBody")}
      confirmLabel={t("defaultsConfirm")}
      schema={z.object({})}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={() => unwrap(api.POST("/api/v1/classes/defaults"))}
    />
  );
}

/* ------------------------------------------------------------------------------ sections */

function SectionsCard({
  years,
  classes,
  sections,
  yearId,
  onYear,
  manage,
}: {
  years: Lists["years"];
  classes: Lists["classes"];
  sections: ReturnType<typeof useYearSections>;
  yearId: string | null;
  onYear: (yearId: string) => void;
  manage: boolean;
}) {
  const ts = useTranslations("school.structure");
  const t = useTranslations("academicStructure");
  const locale = useLocale();
  const selectId = useId();
  const classById = new Map(
    classes.status === "ready" ? classes.data.map((row) => [row.id, row] as const) : [],
  );
  const yearList = years.status === "ready" ? years.data : [];
  const year = yearList.find((row) => row.id === yearId);
  const classList = classes.status === "ready" ? classes.data : [];
  // Sort like the class list (display order), then by section name.
  const order = new Map(classList.map((row, index) => [row.id, index] as const));
  const rows =
    sections.status === "ready"
      ? {
          status: "ready" as const,
          data: [...sections.data].sort(
            (a, b) =>
              (order.get(a.class_id) ?? 0) - (order.get(b.class_id) ?? 0) ||
              a.name.localeCompare(b.name, locale),
          ),
        }
      : sections;
  const parentName = (row: Section): string | null => {
    const parent = classById.get(row.class_id);
    return parent ? classLabel(parent, locale) : null;
  };
  const columns: Column<Section>[] = [
    {
      key: "class",
      header: ts("sections.colClass"),
      cell: (row) => <Value>{parentName(row)}</Value>,
    },
    { key: "name", header: ts("sections.colName"), cell: (row) => row.name },
    ...(manage
      ? [
          {
            key: "actions",
            header: t("colActions"),
            cell: (row: Section) => (
              <EditSectionDialog section={row} className={parentName(row) ?? ""} />
            ),
          },
        ]
      : []),
  ];

  return (
    <Card
      title={ts("sections.title")}
      description={t("sections.description")}
      actions={
        manage && year && classList.length > 0 ? (
          <AddSectionDialog year={year} classes={classList} />
        ) : undefined
      }
    >
      {yearList.length > 0 ? (
        <div className="mb-4 max-w-xs space-y-1">
          <Label htmlFor={selectId}>{t("sections.yearLabel")}</Label>
          <Select
            id={selectId}
            value={yearId ?? ""}
            onChange={(event) => onYear(event.target.value)}
            options={yearList.map((row) => ({
              value: row.id,
              label: row.is_current
                ? t("sections.currentYearOption", { label: row.label })
                : row.label,
            }))}
          />
        </div>
      ) : null}
      {years.status === "ready" && yearList.length === 0 ? (
        <Alert tone="info">{t("sections.needYear")}</Alert>
      ) : (
        <DataTable
          caption={ts("sections.title")}
          captionHidden
          columns={columns}
          state={years.status === "ready" ? rows : years}
          rowKey={(row) => row.id}
          emptyTitle={ts("sections.emptyTitle")}
          emptyBody={ts("sections.emptyBody")}
        />
      )}
      {manage && classes.status === "ready" && classList.length === 0 && yearList.length > 0 ? (
        <p className="mt-3 text-sm text-ink-muted">{t("sections.needClass")}</p>
      ) : null}
    </Card>
  );
}

function AddSectionDialog({
  year,
  classes,
}: {
  year: AcademicYear;
  classes: readonly SchoolClass[];
}) {
  const t = useTranslations("academicStructure.sections");
  const tc = useTranslations("common");
  const locale = useLocale();
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("add")}
      triggerVariant="primary"
      title={t("addTitle", { year: year.label })}
      description={t("addDescription")}
      confirmLabel={t("add")}
      schema={sectionCreateSchema}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={(data, key) =>
        unwrap(
          api.POST("/api/v1/sections", {
            headers: { "Idempotency-Key": key },
            body: {
              academic_year_id: data.academic_year_id,
              class_id: data.class_id,
              name: data.name,
            },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <input type="hidden" name="academic_year_id" value={year.id} />
          <SelectField
            name="class_id"
            label={t("classField")}
            error={errors.class_id}
            placeholder={tc("chooseOne")}
            defaultValue=""
            options={classes.map((row) => ({ value: row.id, label: classLabel(row, locale) }))}
            required
          />
          <TextField
            name="name"
            label={t("nameField")}
            hint={t("nameHint")}
            error={errors.name}
            autoComplete="off"
            maxLength={16}
            className="max-w-48"
            required
          />
        </>
      )}
    </ActionDialog>
  );
}

function EditSectionDialog({ section, className }: { section: Section; className: string }) {
  const t = useTranslations("academicStructure");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const names = { name: section.name, className };
  return (
    <ActionDialog
      triggerLabel={t("edit")}
      triggerSize="sm"
      triggerDescription={t("sections.editDescribe", names)}
      title={t("sections.editTitle", names)}
      confirmLabel={t("save")}
      schema={sectionEditSchema}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={(data) =>
        withFreshOnConflict(queryClient, () =>
          unwrap(
            api.PATCH("/api/v1/sections/{section_id}", {
              params: { path: { section_id: section.id } },
              headers: { "If-Match": ifMatch(section.version) },
              body: { name: data.name },
            }),
          ),
        )
      }
    >
      {(errors) => (
        <TextField
          name="name"
          label={t("sections.nameField")}
          hint={t("sections.nameHint")}
          error={errors.name}
          defaultValue={section.name}
          autoComplete="off"
          maxLength={16}
          className="max-w-48"
          required
        />
      )}
    </ActionDialog>
  );
}

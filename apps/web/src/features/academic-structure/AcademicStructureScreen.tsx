"use client";

import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState, type ReactNode } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge, Pill } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { Toggle } from "@/components/ui/Toggle";
import { TextField } from "@/components/ui/Input";
import { Select, SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Value } from "@/components/ui/Value";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatList } from "@/lib/format";
import type { FieldErrors } from "@/lib/forms";
import type { Loadable } from "@/lib/loadable";
import {
  ALL_STRUCTURE_KEYS,
  STRUCTURE_MANAGE,
  classCreateSchema,
  classEditSchema,
  classLabel,
  ifMatch,
  isArchived,
  nextSortOrder,
  pickYear,
  sectionCreateSchema,
  sectionEditSchema,
  setArchived,
  useStaffDirectory,
  useStructureLists,
  useYearSections,
  withFreshOnConflict,
  yearCreateSchema,
  yearEditSchema,
  type StaffMember,
  type StructureKind,
} from "./data";

/**
 * School structure (US-202, FR-TEN-010): academic years, classes and sections, with add, edit,
 * archive and unarchive for holders of `tenant.structure.manage` (AC3: others see the lists
 * only). Every change sends If-Match with the version it was read at; a 412 reloads the lists
 * and says so. Archived rows are hidden unless "Show archived" is on, and are marked when
 * shown. A section's class teacher is chosen from the staff directory (GET /staff). Each
 * year links to its year-end promotion (FR-TEN-011, US-202 AC2).
 */
export function AcademicStructureScreen() {
  const ts = useTranslations("school.structure");
  const t = useTranslations("academicStructure");
  const locale = useLocale();
  const can = useStaffCan();
  const manage = can(STRUCTURE_MANAGE);
  const [showArchived, setShowArchived] = useState(false);
  const { years, classes } = useStructureLists(showArchived);
  const [chosenYear, setChosenYear] = useState<string | null>(null);
  const yearId = years.status === "ready" ? pickYear(years.data, chosenYear) : null;
  const sections = useYearSections(yearId, showArchived);
  const staff = useStaffDirectory(manage, locale);
  // The read-only note waits for /me so it never flashes for managers.
  const meLoaded = useStaffMe() !== undefined;
  const tn = useTranslations("school.nav");

  return (
    <div className="space-y-6">
      <PageHeader
        title={ts("title")}
        description={ts("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: ts("title") }]}
        actions={
          manage ? (
            <ButtonLink href="/settings/structure/promotions" variant="secondary">
              {tn("promotions")}
            </ButtonLink>
          ) : undefined
        }
      />
      {!manage && meLoaded ? <Alert tone="info">{t("readOnlyNote")}</Alert> : null}
      {/* Applies at once (a view filter), so a switch rather than a checkbox. */}
      <div className="max-w-2xl rounded-xl border border-border bg-surface px-5 py-4 shadow-card">
        <Toggle
          label={t("showArchived")}
          description={t("showArchivedHint")}
          checked={showArchived}
          onCheckedChange={setShowArchived}
        />
      </div>
      <YearsCard years={years} manage={manage} />
      <ClassesCard classes={classes} sections={sections} manage={manage} />
      <SectionsCard
        years={years}
        classes={classes}
        sections={sections}
        staff={staff}
        yearId={yearId}
        onYear={setChosenYear}
        manage={manage}
      />
    </div>
  );
}

type Lists = ReturnType<typeof useStructureLists>;

/** Name plus an "Archived" badge, so archived rows never look like rows in use. */
function NameCell({ name, archived }: { name: ReactNode; archived: boolean }) {
  const t = useTranslations("academicStructure");
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span>{name}</span>
      {archived ? <Badge tone="warning">{t("archivedBadge")}</Badge> : null}
    </span>
  );
}

function Actions({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap gap-2">{children}</div>;
}

/**
 * Archive, or say why it is not offered: a row with students enrolled (`in_use` from the API)
 * cannot be archived (409 `structure_in_use`), so the button would only lead to an error.
 */
function ArchiveOrInUse(props: {
  kind: StructureKind;
  row: { id: string; version: number; in_use?: boolean | null };
  name: string;
}) {
  const t = useTranslations("academicStructure.archive");
  if (props.row.in_use) {
    return <span className="self-center text-sm text-ink-muted">{t("inUse")}</span>;
  }
  return <ArchiveDialog kind={props.kind} row={props.row} name={props.name} archive />;
}

/* ------------------------------------------------------------------------ academic years */

function YearsCard({ years, manage }: { years: Lists["years"]; manage: boolean }) {
  const ts = useTranslations("school.structure");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  let body: ReactNode;
  if (years.status === "loading") {
    body = <LoadingState label={tc("loading")} variant="cards" rows={3} />;
  } else if (years.status === "error") {
    body = (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {years.reason ? te(`load.${years.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  } else if (years.status === "unavailable") {
    body = <EmptyState title={tc("notAvailableYetTitle")} body={tc("notAvailableYetBody")} />;
  } else if (years.data.length === 0) {
    body = (
      <EmptyState icon="calendar" title={ts("years.emptyTitle")} body={ts("years.emptyBody")} />
    );
  } else {
    body = (
      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {years.data.map((row) => (
          <YearCard key={row.id} year={row} manage={manage} />
        ))}
      </ul>
    );
  }
  return (
    <Card title={ts("years.title")} actions={manage ? <AddYearDialog /> : undefined}>
      {body}
    </Card>
  );
}

/**
 * One academic year: its label, a "Current" pill, the dates and (for managers) the actions.
 * Archive and "Make current" are secondary; editing and promotion come first.
 */
function YearCard({ year, manage }: { year: AcademicYear; manage: boolean }) {
  const ts = useTranslations("school.structure");
  const t = useTranslations("academicStructure");
  const archived = isArchived(year);
  return (
    <li
      className={
        year.is_current
          ? "flex flex-col gap-4 rounded-lg border border-primary bg-primary-soft p-4"
          : "flex flex-col gap-4 rounded-lg border border-border bg-surface-muted p-4"
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-medium text-ink">{year.label}</h3>
        {year.is_current ? <Pill variant="done">{ts("currentBadge")}</Pill> : null}
        {archived ? <Badge tone="warning">{t("archivedBadge")}</Badge> : null}
      </div>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-ink-muted">{ts("years.colStarts")}</dt>
          <dd className="font-medium text-ink">
            <Value>{formatDate(year.starts_on)}</Value>
          </dd>
        </div>
        <div>
          <dt className="text-ink-muted">{ts("years.colEnds")}</dt>
          <dd className="font-medium text-ink">
            <Value>{formatDate(year.ends_on)}</Value>
          </dd>
        </div>
      </dl>
      {manage ? (
        <div className="mt-auto flex flex-wrap gap-2 border-t border-border pt-3">
          {archived ? (
            <ArchiveDialog kind="year" row={year} name={year.label} archive={false} />
          ) : (
            <>
              <EditYearDialog year={year} />
              <ButtonLink
                href={`/settings/structure/years/${year.id}/promotions`}
                variant="secondary"
                size="sm"
              >
                {t("years.promote")}{" "}
                <span className="sr-only">{t("years.promoteFor", { label: year.label })}</span>
              </ButtonLink>
              {year.is_current ? null : <MakeCurrentDialog year={year} />}
              {year.is_current ? null : <ArchiveOrInUse kind="year" row={year} name={year.label} />}
            </>
          )}
        </div>
      ) : null}
    </li>
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

/* ------------------------------------------------------------------- archive / unarchive */

/**
 * Archive (hide from lists, keep for old records) or bring back one year, class or section.
 * The API refuses the current year (`academic_year_current`) and anything with students
 * enrolled (`structure_in_use`); both are explained in plain language.
 */
function ArchiveDialog({
  kind,
  row,
  name,
  archive,
}: {
  kind: StructureKind;
  row: { id: string; version: number };
  name: string;
  archive: boolean;
}) {
  const t = useTranslations("academicStructure.archive");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const values = { name };
  return (
    <ActionDialog
      triggerLabel={archive ? t("archive") : t("unarchive")}
      triggerVariant={archive ? "ghost" : "secondary"}
      triggerSize="sm"
      triggerDescription={
        archive ? t(`${kind}.describe`, values) : t(`${kind}.unarchiveDescribe`, values)
      }
      title={archive ? t(`${kind}.title`, values) : t(`${kind}.unarchiveTitle`, values)}
      description={archive ? t(`${kind}.body`) : t("unarchiveBody")}
      confirmLabel={archive ? t("archive") : t("unarchive")}
      confirmVariant={archive ? "danger" : "primary"}
      schema={z.object({})}
      invalidate={ALL_STRUCTURE_KEYS}
      errorNamespace="academicStructure"
      submit={() => withFreshOnConflict(queryClient, () => setArchived(api, kind, row, archive))}
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
    {
      key: "name",
      header: ts("classes.colName"),
      cell: (row) => <NameCell name={classLabel(row, locale)} archived={isArchived(row)} />,
    },
    {
      key: "code",
      header: t("classes.colCode"),
      cell: (row) => <span className="font-mono text-sm">{row.code}</span>,
    },
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
            cell: (row: SchoolClass) =>
              isArchived(row) ? (
                <ArchiveDialog
                  kind="class"
                  row={row}
                  name={classLabel(row, locale)}
                  archive={false}
                />
              ) : (
                <Actions>
                  <EditClassDialog schoolClass={row} />
                  <ArchiveOrInUse kind="class" row={row} name={classLabel(row, locale)} />
                </Actions>
              ),
          },
        ]
      : []),
  ];
  const inUse = classes.status === "ready" ? classes.data.filter((row) => !isArchived(row)) : [];
  return (
    <Card
      title={ts("classes.title")}
      actions={
        manage ? (
          <>
            <DefaultClassesDialog />
            <AddClassDialog nextOrder={classes.status === "ready" ? nextSortOrder(inUse) : 0} />
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

type StaffList = Loadable<readonly StaffMember[]>;

/**
 * "Name (Teacher, Class teacher)": built-in role names from the messages, else the key. A
 * member who has not signed in yet (`status` invited) is marked so.
 */
function useStaffLabel(): (member: StaffMember) => string {
  const tr = useTranslations("school.users.roles");
  const t = useTranslations("academicStructure.sections");
  const locale = useLocale() as Locale;
  const loose = tr as unknown as ((key: string) => string) & { has: (key: string) => boolean };
  return (member) => {
    const roles = member.roles.map((key) => (loose.has(key) ? loose(key) : key));
    const name =
      roles.length === 0
        ? member.display_name
        : `${member.display_name} (${formatList(roles, locale)})`;
    return member.status === "invited" ? t("teacherInvited", { name }) : name;
  };
}

/** The class teacher's name for the sections table (managers only; they can read /staff). */
function TeacherCell({ membershipId, staff }: { membershipId: string | null; staff: StaffList }) {
  const t = useTranslations("academicStructure.sections");
  if (!membershipId) return <Value>{null}</Value>;
  if (staff.status !== "ready") return <>{t("teacherAssigned")}</>;
  const member = staff.data.find((row) => row.membership_id === membershipId);
  if (!member) return <>{t("teacherNotListed")}</>;
  return (
    <>
      {member.status === "invited"
        ? t("teacherInvited", { name: member.display_name })
        : member.display_name}
    </>
  );
}

/**
 * Class teacher picker (GET /staff, sorted by name). While the list loads, or when it cannot
 * be read (e.g. no permission), the field is left out and the section keeps its teacher.
 */
function ClassTeacherField({
  errors,
  staff,
  current,
}: {
  errors: FieldErrors;
  staff: StaffList;
  current: string | null;
}) {
  const t = useTranslations("academicStructure.sections");
  const tc = useTranslations("common");
  const label = useStaffLabel();
  if (staff.status === "loading") {
    return <p className="text-sm text-ink-muted">{t("teacherLoading")}</p>;
  }
  if (staff.status !== "ready") {
    return <p className="text-sm text-ink-muted">{t("teacherUnavailable")}</p>;
  }
  const options = staff.data.map((member) => ({
    value: member.membership_id,
    label: label(member),
  }));
  if (current && !staff.data.some((member) => member.membership_id === current)) {
    options.unshift({ value: current, label: t("teacherNotListed") });
  }
  return (
    <SelectField
      name="class_teacher_membership_id"
      label={t("teacherField")}
      hint={t("teacherHint")}
      error={errors.class_teacher_membership_id}
      placeholder={staff.data.length > 0 ? t("noTeacher") : tc("notAvailable")}
      defaultValue={current ?? ""}
      options={options}
    />
  );
}

function SectionsCard({
  years,
  classes,
  sections,
  staff,
  yearId,
  onYear,
  manage,
}: {
  years: Lists["years"];
  classes: Lists["classes"];
  sections: ReturnType<typeof useYearSections>;
  staff: StaffList;
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
  const activeClasses = classList.filter((row) => !isArchived(row));
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
    {
      key: "name",
      header: ts("sections.colName"),
      cell: (row) => <NameCell name={row.name} archived={isArchived(row)} />,
    },
    ...(manage
      ? [
          {
            key: "teacher",
            header: t("sections.colTeacher"),
            cell: (row: Section) => (
              <TeacherCell membershipId={row.class_teacher_membership_id} staff={staff} />
            ),
          },
          {
            key: "actions",
            header: t("colActions"),
            cell: (row: Section) => {
              const name = t("sections.fullName", {
                className: parentName(row) ?? "",
                name: row.name,
              });
              return isArchived(row) ? (
                <ArchiveDialog kind="section" row={row} name={name} archive={false} />
              ) : (
                <Actions>
                  <EditSectionDialog
                    section={row}
                    className={parentName(row) ?? ""}
                    staff={staff}
                  />
                  <ArchiveOrInUse kind="section" row={row} name={name} />
                </Actions>
              );
            },
          },
        ]
      : []),
  ];

  return (
    <Card
      title={ts("sections.title")}
      description={t("sections.description")}
      actions={
        manage && year && !isArchived(year) && activeClasses.length > 0 ? (
          <AddSectionDialog year={year} classes={activeClasses} staff={staff} />
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
                : isArchived(row)
                  ? t("sections.archivedYearOption", { label: row.label })
                  : row.label,
            }))}
          />
        </div>
      ) : null}
      {year && isArchived(year) ? (
        <p className="mb-3 text-sm text-ink-muted">{t("sections.archivedYearNote")}</p>
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
      {manage && classes.status === "ready" && activeClasses.length === 0 && yearList.length > 0 ? (
        <p className="mt-3 text-sm text-ink-muted">{t("sections.needClass")}</p>
      ) : null}
    </Card>
  );
}

function AddSectionDialog({
  year,
  classes,
  staff,
}: {
  year: AcademicYear;
  classes: readonly SchoolClass[];
  staff: StaffList;
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
              ...(data.class_teacher_membership_id
                ? { class_teacher_membership_id: data.class_teacher_membership_id }
                : {}),
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
          <ClassTeacherField errors={errors} staff={staff} current={null} />
        </>
      )}
    </ActionDialog>
  );
}

function EditSectionDialog({
  section,
  className,
  staff,
}: {
  section: Section;
  className: string;
  staff: StaffList;
}) {
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
      submit={(data) => {
        const teacher = data.class_teacher_membership_id;
        // Send the teacher only when it changed (each change is audited on its own).
        const teacherChanged =
          teacher !== undefined && teacher !== section.class_teacher_membership_id;
        return withFreshOnConflict(queryClient, () =>
          unwrap(
            api.PATCH("/api/v1/sections/{section_id}", {
              params: { path: { section_id: section.id } },
              headers: { "If-Match": ifMatch(section.version) },
              body: {
                name: data.name,
                ...(teacherChanged ? { class_teacher_membership_id: teacher } : {}),
              },
            }),
          ),
        );
      }}
    >
      {(errors) => (
        <>
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
          <ClassTeacherField
            errors={errors}
            staff={staff}
            current={section.class_teacher_membership_id}
          />
        </>
      )}
    </ActionDialog>
  );
}

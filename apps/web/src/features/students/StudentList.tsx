"use client";

import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { z } from "zod";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField, type SelectOption } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { className as classDisplay } from "@/features/school/StructureView";
import { Link } from "@/i18n/navigation";
import { formValues } from "@/lib/forms";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { type Loadable } from "@/lib/loadable";
import { UUID_PATTERN } from "@/lib/validation";
import { containsFullAadhaar } from "./aadhaar";
import { GuardedTextField } from "./fields";
import { PERM, useStaffPermissions, type Permissions } from "./me";
import { Pager, useCursorStack } from "./paging";
import { StudentStatusBadge, useAttributeIndex, useAttributes } from "./parts";
import {
  STUDENT_STATUSES,
  isStudentStatus,
  type StudentStatus,
  type StudentSummary,
} from "./types";

export const PAGE_SIZE = 50;
const SEARCH_FIELD_ID = "student-search";

export interface StudentFilters {
  q?: string | undefined;
  classId?: string | undefined;
  sectionId?: string | undefined;
  status?: string | undefined;
}

interface CleanFilters {
  query?: string;
  class_id?: string;
  section_id?: string;
  status?: StudentStatus;
}

const filtersSchema = z.object({
  q: z.string().optional(),
  class_id: z.string().optional(),
  section_id: z.string().optional(),
  status: z.string().optional(),
});

/** Search form values → filters (the zod schema only shapes them; cleanFilters checks them). */
export function filtersFromForm(form: HTMLFormElement): StudentFilters {
  const parsed = filtersSchema.parse(formValues(form));
  return {
    q: parsed.q?.trim() || undefined,
    classId: parsed.class_id || undefined,
    sectionId: parsed.section_id || undefined,
    status: parsed.status || undefined,
  };
}

/** Filters → API query (ignores anything malformed rather than failing the page). */
export function cleanFilters(filters: StudentFilters): CleanFilters {
  const q = filters.q?.trim().slice(0, 200) ?? "";
  return {
    ...(q ? { query: q } : {}),
    ...(filters.classId && UUID_PATTERN.test(filters.classId) ? { class_id: filters.classId } : {}),
    ...(filters.sectionId && UUID_PATTERN.test(filters.sectionId)
      ? { section_id: filters.sectionId }
      : {}),
    ...(filters.status && isStudentStatus(filters.status) ? { status: filters.status } : {}),
  };
}

interface StructureData {
  years: Loadable<readonly AcademicYear[]>;
  classes: Loadable<readonly SchoolClass[]>;
  sections: Loadable<readonly Section[]>;
}

/** Classes and sections for filters and forms (GET /academic-years, /classes, /sections). */
export function useSchoolStructure(): StructureData {
  const api = useBffClient("staff");
  const query = { limit: 200 } as const;
  const years = useApiQuery(
    ["staff", "academic-years"],
    async () => (await unwrap(api.GET("/api/v1/academic-years", { params: { query } }))).data,
  );
  const classes = useApiQuery(
    ["staff", "classes"],
    async () => (await unwrap(api.GET("/api/v1/classes", { params: { query } }))).data,
  );
  const sections = useApiQuery(
    ["staff", "sections"],
    async () => (await unwrap(api.GET("/api/v1/sections", { params: { query } }))).data,
  );
  return { years, classes, sections };
}

/** "Class 9 · A" options for the current academic year, optionally for one class. */
export function useSectionOptions(
  structure: StructureData,
  classId?: string,
): readonly SelectOption[] {
  const locale = useLocale();
  if (structure.sections.status !== "ready" || structure.classes.status !== "ready") return [];
  const current =
    structure.years.status === "ready"
      ? structure.years.data.find((year) => year.is_current)?.id
      : undefined;
  const classes = new Map(structure.classes.data.map((row) => [row.id, row] as const));
  return structure.sections.data
    .filter((section) => !current || section.academic_year_id === current)
    .filter((section) => !classId || section.class_id === classId)
    .map((section) => {
      const parent = classes.get(section.class_id);
      return {
        section,
        order: parent?.sort_order ?? 0,
        label: `${parent ? classDisplay(parent, locale) : ""} · ${section.name}`,
      };
    })
    .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label))
    .map(({ section, label }) => ({ value: section.id, label }));
}

export interface StudentPage {
  data: readonly StudentSummary[];
  next_cursor: string | null;
}

export interface StudentListViewProps {
  filters: StudentFilters;
  results: Loadable<StudentPage> | null;
  structure: StructureData;
  permissions: Permissions;
  pageNumber: number;
  onNext?: (() => void) | undefined;
  onPrevious?: (() => void) | undefined;
  /** The search text looked like a full Aadhaar number: it was not sent. */
  aadhaarBlocked?: boolean;
  /** New search (kept in memory only: names never go into the page URL or history). */
  onSearch: (filters: StudentFilters) => void;
  onClear: () => void;
}

/** US-302 / FR-STU-010: find students by name (EN/TE), admission number, class/section, parent. */
export function StudentListView({
  filters,
  results,
  structure,
  permissions,
  pageNumber,
  onNext,
  onPrevious,
  aadhaarBlocked = false,
  onSearch,
  onClear,
}: StudentListViewProps) {
  const t = useTranslations("students.list");
  const ts = useTranslations("students");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [classId, setClassId] = useState(filters.classId ?? "");
  const attributes = useAttributes(results !== null);
  const index = useAttributeIndex(attributes);

  const classOptions: SelectOption[] =
    structure.classes.status === "ready"
      ? [...structure.classes.data]
          .sort((a, b) => a.sort_order - b.sort_order)
          .map((row) => ({ value: row.id, label: classDisplay(row, locale) }))
      : [];
  const sectionOptions = useSectionOptions(structure, classId || undefined);

  const columns: Column<StudentSummary>[] = [
    {
      key: "name",
      header: t("colName"),
      cell: (row) => (
        <div>
          <Link
            href={`/students/${row.id}`}
            className="font-semibold text-primary underline underline-offset-2"
          >
            {row.display_name ?? t("unnamed")}
          </Link>
          {row.match.field && !["full_name", "admission_no"].includes(row.match.field) ? (
            <p className="text-xs text-ink-muted" data-print="hide">
              {t("matchedOn", { field: index.label(row.match.field) })}
            </p>
          ) : null}
        </div>
      ),
    },
    {
      key: "admission",
      header: t("colAdmissionNo"),
      cell: (row) => <Value>{row.admission_no}</Value>,
    },
    {
      key: "class",
      header: t("colClassSection"),
      cell: (row) => <Value>{row.class_section}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <StudentStatusBadge status={row.status} />,
    },
  ];

  const rows: Loadable<readonly StudentSummary[]> | null =
    results === null
      ? null
      : results.status === "ready"
        ? { status: "ready", data: results.data.data }
        : results;
  const hasFilters = Boolean(filters.q || filters.classId || filters.sectionId || filters.status);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSearch(filtersFromForm(event.currentTarget));
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            {permissions.has(PERM.create) ? (
              <ButtonLink href="/students/new">{t("add")}</ButtonLink>
            ) : null}
            {permissions.has(PERM.importRun) ? (
              <ButtonLink href="/imports" variant="secondary">
                {t("import")}
              </ButtonLink>
            ) : null}
            <Button variant="secondary" onClick={() => window.print()}>
              {t("print")}
            </Button>
          </>
        }
      />
      <div data-print="hide">
        <Card title={t("searchTitle")}>
          {/*
            The search stays in this page's memory: a student's or parent's name is personal
            data and must never end up in the address bar, browser history or access logs.
          */}
          <form
            role="search"
            aria-label={t("searchTitle")}
            className="space-y-4"
            noValidate
            onSubmit={submit}
          >
            <div className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-[2fr_1fr_1fr_1fr_auto]">
              <GuardedTextField
                id={SEARCH_FIELD_ID}
                name="q"
                type="search"
                label={t("searchLabel")}
                hint={t("searchHint")}
                defaultValue={filters.q ?? ""}
                maxLength={200}
                autoComplete="off"
                spellCheck={false}
                error={aadhaarBlocked ? ts("aadhaarNotAllowed") : undefined}
              />
              <SelectField
                name="class_id"
                label={t("filterClass")}
                placeholder={tc("all")}
                options={classOptions}
                value={classId}
                onChange={(event) => setClassId(event.currentTarget.value)}
              />
              <SelectField
                name="section_id"
                label={t("filterSection")}
                placeholder={tc("all")}
                options={sectionOptions}
                defaultValue={filters.sectionId ?? ""}
                key={`section-${classId}`}
              />
              <SelectField
                name="status"
                label={t("filterStatus")}
                placeholder={tc("all")}
                options={STUDENT_STATUSES.map((value) => ({
                  value,
                  label: ts(`status.${value}`),
                }))}
                defaultValue={filters.status ?? ""}
              />
              <div className="flex gap-2">
                <Button type="submit">{tc("search")}</Button>
              </div>
            </div>
            {hasFilters ? (
              <div>
                <Button variant="ghost" size="sm" onClick={onClear}>
                  {t("clear")}
                </Button>
              </div>
            ) : null}
          </form>
        </Card>
      </div>

      {rows === null ? null : (
        <section aria-labelledby="student-results" className="space-y-3">
          <h2 id="student-results" className="text-lg font-semibold">
            {t("resultsTitle")}
          </h2>
          <DataTable
            caption={t("resultsTitle")}
            captionHidden
            columns={columns}
            state={rows}
            rowKey={(row) => row.id}
            emptyTitle={hasFilters ? t("noMatchTitle") : t("emptyTitle")}
            emptyBody={hasFilters ? t("noMatchBody") : t("emptyBody")}
          />
          {results?.status === "ready" ? (
            <Pager
              label={t("pagesLabel")}
              page={pageNumber}
              onPrevious={onPrevious}
              onNext={onNext}
            />
          ) : null}
        </section>
      )}
    </div>
  );
}

/** GET /students with the filters held in memory, cursor paging (Next / Previous). */
export function StudentsScreen() {
  const api = useBffClient("staff");
  const permissions = useStaffPermissions();
  const structure = useSchoolStructure();
  const [filters, setFilters] = useState<StudentFilters>({});
  // Clearing remounts the search form so every field starts empty again.
  const [formKey, setFormKey] = useState(0);
  const clean = cleanFilters(filters);
  const pages = useCursorStack();
  const cursor = pages.cursor;
  const aadhaarBlocked = Boolean(clean.query && containsFullAadhaar(clean.query));
  const query = {
    ...clean,
    limit: PAGE_SIZE,
    ...(cursor ? { cursor } : {}),
  };
  const results = useApiQuery(
    ["staff", "students", "search", query],
    () => unwrap(api.GET("/api/v1/students", { params: { query } })),
    { enabled: !aadhaarBlocked },
  );
  const next = results.status === "ready" ? results.data.next_cursor : null;
  const search = (value: StudentFilters) => {
    pages.reset();
    setFilters(value);
  };
  return (
    <StudentListView
      key={formKey}
      filters={filters}
      results={aadhaarBlocked ? null : results}
      structure={structure}
      permissions={permissions}
      pageNumber={pages.page}
      aadhaarBlocked={aadhaarBlocked}
      onNext={next ? () => pages.next(next) : undefined}
      onPrevious={pages.hasPrevious ? pages.previous : undefined}
      onSearch={search}
      onClear={() => {
        search({});
        setFormKey((value) => value + 1);
        // Keyboard users land back in the search box, not at the top of the page.
        requestAnimationFrame(() => document.getElementById(SEARCH_FIELD_ID)?.focus());
      }}
    />
  );
}

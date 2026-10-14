"use client";

import type { AcademicYear, SchoolClass, Section } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { z } from "zod";
import { Avatar } from "@/components/ui/Avatar";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField, type SelectOption } from "@/components/ui/Select";
import { NarrowSwitch } from "@/components/ui/NarrowSwitch";
import {
  DataTable,
  StackedRows,
  Table,
  TableScroll,
  TBody,
  THead,
  Td,
  Th,
  Tr,
  type Column,
} from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { classLabel as classDisplay } from "@/lib/school-class";
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
  /** Another academic year's class lists (the current year when unset). */
  yearId?: string | undefined;
  /** Exact APAAR ID as typed (FR-STU-016); sent only when it is 12 digits. */
  apaar?: string | undefined;
  /** Exact UDISE+ PEN as typed (FR-STU-019); sent only when it is 11 digits. */
  pen?: string | undefined;
}

interface CleanFilters {
  query?: string;
  class_id?: string;
  section_id?: string;
  status?: StudentStatus;
  academic_year_id?: string;
  apaar_id?: string;
  udise_pen?: string;
}

const filtersSchema = z.object({
  q: z.string().optional(),
  class_id: z.string().optional(),
  section_id: z.string().optional(),
  status: z.string().optional(),
  academic_year_id: z.string().optional(),
  apaar_id: z.string().optional(),
  udise_pen: z.string().optional(),
});

/** FR-STU-017: the 11 digits of a typed PEN (spaces and hyphens removed), or null. */
export function penDigits(text: string): string | null {
  const value = text.trim().replace(/[ -]/g, "");
  return /^\d{11}$/.test(value) ? value : null;
}

/** FR-STU-015/016: 12 digits, optionally grouped 4-4-4 by a space or hyphen (as the API). */
const DIGITS12 = /^\d{4}[ -]?\d{4}[ -]?\d{4}$/;

/** The 12 digits of a typed APAAR ID, or null when it is not one. */
export function apaarDigits(text: string): string | null {
  const value = text.trim();
  return DIGITS12.test(value) ? value.replace(/[ -]/g, "") : null;
}

/** Search form values → filters (the zod schema only shapes them; cleanFilters checks them). */
export function filtersFromForm(form: HTMLFormElement): StudentFilters {
  const parsed = filtersSchema.parse(formValues(form));
  return {
    q: parsed.q?.trim() || undefined,
    classId: parsed.class_id || undefined,
    sectionId: parsed.section_id || undefined,
    status: parsed.status || undefined,
    yearId: parsed.academic_year_id || undefined,
    apaar: parsed.apaar_id?.trim() || undefined,
    pen: parsed.udise_pen?.trim() || undefined,
  };
}

/** Filters → search body (ignores anything malformed rather than failing the page). */
export function cleanFilters(filters: StudentFilters): CleanFilters {
  const q = filters.q?.trim().slice(0, 200) ?? "";
  const apaar = filters.apaar ? apaarDigits(filters.apaar) : null;
  const pen = filters.pen ? penDigits(filters.pen) : null;
  return {
    ...(q ? { query: q } : {}),
    ...(apaar ? { apaar_id: apaar } : {}),
    ...(pen ? { udise_pen: pen } : {}),
    ...(filters.classId && UUID_PATTERN.test(filters.classId) ? { class_id: filters.classId } : {}),
    ...(filters.sectionId && UUID_PATTERN.test(filters.sectionId)
      ? { section_id: filters.sectionId }
      : {}),
    ...(filters.status && isStudentStatus(filters.status) ? { status: filters.status } : {}),
    ...(filters.yearId && UUID_PATTERN.test(filters.yearId)
      ? { academic_year_id: filters.yearId }
      : {}),
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

/**
 * "Class 9 · A" options for the current academic year (or `yearId`), optionally for one class.
 * Archived sections, and sections of archived classes, are never offered (FR-TEN-010: the API
 * refuses them with 409 `structure_archived`).
 */
export function useSectionOptions(
  structure: StructureData,
  classId?: string,
  yearId?: string,
): readonly SelectOption[] {
  const locale = useLocale();
  if (structure.sections.status !== "ready" || structure.classes.status !== "ready") return [];
  const year =
    yearId ??
    (structure.years.status === "ready"
      ? structure.years.data.find((row) => row.is_current)?.id
      : undefined);
  const classes = new Map(structure.classes.data.map((row) => [row.id, row] as const));
  return structure.sections.data
    .filter((section) => !year || section.academic_year_id === year)
    .filter((section) => !classId || section.class_id === classId)
    .filter((section) => {
      const parent = classes.get(section.class_id);
      return !section.archived_at && parent !== undefined && !parent.archived_at;
    })
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
  /** The APAAR ID was not 12 digits: it was not sent. */
  apaarInvalid?: boolean;
  /** The PEN was not 11 digits: it was not sent. */
  penInvalid?: boolean;
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
  apaarInvalid = false,
  penInvalid = false,
  onSearch,
  onClear,
}: StudentListViewProps) {
  const t = useTranslations("students.list");
  const ts = useTranslations("students");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [classId, setClassId] = useState(filters.classId ?? "");
  const [yearId, setYearId] = useState(filters.yearId ?? "");
  const attributes = useAttributes(results !== null);
  const index = useAttributeIndex(attributes);

  const classOptions: SelectOption[] =
    structure.classes.status === "ready"
      ? [...structure.classes.data]
          .sort((a, b) => a.sort_order - b.sort_order)
          .map((row) => ({ value: row.id, label: classDisplay(row, locale) }))
      : [];
  const sectionOptions = useSectionOptions(structure, classId || undefined, yearId || undefined);
  const yearOptions: SelectOption[] =
    structure.years.status === "ready"
      ? structure.years.data
          .filter((row) => !row.is_current)
          .map((row) => ({ value: row.id, label: row.label }))
      : [];

  const rows: Loadable<readonly StudentSummary[]> | null =
    results === null
      ? null
      : results.status === "ready"
        ? { status: "ready", data: results.data.data }
        : results;
  const hasFilters = Boolean(
    filters.q ||
    filters.classId ||
    filters.sectionId ||
    filters.status ||
    filters.yearId ||
    filters.apaar ||
    filters.pen,
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSearch(filtersFromForm(event.currentTarget));
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("crumbHome"), href: "/" }, { label: t("title") }]}
        actions={
          <>
            {permissions.has(PERM.create) ? (
              <ButtonLink href="/students/new">
                <Icon name="plus" className="size-4" />
                {t("add")}
              </ButtonLink>
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
            <div
              className={
                yearOptions.length > 0
                  ? "grid items-end gap-4 md:grid-cols-2 xl:grid-cols-[2fr_1fr_1fr_1fr]"
                  : "grid items-end gap-4 md:grid-cols-2 xl:grid-cols-[2fr_1fr_1fr]"
              }
            >
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
              {yearOptions.length > 0 ? (
                <SelectField
                  name="academic_year_id"
                  label={t("filterYear")}
                  placeholder={t("currentYear")}
                  options={yearOptions}
                  value={yearId}
                  onChange={(event) => setYearId(event.currentTarget.value)}
                />
              ) : null}
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
                key={`section-${yearId}-${classId}`}
              />
            </div>
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="flex flex-wrap items-end gap-4">
                <SegmentedControl
                  name="status"
                  legend={t("filterStatus")}
                  legendVisible
                  size="sm"
                  defaultValue={filters.status ?? ""}
                  options={[
                    { value: "", label: tc("all") },
                    ...STUDENT_STATUSES.map((value) => ({
                      value,
                      label: ts(`status.${value}`),
                    })),
                  ]}
                />
                {/*
                  FR-STU-016 / ADR-0037: the one search field where 12 digits are expected, so
                  no Aadhaar paste guard here; it matches only the typed APAAR ID, exactly.
                */}
                <div className="w-full sm:w-60">
                  <TextField
                    name="apaar_id"
                    label={t("apaarLabel")}
                    hint={t("apaarHint")}
                    error={apaarInvalid ? t("apaarInvalid") : undefined}
                    defaultValue={filters.apaar ?? ""}
                    maxLength={14}
                    inputMode="numeric"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
                {/* FR-STU-019 / ADR-0039: exact UDISE+ PEN, 11 digits. */}
                <div className="w-full sm:w-52">
                  <TextField
                    name="udise_pen"
                    label={t("penLabel")}
                    hint={t("penHint")}
                    error={penInvalid ? t("penInvalid") : undefined}
                    defaultValue={filters.pen ?? ""}
                    maxLength={15}
                    inputMode="numeric"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {hasFilters ? (
                  <Button variant="ghost" onClick={onClear}>
                    {t("clear")}
                  </Button>
                ) : null}
                <Button type="submit">
                  <Icon name="search" className="size-4" />
                  {tc("search")}
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>

      {rows === null ? null : (
        <section aria-labelledby="student-results" className="space-y-3">
          <h2 id="student-results" className="text-lg font-semibold text-ink">
            {t("resultsTitle")}
          </h2>
          {rows.status === "ready" && rows.data.length > 0 ? (
            <StudentRows rows={rows.data} matchedOn={(row) => matchedField(row, index.label)} />
          ) : (
            <DataTable
              caption={t("resultsTitle")}
              captionHidden
              columns={[]}
              state={rows}
              rowKey={(row) => row.id}
              emptyTitle={hasFilters ? t("noMatchTitle") : t("emptyTitle")}
              emptyBody={hasFilters ? t("noMatchBody") : t("emptyBody")}
            />
          )}
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

/** "Found by: Father's name" when the match was not on the name or admission number. */
function matchedField(row: StudentSummary, label: (key: string) => string): string | null {
  const field = row.match.field;
  if (!field || ["full_name", "admission_no", "apaar_id", "udise_pen"].includes(field)) {
    return null;
  }
  return label(field);
}

/**
 * The results table. The name is the row's one link; it stretches over the whole row, so a
 * click anywhere on the row opens the profile while keyboard users still Tab to one link
 * per student (no row-level tabindex or click handlers).
 */
function StudentRows({
  rows,
  matchedOn,
}: {
  rows: readonly StudentSummary[];
  matchedOn: (row: StudentSummary) => string | null;
}) {
  const t = useTranslations("students.list");
  // Below 640px the same rows as cards: the name stretches over its card (docs/17 §5.7).
  const columns: Column<StudentSummary>[] = [
    {
      key: "name",
      header: t("colName"),
      cell: (row) => {
        const found = matchedOn(row);
        return (
          <div className="flex items-center gap-3">
            {row.display_name ? <Avatar name={row.display_name} size="sm" decorative /> : null}
            <div className="min-w-0">
              <Link
                href={`/students/${row.id}`}
                className="font-semibold break-anywhere text-primary underline-offset-4 after:absolute after:inset-0 hover:underline"
              >
                {row.display_name ?? t("unnamed")}
              </Link>
              {found ? (
                <p className="text-xs text-ink-muted" data-print="hide">
                  {t("matchedOn", { field: found })}
                </p>
              ) : null}
            </div>
          </div>
        );
      },
    },
    {
      key: "admission",
      header: t("colAdmissionNo"),
      cell: (row) => (
        <span className="font-mono text-sm">
          <Value>{row.admission_no}</Value>
        </span>
      ),
    },
    {
      key: "class",
      header: t("colClassSection"),
      cell: (row) =>
        row.class_section ? <Pill variant="tag">{row.class_section}</Pill> : <Value>{null}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <StudentStatusBadge status={row.status} />,
    },
  ];
  return (
    <NarrowSwitch
      narrow={
        <StackedRows
          caption={t("resultsTitle")}
          captionHidden
          columns={columns}
          rows={rows}
          rowKey={(row) => row.id}
        />
      }
      wide={<StudentTable rows={rows} matchedOn={matchedOn} />}
    />
  );
}

function StudentTable({
  rows,
  matchedOn,
}: {
  rows: readonly StudentSummary[];
  matchedOn: (row: StudentSummary) => string | null;
}) {
  const t = useTranslations("students.list");
  const tc = useTranslations("common");
  return (
    <TableScroll label={tc("scrollableTable", { caption: t("resultsTitle") })} framed>
      <Table>
        <caption className="sr-only">{t("resultsTitle")}</caption>
        <THead>
          <Tr>
            <Th>{t("colName")}</Th>
            <Th>{t("colAdmissionNo")}</Th>
            <Th>{t("colClassSection")}</Th>
            <Th>{t("colStatus")}</Th>
          </Tr>
        </THead>
        <TBody>
          {rows.map((row) => {
            const found = matchedOn(row);
            return (
              <Tr key={row.id} className="relative">
                <Td>
                  <div className="flex min-w-44 items-center gap-3">
                    {row.display_name ? (
                      <Avatar name={row.display_name} size="sm" decorative />
                    ) : null}
                    <div className="min-w-0">
                      <Link
                        href={`/students/${row.id}`}
                        className="font-semibold text-primary underline-offset-4 after:absolute after:inset-0 hover:underline"
                      >
                        {row.display_name ?? t("unnamed")}
                      </Link>
                      {found ? (
                        <p className="text-xs text-ink-muted" data-print="hide">
                          {t("matchedOn", { field: found })}
                        </p>
                      ) : null}
                    </div>
                  </div>
                </Td>
                <Td>
                  <span className="block max-w-56 font-mono text-sm break-all">
                    <Value>{row.admission_no}</Value>
                  </span>
                </Td>
                <Td>
                  {row.class_section ? (
                    <Pill variant="tag">{row.class_section}</Pill>
                  ) : (
                    <Value>{null}</Value>
                  )}
                </Td>
                <Td>
                  <StudentStatusBadge status={row.status} />
                </Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
    </TableScroll>
  );
}

/**
 * POST /students/search with the filters held in memory, cursor paging (Next / Previous).
 * Names and admission numbers go in the JSON body, never in a URL: URLs end up in proxy and
 * load-balancer access logs (SEC-008).
 */
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
  const apaarInvalid = Boolean(filters.apaar && !clean.apaar_id);
  const penInvalid = Boolean(filters.pen && !clean.udise_pen);
  const blocked = aadhaarBlocked || apaarInvalid || penInvalid;
  const body = {
    ...clean,
    limit: PAGE_SIZE,
    ...(cursor ? { cursor } : {}),
  };
  const results = useApiQuery(
    ["staff", "students", "search", body],
    () => unwrap(api.POST("/api/v1/students/search", { body })),
    { enabled: !blocked },
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
      results={blocked ? null : results}
      structure={structure}
      permissions={permissions}
      pageNumber={pages.page}
      aadhaarBlocked={aadhaarBlocked}
      apaarInvalid={apaarInvalid}
      penInvalid={penInvalid}
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
